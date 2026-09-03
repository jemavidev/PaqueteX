# -*- coding: utf-8 -*-
"""
Servicio de dominio de registro de Persona (Seam A).

Concentra el invariante "el Teléfono es la llave universal": normaliza el
teléfono ANTES de persistir y garantiza que un mismo número —en cualquier
formato— resuelva a UNA sola Persona (registro implícito, sin duplicados).
"""

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .ocupante import Ocupante
from .persona import Persona
from .telefono import normalizar_telefono
from .texto import normalizar_nombre

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
# Reglas publicadas por Meta para el username de WhatsApp (rollout 2026,
# .scratch/pendientes-cliente/issues/67): 3-35 caracteres, letras latinas,
# números, puntos o guion bajo.
WHATSAPP_USUARIO_RE = re.compile(r"^[A-Za-z0-9._]{3,35}$")
_ANONIMIZADO_PREFIJO = "DEL-"  # nunca colisiona con un teléfono real (+57…)
_NOMBRE_ANONIMIZADO = "Cliente eliminado"


def _normalizar_whatsapp_usuario(whatsapp_usuario: str) -> str:
    """Forma canónica de un usuario de WhatsApp: sin espacios, sin `@`
    inicial, todo en minúscula -- Meta identifica usuarios de WhatsApp sin
    distinguir mayúsculas de minúsculas (issue 162, .scratch/pendientes-
    cliente), así que `Jesus.Villalobos` y `jesus.villalobos` deben resolver
    a la MISMA Persona, igual que dos formatos del mismo Teléfono ya
    resuelven a una sola vía `normalizar_telefono`. Compartida por los 3
    puntos que leen o escriben este campo (`get_or_create_persona_por_
    whatsapp`, `buscar_persona_por_whatsapp`, `update_datos_personales`) --
    una sola fuente de verdad para la forma canónica."""
    return (whatsapp_usuario or "").strip().lstrip("@").lower()


def _buscar_por_telefono(session: Session, telefono_canonico: str):
    return (
        session.query(Persona)
        .filter(Persona.telefono == telefono_canonico)
        .one_or_none()
    )


def _buscar_por_whatsapp(session: Session, whatsapp_usuario: str):
    return (
        session.query(Persona)
        .filter(Persona.whatsapp_usuario == whatsapp_usuario)
        .one_or_none()
    )


def _validar_whatsapp_usuario(whatsapp_usuario: str) -> None:
    """Valida la forma de un usuario de WhatsApp ya normalizado (sin `@`
    inicial) -- reglas publicadas por Meta (rollout 2026, issue 67): 3-35
    caracteres, letras latinas, números, puntos o guion bajo. Compartida por
    `update_datos_personales` y `get_or_create_persona_por_whatsapp` para que
    la regla viva en un solo lugar.

    Raises:
        ValueError: si no cumple el formato.
    """
    if not WHATSAPP_USUARIO_RE.match(whatsapp_usuario):
        raise ValueError(
            f"El usuario de WhatsApp {whatsapp_usuario!r} no es válido -- usa "
            "entre 3 y 35 letras, números, puntos o guion bajo (sin el @)."
        )


def _obtener_o_crear_persona(session: Session, buscar, construir) -> Persona:
    """Reutiliza la Persona que `buscar()` encuentra, o la crea con
    `construir()` si no existe -- maneja la carrera real (dos transacciones
    creando la misma Persona a la vez con la misma llave) reintentando
    `buscar()` tras un `IntegrityError` en vez de dejarlo propagar. Patrón
    compartido por `get_or_create_persona` (llave: Teléfono) y
    `get_or_create_persona_por_whatsapp` (llave: `whatsapp_usuario`).

    Args:
        buscar: callable sin argumentos, devuelve la Persona existente o `None`.
        construir: callable sin argumentos, devuelve una Persona nueva sin guardar.
    """
    existente = buscar()
    if existente is not None:
        return existente

    persona = construir()
    session.add(persona)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        encontrada = buscar()
        if encontrada is None:
            raise
        return encontrada

    return persona


