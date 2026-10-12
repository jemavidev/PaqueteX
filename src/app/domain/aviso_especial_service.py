# -*- coding: utf-8 -*-
"""
Avisos especiales (`.scratch/avisos-especiales`): avisarles a mano a los clientes con algo pendiente que Papyrus no
presta el servicio de paquetes ciertos días (fines de semana, festivos, cierres).

Este módulo concentra las reglas para que las rutas solo lo llamen:

- los mensajes guardados (`MensajeEspecial`): crear, editar, eliminar (lógico), con historial;
- la resolución del texto para un cliente: un solo texto para SMS y WhatsApp, con `{nombre}` y `{codigos}` como
  únicas variables;
- quiénes aparecen en la vista: un cliente por fila, con sus Paquetes pendientes.
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .mensaje_especial import (
    MAX_NOMBRE_MENSAJE,
    MAX_TEXTO_MENSAJE,
    AccionMensajeEspecial,
    EnvioAvisoEspecial,
    MensajeEspecial,
    MensajeEspecialHistorial,
)
from .notification_sender import NotificationSender
from .notificacion_service import _sin_tildes, normalizar_texto_plantilla, resolver_destino_notificable
from .paquete import EstadoPaquete, Paquete, torre_sin_prefijo
from .persona import Persona
from .persona_service import buscar_persona_por_whatsapp, url_whatsapp, url_whatsapp_desktop
from .preferencia_notificacion import CanalNotificacion, PersonaPreferenciaNotificacion
from .registro_sms import TipoRegistroSms
from .registro_sms_service import registrar_envio
from .zona_horaria import ZONA_HORARIA_APP

logger = logging.getLogger(__name__)

VARIABLES_MENSAJE = ("nombre", "codigos")

# Un SMS en GSM-7 (sin tildes, ver `notificacion_service._sin_tildes`) admite 160 caracteres en un solo segmento.
MAX_CARACTERES_UN_SMS = 160

EJEMPLO_VARIABLES = {"nombre": "MARIA PEREZ", "codigos": "A1B2, C3D4"}


# --------------------------------------------------------------------------- #
# Texto del mensaje
# --------------------------------------------------------------------------- #
def validar_texto_mensaje(texto: str) -> str | None:
    """El mensaje de error para `texto` (ya normalizado), o `None` si se puede guardar."""
    if not texto:
        return "El texto no puede quedar vacío."
    if len(texto) > MAX_TEXTO_MENSAJE:
        return f"El mensaje tiene {len(texto)} caracteres; el máximo es {MAX_TEXTO_MENSAJE}."
    try:
        texto.format(**{nombre: "" for nombre in VARIABLES_MENSAJE})
    except KeyError as exc:
        return f"La variable {{{exc.args[0]}}} no existe. Usa: {{nombre}}, {{codigos}}."
    except (IndexError, ValueError):
        return "Hay una llave { o } suelta: las variables van completas, como {nombre}."
    return None


def resolver_texto(texto: str, nombre: str, codigos: list[str]) -> str:
    """El texto de `MensajeEspecial` con `{nombre}` y `{codigos}` (separados por coma) resueltos -- el que va por
    WhatsApp tal cual. Para el SMS, ver `texto_sms`. Tolerante: un texto que no se pueda resolver (no debería pasar,
    el guardado lo valida) se devuelve sin resolver en vez de reventar."""
    try:
        return texto.format(nombre=nombre or "", codigos=", ".join(codigos))
    except (KeyError, IndexError, ValueError):
        return texto


def texto_sms(texto_resuelto: str) -> str:
    """El texto que sale por SMS: sin tildes, mismo criterio que los avisos automáticos (un carácter fuera de GSM-7
    recorta el segmento de 160 a 70 caracteres)."""
    return _sin_tildes(texto_resuelto)


def vista_previa(texto: str) -> dict:
    """Vista previa con datos de ejemplo: el texto resuelto, su largo como SMS y si cabe en un solo SMS."""
    resuelto = resolver_texto(texto, EJEMPLO_VARIABLES["nombre"], EJEMPLO_VARIABLES["codigos"].split(", "))
    caracteres = len(texto_sms(resuelto))
    return {"texto": resuelto, "caracteres": caracteres, "un_sms": caracteres <= MAX_CARACTERES_UN_SMS}


# --------------------------------------------------------------------------- #
# Mensajes guardados
# --------------------------------------------------------------------------- #
def listar_mensajes(session: Session) -> list[MensajeEspecial]:
    """Los mensajes vigentes (no eliminados), por nombre."""
    return (
        session.query(MensajeEspecial)
        .filter(MensajeEspecial.eliminado_en.is_(None))
        .order_by(func.lower(MensajeEspecial.nombre))
        .all()
    )


def obtener_mensaje(session: Session, mensaje_id) -> MensajeEspecial | None:
    """El mensaje vigente con `mensaje_id`, o `None` si no existe o fue eliminado."""
    mensaje = session.get(MensajeEspecial, mensaje_id)
    if mensaje is None or mensaje.eliminado_en is not None:
        return None
    return mensaje


def _validar(session: Session, nombre: str, texto: str, excluir_id=None) -> tuple[str, str]:
    nombre_limpio = " ".join((nombre or "").split())
    if not nombre_limpio:
        raise ValueError("El nombre no puede quedar vacío.")
    if len(nombre_limpio) > MAX_NOMBRE_MENSAJE:
        raise ValueError(f"El nombre admite hasta {MAX_NOMBRE_MENSAJE} caracteres.")
    texto_limpio = normalizar_texto_plantilla(texto)
    error = validar_texto_mensaje(texto_limpio)
    if error:
        raise ValueError(error)
    duplicado = session.query(MensajeEspecial).filter(
        MensajeEspecial.eliminado_en.is_(None),
        func.lower(MensajeEspecial.nombre) == nombre_limpio.lower(),
    )
    if excluir_id is not None:
        duplicado = duplicado.filter(MensajeEspecial.id != excluir_id)
    if duplicado.first() is not None:
        raise ValueError(f'Ya existe un mensaje llamado "{nombre_limpio}".')
    return nombre_limpio, texto_limpio


def _flush_o_duplicado(session: Session, nombre: str) -> None:
    try:
        session.flush()
    except IntegrityError:
        # Carrera: otro ADMIN guardó el mismo nombre entre la validación y este flush.
        session.rollback()
        raise ValueError(f'Ya existe un mensaje llamado "{nombre}".')


def crear_mensaje(session: Session, nombre: str, texto: str, usuario_id=None) -> MensajeEspecial:
    """Crea un mensaje especial y deja su creación en el historial.

    Raises:
        ValueError: nombre vacío, largo o repetido entre los vigentes (sin distinguir mayúsculas); texto vacío,
            largo, o con una variable distinta de `{nombre}`/`{codigos}`.
    """
    nombre_limpio, texto_limpio = _validar(session, nombre, texto)
    mensaje = MensajeEspecial(nombre=nombre_limpio, texto=texto_limpio)
    session.add(mensaje)
    _flush_o_duplicado(session, nombre_limpio)
    session.add(
        MensajeEspecialHistorial(
            mensaje_id=mensaje.id,
            accion=AccionMensajeEspecial.CREADO,
            usuario_id=usuario_id,
            nombre_nuevo=nombre_limpio,
            texto_nuevo=texto_limpio,
        )
    )
    session.flush()
    return mensaje


def editar_mensaje(session: Session, mensaje_id, nombre: str, texto: str, usuario_id=None) -> MensajeEspecial:
    """Cambia el nombre y/o el texto de un mensaje vigente y deja el antes/después en el historial.

    Raises:
        ValueError: el mensaje no existe (o fue eliminado), o las mismas validaciones de `crear_mensaje`.
    """
    mensaje = obtener_mensaje(session, mensaje_id)
    if mensaje is None:
        raise ValueError("Mensaje no encontrado.")
    nombre_limpio, texto_limpio = _validar(session, nombre, texto, excluir_id=mensaje.id)
    nombre_anterior, texto_anterior = mensaje.nombre, mensaje.texto
    mensaje.nombre, mensaje.texto = nombre_limpio, texto_limpio
    _flush_o_duplicado(session, nombre_limpio)
    session.add(
        MensajeEspecialHistorial(
            mensaje_id=mensaje.id,
            accion=AccionMensajeEspecial.EDITADO,
            usuario_id=usuario_id,
            nombre_anterior=nombre_anterior,
            nombre_nuevo=nombre_limpio,
            texto_anterior=texto_anterior,
            texto_nuevo=texto_limpio,
        )
    )
    session.flush()
    return mensaje


def eliminar_mensaje(session: Session, mensaje_id, usuario_id=None) -> None:
    """Elimina un mensaje vigente: eliminación LÓGICA (`eliminado_en`), para que los envíos ya hechos conserven su
    nombre y que el nombre pueda volver a usarse. Queda en el historial.

    Raises:
        ValueError: el mensaje no existe o ya fue eliminado.
    """
    mensaje = obtener_mensaje(session, mensaje_id)
    if mensaje is None:
        raise ValueError("Mensaje no encontrado.")
    mensaje.eliminado_en = datetime.now(timezone.utc)
    session.add(
        MensajeEspecialHistorial(
            mensaje_id=mensaje.id,
            accion=AccionMensajeEspecial.ELIMINADO,
            usuario_id=usuario_id,
            nombre_anterior=mensaje.nombre,
            texto_anterior=mensaje.texto,
        )
    )
    session.flush()


# --------------------------------------------------------------------------- #
# Quiénes aparecen en la vista
# --------------------------------------------------------------------------- #
# Un Paquete ANUNCIADO cuenta si se anunció hoy o en los 4 días calendario anteriores (hora de Colombia).
DIAS_ANUNCIADO = 5


@dataclass
class ClienteAviso:
    """Una fila de la vista: la Persona que recibiría hoy los avisos automáticos de estos Paquetes, con sus
    Paquetes pendientes (ANUNCIADOS recientes y RECIBIDOS)."""

    persona: Persona
    paquetes: list[Paquete] = field(default_factory=list)
    # El último SMS de aviso especial que salió hacia esta Persona (cualquier mensaje), o `None`.
    ultimo_envio: EnvioAvisoEspecial | None = None
    # Por qué no se le puede enviar SMS ("De baja", "Sin teléfono", "Notificaciones SMS desactivadas"), o `None`.
    motivo_sin_sms: str | None = None

    @property
    def codigos(self) -> list[str]:
        return [p.access_code for p in self.paquetes]

    @property
    def estado_principal(self) -> str:
        """El Estado que pinta la franja de la fila: RECIBIDO si tiene alguno (lo que más urge retirar), si no
        ANUNCIADO."""
        recibido = any(p.estado is EstadoPaquete.RECIBIDO for p in self.paquetes)
        return EstadoPaquete.RECIBIDO.value if recibido else EstadoPaquete.ANUNCIADO.value

    @property
    def direccion(self) -> str | None:
        """"Torre 2 · Apt 706" del primer Paquete que tenga apartamento (mismo formato que `/paquetes`)."""
        for p in self.paquetes:
            if p.snapshot_apartamento:
                torre = torre_sin_prefijo(p.snapshot_torre)
                return f"Torre {torre} · Apt {p.snapshot_apartamento}" if torre else f"Apt {p.snapshot_apartamento}"
        return None


def inicio_ventana_anunciados(hoy: date) -> datetime:
    """El instante (UTC) desde el que un ANUNCIADO cuenta: medianoche, hora de Colombia, de hace 4 días."""
    primer_dia = hoy - timedelta(days=DIAS_ANUNCIADO - 1)
    return datetime.combine(primer_dia, time.min, tzinfo=ZONA_HORARIA_APP).astimezone(timezone.utc)


def hoy_local() -> date:
    return datetime.now(ZONA_HORARIA_APP).date()


def listar_clientes(session: Session, hoy: date | None = None) -> list[ClienteAviso]:
    """Un `ClienteAviso` por Persona, ordenados por nombre. Entran los Paquetes RECIBIDOS (cualquier fecha) y los
    ANUNCIADOS desde `inicio_ventana_anunciados(hoy)`; nunca ENTREGADOS ni CANCELADOS. El cliente de cada Paquete es
    el mismo destino de sus avisos automáticos (`resolver_destino_notificable`): un Paquete sin nadie alcanzable no
    aparece."""
    hoy = hoy or hoy_local()
    paquetes = (
        session.query(Paquete)
        .filter(
            (Paquete.estado == EstadoPaquete.RECIBIDO)
            | (
                (Paquete.estado == EstadoPaquete.ANUNCIADO)
                & (Paquete.announced_at >= inicio_ventana_anunciados(hoy))
            )
        )
        .order_by(Paquete.announced_at)
        .all()
    )
    por_persona: dict = {}
    for paquete in paquetes:
        persona = cliente_del_paquete(session, paquete)
        if persona is None:
            continue
        por_persona.setdefault(persona.id, ClienteAviso(persona)).paquetes.append(paquete)
    ultimos = ultimos_envios(session, list(por_persona))
    for persona_id, cliente in por_persona.items():
        cliente.ultimo_envio = ultimos.get(persona_id)
        cliente.motivo_sin_sms = motivo_sin_sms(session, cliente)
    return sorted(por_persona.values(), key=lambda c: (c.persona.nombre or "").lower())


def cliente_del_paquete(session: Session, paquete: Paquete) -> Persona | None:
    """La Persona de la fila de `paquete`. Primero, la misma de sus avisos automáticos (`resolver_destino_notificable`:
    el Destinatario alcanzable, o si no, el Anunciante vivo y con teléfono). Si esa resolución no da a nadie (de baja,
    o solo con usuario de WhatsApp), la fila igual aparece -- con el SMS apagado y su motivo (`motivo_sin_sms`) -- a
    nombre del Destinatario (por teléfono o usuario de WhatsApp) o, si no, del Anunciante con algún contacto, mientras
    no esté anonimizado: nunca una fila sin a quién escribirle. No cambia nada de los avisos automáticos."""
    persona = resolver_destino_notificable(session, paquete)
    if persona is not None:
        return persona
    destinatario = None
    if paquete.recipient_phone:
        destinatario = session.query(Persona).filter(Persona.telefono == paquete.recipient_phone).one_or_none()
    elif paquete.recipient_whatsapp:
        destinatario = buscar_persona_por_whatsapp(session, paquete.recipient_whatsapp)
    if destinatario is not None and destinatario.eliminado_en is None:
        return destinatario
    anunciante = session.get(Persona, paquete.announced_by_persona_id)
    if (
        anunciante is not None
        and anunciante.eliminado_en is None
        and (anunciante.telefono or anunciante.whatsapp_usuario)
    ):
        return anunciante
    return None


def motivo_sin_sms(session: Session, cliente: ClienteAviso) -> str | None:
    """Por qué NO se le puede enviar SMS a `cliente`, o `None` si se puede. En orden de prioridad: de baja
    administrativa; sin teléfono; el cliente APAGÓ A PROPÓSITO el SMS (una preferencia guardada en falso) para el
    Estado de TODOS sus Paquetes en la lista. El bloqueo no agrega regla, igual que en los avisos automáticos.

    Los valores por defecto NO bloquean (decisión de Jesús, 2026-10-11): por costo, el SMS de Recibido viene apagado
    por defecto (`preferencia_notificacion_service._default_activo`), así que con `preferencia_activa` casi ningún
    cliente con solo Recibidos podría recibir el aviso. El aviso especial no es un evento de Paquete: lo que se
    respeta es lo que el cliente eligió."""
    persona = cliente.persona
    if persona.baja_administrativa_en is not None:
        return "De baja"
    if not persona.telefono:
        return "Sin teléfono"
    estados = {p.estado.value for p in cliente.paquetes}
    apagados = {
        evento
        for (evento,) in session.query(PersonaPreferenciaNotificacion.evento).filter(
            PersonaPreferenciaNotificacion.persona_id == persona.id,
            PersonaPreferenciaNotificacion.canal == CanalNotificacion.SMS.value,
            PersonaPreferenciaNotificacion.activo.is_(False),
        )
    }
    if estados and estados <= apagados:
        return "Notificaciones SMS desactivadas"
    return None


def _normalizado(texto: str | None) -> str:
    return " ".join(_sin_tildes(texto or "").lower().split())


def filtrar_clientes(clientes: list[ClienteAviso], q: str = "", estado: str = "") -> list[ClienteAviso]:
    """La búsqueda y el filtro de la vista (ticket 07). `q` coincide, sin tildes ni mayúsculas, con el nombre, el
    usuario de WhatsApp, un código de acceso o la Torre/Apartamento; sus dígitos, con el teléfono. `estado`
    (ANUNCIADO/RECIBIDO) deja a quien tenga al menos un Paquete en ese Estado: un cliente con los dos sale en ambos."""
    termino = _normalizado(q)
    digitos = "".join(ch for ch in termino if ch.isdigit())
    resultado = []
    for c in clientes:
        if estado and not any(p.estado.value == estado for p in c.paquetes):
            continue
        if termino:
            textos = [c.persona.nombre, c.persona.whatsapp_usuario, c.direccion, *c.codigos]
            telefono = "".join(ch for ch in (c.persona.telefono or "") if ch.isdigit())
            if not (
                any(termino in _normalizado(t) for t in textos)
                or (digitos and digitos == termino.replace(" ", "") and digitos in telefono)
            ):
                continue
        resultado.append(c)
    return resultado


def conteos_por_estado(clientes: list[ClienteAviso]) -> dict:
    """`{"": total, "ANUNCIADO": n, "RECIBIDO": n}` -- cuántos clientes muestra cada opción del filtro."""
    return {
        "": len(clientes),
        **{
            e.value: sum(1 for c in clientes if any(p.estado is e for p in c.paquetes))
            for e in (EstadoPaquete.ANUNCIADO, EstadoPaquete.RECIBIDO)
        },
    }


def ultimos_envios(session: Session, persona_ids: list) -> dict:
    """`{persona_id: EnvioAvisoEspecial}` con el envío más reciente de cada Persona (las que no tienen, no están)."""
    if not persona_ids:
        return {}
    envios = (
        session.query(EnvioAvisoEspecial)
        .filter(EnvioAvisoEspecial.persona_id.in_(persona_ids))
        .order_by(EnvioAvisoEspecial.persona_id, EnvioAvisoEspecial.creado_en.desc())
        .distinct(EnvioAvisoEspecial.persona_id)
        .all()
    )
    return {e.persona_id: e for e in envios}


def texto_para(cliente: ClienteAviso, mensaje: MensajeEspecial) -> str:
    """El texto de `mensaje` resuelto para `cliente`: su nombre y los códigos de sus Paquetes en la lista."""
    return resolver_texto(mensaje.texto, cliente.persona.nombre, cliente.codigos)


def enlaces_whatsapp(cliente: ClienteAviso, mensaje: MensajeEspecial) -> tuple[str, str] | None:
    """`(celular, escritorio)` para abrir el chat de `cliente` con `mensaje` ya escrito -- los mismos enlaces del
    resto de la app (`url_whatsapp`/`url_whatsapp_desktop`). `None` si no tiene teléfono ni usuario de WhatsApp.
    El WhatsApp no deja registro: la app no puede saber si de verdad se envió."""
    persona = cliente.persona
    if not (persona.telefono or persona.whatsapp_usuario):
        return None
    texto = texto_para(cliente, mensaje)
    return url_whatsapp(persona, texto=texto), url_whatsapp_desktop(persona, texto=texto)


def elegir_mensaje(session: Session, mensaje_id) -> MensajeEspecial | None:
    """El mensaje elegido en el selector: `mensaje_id` (un UUID, o `None`) si es un mensaje vigente, si no el primero
    por nombre, o `None` si no hay ninguno."""
    if mensaje_id is not None:
        elegido = obtener_mensaje(session, mensaje_id)
        if elegido is not None:
            return elegido
    mensajes = listar_mensajes(session)
    return mensajes[0] if mensajes else None


# --------------------------------------------------------------------------- #
# Enviar por SMS
# --------------------------------------------------------------------------- #
class AvisoNoEnviable(ValueError):
    """El SMS no se puede intentar: el mensaje ya no existe o el cliente ya no está en la lista."""


@dataclass
class ResultadoEnvio:
    cliente: ClienteAviso
    enviado: bool


def buscar_cliente(session: Session, persona_id, hoy: date | None = None) -> ClienteAviso | None:
    """La fila de `persona_id` en la lista de hoy, o `None` si ya no tiene Paquetes pendientes."""
    return next((c for c in listar_clientes(session, hoy) if c.persona.id == persona_id), None)


def enviar_sms(
    session: Session,
    persona_id,
    mensaje_id,
    sender: NotificationSender,
    usuario_id=None,
    hoy: date | None = None,
) -> ResultadoEnvio:
    """Envía `mensaje_id` por SMS al cliente `persona_id`, ahora (síncrono: quien llama espera la respuesta real del
    proveedor, porque necesita saber si llegó). Solo si el cliente sigue en la lista de hoy.

    - Sale: queda un `EnvioAvisoEspecial` (el nuevo "Último SMS") y, si hubo un proveedor real, un `RegistroSms`
      `AVISO_ESPECIAL` exitoso.
    - Falla (`sender.enviar` lanza): un `RegistroSms` fallido y NINGÚN `EnvioAvisoEspecial` -- la fecha del último SMS
      siempre es de uno que sí salió.

    Sin confirmación ni bloqueo por envíos recientes: el "Último SMS" es solo informativo.

    Raises:
        AvisoNoEnviable: el mensaje no existe o fue eliminado, el cliente ya no tiene Paquetes pendientes, o no se
            le puede enviar SMS (`motivo_sin_sms`).
    """
    mensaje = obtener_mensaje(session, mensaje_id)
    if mensaje is None:
        raise AvisoNoEnviable("Ese mensaje ya no existe. Elige otro.")
    cliente = buscar_cliente(session, persona_id, hoy)
    if cliente is None:
        raise AvisoNoEnviable("Ese cliente ya no tiene paquetes pendientes.")
    if cliente.motivo_sin_sms:
        raise AvisoNoEnviable(f"No se le puede enviar SMS: {cliente.motivo_sin_sms}.")

    texto = texto_sms(texto_para(cliente, mensaje))
    try:
        proveedor = sender.enviar(cliente.persona.telefono, texto)
    except Exception:
        # Proveedor caído o rechazo -- y también un error de programación: se registra como fallido sin tumbar la
        # vista, pero queda en el log para no confundir un bug con una caída del proveedor.
        logger.exception("No se pudo enviar el SMS de aviso especial a la persona %s", cliente.persona.id)
        registrar_envio(session, TipoRegistroSms.AVISO_ESPECIAL, exitoso=False)
        return ResultadoEnvio(cliente, enviado=False)

    envio = EnvioAvisoEspecial(
        persona_id=cliente.persona.id, mensaje_id=mensaje.id, nombre_mensaje=mensaje.nombre, usuario_id=usuario_id
    )
    session.add(envio)
    session.flush()
    if proveedor is not None:
        registrar_envio(session, TipoRegistroSms.AVISO_ESPECIAL, exitoso=True, proveedor=proveedor)
    cliente.ultimo_envio = envio
    return ResultadoEnvio(cliente, enviado=True)