def get_or_create_persona(session: Session, telefono: str, nombre: str) -> Persona:
    """Reutiliza la Persona del teléfono dado, o la crea si no existe.

    Normaliza el teléfono a su forma canónica antes de buscar/persistir, de modo
    que dos formatos del mismo número resuelvan a la misma Persona.

    Args:
        session: sesión de SQLAlchemy activa.
        telefono: teléfono en cualquier formato.
        nombre: nombre del residente (solo se usa al CREAR; si la Persona ya
            existe no se sobreescribe su nombre).

    Returns:
        La Persona existente o recién creada.
    """
    telefono_canonico = normalizar_telefono(telefono)
    return _obtener_o_crear_persona(
        session,
        lambda: _buscar_por_telefono(session, telefono_canonico),
        lambda: Persona(telefono=telefono_canonico, nombre=normalizar_nombre(nombre)),
    )


def get_or_create_persona_por_whatsapp(
    session: Session, whatsapp_usuario: str, nombre: str
) -> Persona:
    """Reutiliza la Persona del usuario de WhatsApp dado, o la crea (sin
    Teléfono) si no existe -- simétrica a `get_or_create_persona`, pero para
    una Persona identificada solo por WhatsApp (ADR-0007,
    `.scratch/announce-rapido`, ticket 01).

    Normaliza el usuario (recorta `@` inicial, valida forma) antes de
    buscar/persistir, mismas reglas que `update_datos_personales`.

    Args:
        session: sesión de SQLAlchemy activa.
        whatsapp_usuario: usuario de WhatsApp, con o sin `@` inicial.
        nombre: nombre del residente (solo se usa al CREAR; si la Persona ya
            existe no se sobreescribe su nombre).

    Returns:
        La Persona existente o recién creada.

    Raises:
        ValueError: si `whatsapp_usuario` no cumple las reglas de username
            de WhatsApp.
    """
    # Mismo criterio que `update_datos_personales` (issue 68): el "@" es
    # puramente de presentación, se guarda SIEMPRE sin él -- y en minúscula
    # (issue 162), la forma en que Meta identifica al usuario.
    usuario_normalizado = _normalizar_whatsapp_usuario(whatsapp_usuario)
    _validar_whatsapp_usuario(usuario_normalizado)
    return _obtener_o_crear_persona(
        session,
        lambda: _buscar_por_whatsapp(session, usuario_normalizado),
        lambda: Persona(whatsapp_usuario=usuario_normalizado, nombre=normalizar_nombre(nombre)),
    )


def buscar_persona_por_telefono(session: Session, telefono: str) -> Persona | None:
    """Busca una Persona YA REGISTRADA por Teléfono (cualquier formato) --
    de SOLO LECTURA, nunca crea (a diferencia de `get_or_create_persona`).
    Usada por el campo único inteligente de `/announce` (ADR-0007,
    `.scratch/announce-rapido` ticket 04) para decidir, mientras el staff
    todavía está escribiendo, si el valor ya coincide con alguien conocido.

    Returns:
        La Persona encontrada, o `None` si no existe o si `telefono` no
        tiene forma válida (nunca lanza `ValueError` por esto último --
        mientras se escribe, un valor a medio terminar es un caso normal,
        no un error).
    """
    try:
        telefono_canonico = normalizar_telefono(telefono)
    except ValueError:
        return None
    return _buscar_por_telefono(session, telefono_canonico)


def buscar_persona_por_whatsapp(session: Session, whatsapp_usuario: str) -> Persona | None:
    """Busca una Persona YA REGISTRADA por usuario de WhatsApp (con o sin
    `@` inicial) -- de SOLO LECTURA, nunca crea. Misma motivación que
    `buscar_persona_por_telefono` (ticket 04).

    Returns:
        La Persona encontrada, o `None` si no existe o si `whatsapp_usuario`
        no tiene forma válida (nunca lanza `ValueError`, mismo criterio que
        la contraparte de Teléfono).
    """
    usuario_normalizado = _normalizar_whatsapp_usuario(whatsapp_usuario)
    try:
        _validar_whatsapp_usuario(usuario_normalizado)
    except ValueError:
        return None
    return _buscar_por_whatsapp(session, usuario_normalizado)


def update_datos_personales(
    session: Session,
    persona: Persona,
    *,
    nombre: str = None,
    email: str = None,
    whatsapp_usuario: str = None,
) -> Persona:
    """Actualiza PARCIALMENTE los datos ampliables de una Persona.

    Los argumentos en ``None`` NO tocan el valor existente — permite guardar
    parcialmente sin borrar lo que no se envió en esta llamada.

    `documento`/`tipo_documento` NO se aceptan aquí a propósito (Grupo 12,
    Ronda 2 de `ajustes-post-referencia-funcional`): el usuario pidió sacar
    ese dato de todo flujo del sistema. Las columnas siguen existiendo en
    `Persona` (dato histórico neutral, sin migración destructiva), pero
    ningún camino de código las escribe ya.

    `segundo_contacto` (issue 170, .scratch/pendientes-cliente) -- a
    diferencia de `documento`/`tipo_documento` arriba, este campo se
    eliminó por completo (columna incluida, migración 0031): nunca lo usó
    ningún flujo real (ni notificaciones, ni OTP), y no estaba expuesto al
    propio cliente en `/mis-datos` -- solo staff podía verlo/tocarlo.

    `whatsapp_usuario` (pedido del cliente, .scratch/pendientes-cliente):
    solo lo escribe `/residentes/{id}` (staff) hoy -- `/mis-datos` (el
    propio cliente) simplemente no pasa este argumento, así que queda
    intacto para ese caller sin necesitar ninguna rama nueva.

    `whatsapp_usuario` y `email` tienen semántica de 3 estados (issue 69
    para WhatsApp; issue 261, .scratch/pendientes-cliente, extiende el
    mismo contrato a `email` -- mismo síntoma reportado en vivo: dejarlo
    vacío y guardar no lo borraba): `None` = no tocar (mismo contrato que
    `nombre`); `""` (string vacío explícito, distinto de `None`) =
    BORRARLO a propósito -- los callers web siempre mandan estos campos
    en cada submit (nunca los omiten, son `<input>` normales, no
    checkboxes), así que "vacío" tiene que poder significar "bórralo", no
    "no lo toques" (si no, nunca sería posible vaciar el campo una vez
    tuviera un valor). Un valor no vacío se valida y se guarda normal.
    `nombre` se queda en 2 estados -- una Persona siempre necesita
    nombre, "bórralo" no aplica ahí.

    Valida la forma básica ANTES de mutar nada (atómico): si `email` o
    `whatsapp_usuario` vienen con forma inválida, lanza `ValueError` y la
    Persona queda intacta (ningún otro campo de esta llamada se aplica
    tampoco).

    Raises:
        ValueError: si `email` viene no vacío y no tiene forma de email, o
            si `whatsapp_usuario` viene (no vacío) y no cumple las reglas
            de username de WhatsApp (issue 67 -- ya no es texto libre:
            arma un link real).
    """
    if email is not None and email and not _EMAIL_RE.match(email):
        raise ValueError(f"El email {email!r} no tiene un formato válido.")
    if whatsapp_usuario is not None:
        # El "@" es puramente de presentación (issue 68) -- se guarda SIEMPRE
        # sin él, sin importar cuántos vengan al inicio (pegar un valor que ya
        # traía "@" no puede duplicarlo: `lstrip` los quita todos antes de
        # validar/guardar). La plantilla antepone un solo "@" al mostrarlo.
        # Minúscula (issue 162): Meta identifica al usuario sin distinguir
        # mayúsculas de minúsculas, mismo criterio en los 3 puntos que tocan
        # este campo (`_normalizar_whatsapp_usuario`).
        whatsapp_usuario = _normalizar_whatsapp_usuario(whatsapp_usuario)
        if whatsapp_usuario:
            _validar_whatsapp_usuario(whatsapp_usuario)

    if nombre is not None:
        nombre_normalizado = normalizar_nombre(nombre)
        persona.nombre = nombre_normalizado
        # Issue 189 (.scratch/pendientes-cliente, auditoría de coherencia):
        # `Ocupante.nombre` es su propia columna, congelada a propósito UNA
        # SOLA VEZ al crear (`agregar_ocupante`, ver su docstring) -- pero
        # nada la actualizaba después si el nombre de la Persona se corregía
        # acá (typo, cambio legal). Bug real: el picker de "Corregir
        # destinatario" (`candidatos_correccion`) y la búsqueda de
        # residentes seguían ofreciendo el nombre VIEJO -- corregir un
        # paquete a ese candidato dejaba `recipient_name` con un nombre que
        # ya no coincidía con la Persona real, reproduciendo el mismo
        # síntoma de "destinatario sin confirmar" (`_destinatario_sin_
        # confirmar`, packages.py) por una vía distinta. Solo Ocupantes
        # ACTIVOS (`desvinculado_en IS NULL`) -- los históricos se quedan
        # congelados tal cual eran en ese momento, mismo criterio que ya usa
        # el resto del código (issue 166).
        session.query(Ocupante).filter(
            Ocupante.persona_id == persona.id, Ocupante.desvinculado_en.is_(None)
        ).update({"nombre": nombre_normalizado}, synchronize_session=False)
    if email is not None:
        persona.email = email or None  # "" -> lo borra (NULL), issue 261
    if whatsapp_usuario is not None:
        persona.whatsapp_usuario = whatsapp_usuario or None  # "" -> lo borra (NULL)

    session.flush()
    return persona


def anonimizar_persona(session: Session, persona: Persona) -> Persona:
    """Anonimiza una Persona (ADR-0005): limpia sus datos personales y
    reemplaza su Teléfono por un valor sintético no reutilizable — sin borrar
    la fila (la FK real `fk_paquetes_anunciante` desde `paquetes` nunca se
    rompe). Idempotente: si ya estaba anonimizada, no hace nada.

    Desvincula del Apartamento asignando `apartamento_actual_id = None`
    directamente (no a través de `move_resident`, que la re-buscaría por
    teléfono de forma redundante ya que aquí se tiene la instancia en mano; la
    garantía de que esto no reescribe el snapshot de paquetes ya anunciados
    vive en el esquema — columnas de texto copiadas, no un FK — no en la
    función que se use para desvincular).
    """
    if persona.eliminado_en is not None:
        return persona

    persona.apartamento_actual_id = None
    persona.nombre = _NOMBRE_ANONIMIZADO
    persona.email = None
    persona.documento = None
    persona.tipo_documento = None
    persona.telefono = _ANONIMIZADO_PREFIJO + uuid.uuid4().hex[:16]
    persona.eliminado_en = datetime.now(timezone.utc)

    session.flush()
    return persona


def set_notificaciones_activas(session: Session, persona: Persona, activas: bool) -> Persona:
    """Activa o desactiva las notificaciones de evento (Recibido/Entregado/
    Cancelado) de una Persona. NUNCA afecta el envío del OTP — es el mecanismo
    de login, no una notificación opcional."""
    persona.notificaciones_activas = activas
    session.flush()
    return persona


def cambiar_telefono_propio(session: Session, persona: Persona, nuevo_telefono: str) -> Persona:
    """Cambia el teléfono de `persona` a `nuevo_telefono` -- edición del
    PROPIO número desde `/mis-datos` (pedido del cliente,
    `.scratch/pendientes-cliente/issues/35`). A diferencia de re-ligar un
    Ocupante gestionado a otra Persona, esto RENOMBRA la fila existente
    (mismo id, mismo historial de paquetes vía snapshot) -- no crea ni
    reutiliza otra Persona.

    El caller es responsable de cerrar la sesión tras un cambio exitoso y
    exigir una nueva verificación OTP al número nuevo -- confirma que quien
    pidió el cambio de verdad controla ese teléfono antes de dejarlo seguir
    operando con la identidad nueva.

    Raises:
        ValueError: si `nuevo_telefono` no tiene forma válida, o si ya
            pertenece a otra Persona.
    """
    canonico = normalizar_telefono(nuevo_telefono)
    if canonico == persona.telefono:
        return persona

    en_uso = (
        session.query(Persona)
        .filter(Persona.telefono == canonico, Persona.id != persona.id)
        .first()
    )
    if en_uso is not None:
        raise ValueError("Ese teléfono ya está en uso por otra cuenta.")

    persona.telefono = canonico
    session.flush()
    return persona


def desvincular_telefono_propio(session: Session, persona: Persona) -> Persona:
    """Quita el Teléfono de `persona` -- self-service desde tab "Datos"
    (`.scratch/ocupante-principal-escenarios`, ticket 14). Exige que
    `persona` ya tenga `whatsapp_usuario` asociado (ADR-0007,
    `ck_personas_telefono_o_whatsapp`: nunca los dos vacíos a la vez) --
    sin ese respaldo, la Persona perdería todo contacto Y toda forma de
    volver a entrar (el login sigue siendo estrictamente por Teléfono, vía
    OTP).

    A diferencia de `cambiar_telefono_propio`, acá no hay número nuevo que
    reverificar -- el caller es responsable de cerrar la sesión de
    inmediato tras el éxito, no de exigir una verificación OTP.

    Raises:
        ValueError: si `persona` no tiene `whatsapp_usuario` asociado.
    """
    if not persona.whatsapp_usuario:
        raise ValueError(
            "No puedes quitar tu Teléfono sin tener un usuario de WhatsApp "
            "asociado como respaldo -- pídele al personal que te lo agregue primero."
        )
    persona.telefono = None
    session.flush()
    return persona


def url_whatsapp(persona: Persona) -> str:
    """Link para abrir un chat de WhatsApp con `persona` (issue 67). Prioriza
    `whatsapp_usuario` (la función de usuarios de WhatsApp, rollout 2026) por
    sobre el teléfono -- si la Persona registró un username ahí, es porque
    prefiere que la contacten por ese medio.

    Dos dominios distintos a propósito (issue 301, .scratch/pendientes-
    cliente, pedido explícito de evitar `wa.me` para que la PWA de WhatsApp
    de Chrome capture el link en vez de abrir la web intermedia):

    - `whatsapp_usuario` es un username real (alfanumérico, `WHATSAPP_USUARIO_
      RE` arriba) -- NO es un teléfono. `web.whatsapp.com/send?phone=` exige
      un E.164 real, no tiene forma de abrir chat por username. `wa.me/<user>`
      sigue siendo el único mecanismo que Meta ofrece para este caso (sin
      fuente oficial 100% confirmada al momento de escribir esto -- Meta no
      ha publicado el esquema del deep link todavía, verificar en vivo).
    - Con teléfono, sí hay E.164 real: `web.whatsapp.com/send?phone=<dígitos>`.
    """
    if persona.whatsapp_usuario:
        return f"https://wa.me/{persona.whatsapp_usuario}"
    numero = re.sub(r"\D", "", persona.telefono)
    return f"https://web.whatsapp.com/send?phone={numero}"


def url_llamada(persona: Persona) -> str:
    """Link `tel:` para marcar directo al teléfono de `persona` (issue 67) --
    usa la forma canónica ya almacenada (con `+`), que iOS/Android aceptan
    igual que sin él."""
    return f"tel:{persona.telefono}"


def set_autoriza_recepcion_automatica(session: Session, persona: Persona, autoriza: bool) -> Persona:
    """Activa o desactiva la autorización automática de recepción
    (.scratch/mis-datos, ticket 12) — puramente informativo para el staff,
    no cambia nada de lo que el staff puede hacer hoy."""
    persona.autoriza_recepcion_automatica = autoriza
    session.flush()
    return persona
