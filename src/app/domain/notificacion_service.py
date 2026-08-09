# -*- coding: utf-8 -*-
"""
Notificación de eventos del Paquete (Seam A) — mensaje + destino, sin infra.

Cuatro eventos notifican: `ANUNCIADO`, `RECIBIDO`, `ENTREGADO`, `CANCELADO`
(Grupo 8 de `ajustes-post-referencia-funcional/REQUERIMIENTOS.md` — decisión
revertida sobre la original del brief §15.1, que excluía `ANUNCIADO`).

El envío es **best-effort**: si el `NotificationSender` falla, `notificar_evento`
NO propaga — la transición del Paquete ya se completó y no debe bloquearse por
un proveedor caído.

`resolver_destino_notificable` resuelve la Persona VIVA (no anonimizada, ADR-0005)
que debe recibir el aviso, y `notificar_evento` respeta su preferencia de canal
SMS para ese evento (`preferencia_notificacion_service.preferencia_activa` —
Grupo 13, Ronda 2 — reemplaza el booleano único `notificaciones_activas` por
una matriz Canal × Evento; el envío real hoy solo existe para SMS). Regla
unificada: el **Anunciante** recibe el aviso siempre que no haya un
Destinatario con teléfono propio y alcanzable — cubre tanto "nombre sin
teléfono" (nunca tuvo) como "fue anonimizado después" (ya no lo tiene), con
la MISMA función, no dos reglas separadas.

`construir_mensaje` busca primero una `PlantillaNotificacion` personalizada
para `(evento, motivo)`; si no existe, usa el texto por defecto de abajo
(comportamiento histórico, intacto) — la tabla es un OVERRIDE, nunca la
única fuente de verdad. `motivo` es el motivo de cancelación para
`CANCELADO`, o `ORIGEN_ANUNCIO_CLIENTE`/`ORIGEN_ANUNCIO_STAFF` para
`ANUNCIADO` (Grupo 19, Ronda 2: el mensaje cambia según quién anunció —
`paquete.announced_by_usuario_id` es el mismo dato que ya usa la auditoría
del Grupo 11), `None` para el resto.
"""

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .notification_sender import NotificationSender
from .ocupante_service import ocupante_activo_de_persona
from .paquete import EstadoPaquete, Paquete
from .persona import Persona
from .plantilla_notificacion import PlantillaNotificacion
from .preferencia_notificacion import CanalNotificacion
from .preferencia_notificacion_service import preferencia_activa

_EVENTOS_QUE_NOTIFICAN = (
    EstadoPaquete.ANUNCIADO,
    EstadoPaquete.RECIBIDO,
    EstadoPaquete.ENTREGADO,
    EstadoPaquete.CANCELADO,
)

# Plantillas por defecto — texto real con placeholders (`{recipient_name}`,
# `{access_code}`, `{motivo}`), no f-strings: así el mismo texto se puede
# MOSTRAR y editar desde `/administracion/notificaciones` (Grupo 8, ticket 02)
# con el mismo mecanismo de `.format()` que una plantilla personalizada.
PLANTILLAS_DEFAULT = {
    EstadoPaquete.RECIBIDO: (
        "Tu paquete ({recipient_name}) ya está en portería. "
        "Puedes reclamarlo cuando quieras. — PAQUETEX"
    ),
    EstadoPaquete.ENTREGADO: "Tu paquete ({recipient_name}) fue entregado. ¡Gracias! — PAQUETEX",
    EstadoPaquete.CANCELADO: (
        "Tu paquete ({recipient_name}) fue cancelado. Motivo: {motivo}. — PAQUETEX"
    ),
}

# ANUNCIADO tiene DOS defaults, no uno (Grupo 19, Ronda 2): quien lo anunció
# él mismo ya sabe que lo hizo; a quien el staff le anuncia un paquete a su
# nombre (vía /announce) recién se está enterando — mismo evento, tono
# distinto. Reutiliza la columna `motivo` de `PlantillaNotificacion` como
# llave de sub-variante, igual que ya hace CANCELADO con sus 4 motivos.
ORIGEN_ANUNCIO_CLIENTE = "CLIENTE"
ORIGEN_ANUNCIO_STAFF = "STAFF"

_ANUNCIADO_DEFAULT = {
    ORIGEN_ANUNCIO_CLIENTE: (
        "Anunciaste un paquete ({recipient_name}). "
        "Tu código de acceso: {access_code}. — PAQUETEX"
    ),
    ORIGEN_ANUNCIO_STAFF: (
        "Portería anunció un paquete a tu nombre ({recipient_name}). "
        "Tu código de acceso: {access_code}. — PAQUETEX"
    ),
}


def origen_anuncio(paquete: Paquete) -> str:
    """`ORIGEN_ANUNCIO_STAFF` si lo anunció el staff (vía `/announce`,
    `announced_by_usuario_id` no es `None`), `ORIGEN_ANUNCIO_CLIENTE` si lo
    anunció el propio cliente (vía `/anunciar`) — mismo dato que ya usa la
    auditoría de actor del Grupo 11."""
    return ORIGEN_ANUNCIO_STAFF if paquete.announced_by_usuario_id else ORIGEN_ANUNCIO_CLIENTE


def plantilla_por_defecto(evento: EstadoPaquete, motivo: str = None) -> str:
    """El texto de plantilla por defecto (sin personalizar) para `evento` —
    usado por `/administracion/notificaciones` para precargar el formulario.

    `motivo` es obligatorio para `ANUNCIADO` (`ORIGEN_ANUNCIO_CLIENTE` o
    `ORIGEN_ANUNCIO_STAFF`) e ignorado para el resto de eventos.
    """
    if evento is EstadoPaquete.ANUNCIADO:
        if motivo not in _ANUNCIADO_DEFAULT:
            raise ValueError(
                f"ANUNCIADO requiere motivo={ORIGEN_ANUNCIO_CLIENTE!r} o "
                f"{ORIGEN_ANUNCIO_STAFF!r}, recibido {motivo!r}."
            )
        return _ANUNCIADO_DEFAULT[motivo]
    if evento not in PLANTILLAS_DEFAULT:
        raise ValueError(f"El evento {evento!r} no dispara notificación.")
    return PLANTILLAS_DEFAULT[evento]


def _variables(paquete: Paquete) -> dict:
    return {
        "recipient_name": paquete.recipient_name,
        "access_code": paquete.access_code,
        "motivo": (paquete.cancel_reason or "").replace("_", " ").capitalize(),
    }


def construir_mensaje(session: Session, evento: EstadoPaquete, paquete: Paquete) -> str:
    """El texto del mensaje para `evento` — personalizado si hay una
    `PlantillaNotificacion` para `(evento, motivo)`, si no el default.

    Raises:
        ValueError: si `evento` no es uno de los que notifican.
    """
    if evento not in _EVENTOS_QUE_NOTIFICAN:
        raise ValueError(f"El evento {evento!r} no dispara notificación.")

    if evento is EstadoPaquete.CANCELADO:
        motivo_buscado = paquete.cancel_reason
    elif evento is EstadoPaquete.ANUNCIADO:
        motivo_buscado = origen_anuncio(paquete)
    else:
        motivo_buscado = None

    plantilla = (
        session.query(PlantillaNotificacion)
        .filter(
            PlantillaNotificacion.evento == evento.value,
            PlantillaNotificacion.motivo == motivo_buscado,
        )
        .one_or_none()
    )
    texto = (
        plantilla.texto
        if plantilla is not None
        else plantilla_por_defecto(evento, motivo_buscado)
    )
    return texto.format(**_variables(paquete))


def resolver_destino(paquete: Paquete) -> str:
    """El teléfono que dice el snapshot: el del Destinatario, o si no tiene
    (nombre sin teléfono), el del Anunciante. Función PURA (sin sesión) — no
    sabe si ese teléfono sigue perteneciendo a una identidad viva; para la
    decisión real de a quién notificar, ver `resolver_destino_notificable`."""
    return paquete.recipient_phone or paquete.announced_by_phone


def es_cliente_verificado(session: Session, persona: Persona) -> bool:
    """¿Puede `persona` ver/editar `/mis-datos`? (.scratch/mis-datos, ticket
    11) -- gate de acceso, no un badge: control anti-abuso, ya que
    `/anunciar` no verifica nada hoy (cualquiera crea una Persona con un
    teléfono+nombre inventado); sin esto, alguien podría usar `/otp` para
    entrar a `/mis-datos` con un teléfono nunca confirmado por un humano y
    empezar a declarar apartamentos/Ocupantes falsos.

    Verdadero si CUALQUIERA de estas dos cosas ya pasó:

    - Se le recibió físicamente al menos un Paquete alguna vez (mismo
      destino que `resolver_destino`: `recipient_phone`, o si no tiene,
      `announced_by_phone` -- pero exige `received_at is not None`, aunque
      el Paquete ya haya pasado a Entregado o Cancelado después).
    - YA es Ocupante activo de algún Apartamento -- quedó ahí por una acción
      humana explícita (el propio principal, ya verificado, lo agregó; o el
      staff lo hizo directamente), no por autoservicio sin verificar.

    Se verifica SOLO en la ruta `/mis-datos` (GET y POST) y sus sub-rutas de
    gestión de Ocupantes -- NUNCA en `/otp/solicitar` ni `/otp/verificar`:
    bloquear ahí permitiría enumerar por mensaje de error qué teléfonos son
    clientes reales."""
    if ocupante_activo_de_persona(session, persona.id) is not None:
        return True
    return (
        session.query(Paquete)
        .filter(
            Paquete.received_at.isnot(None),
            or_(
                Paquete.recipient_phone == persona.telefono,
                and_(
                    Paquete.recipient_phone.is_(None),
                    Paquete.announced_by_phone == persona.telefono,
                ),
            ),
        )
        .first()
        is not None
    )


def resolver_destino_notificable(session: Session, paquete: Paquete) -> Persona | None:
    """La Persona VIVA y CON TELÉFONO que debe recibir el aviso, o `None` si
    no queda nadie alcanzable por este canal.

    Prioriza al Destinatario si tiene teléfono propio Y ese teléfono sigue
    perteneciendo a una identidad viva (una Persona anonimizada ya no tiene ese
    teléfono — la búsqueda no la encuentra, sin necesidad de filtrar
    `eliminado_en` aparte). Si no hay Destinatario alcanzable —porque nunca tuvo
    teléfono, o porque lo tenía pero fue anonimizado después—, cae al
    **Anunciante** (FK real `announced_by_persona_id`, ADR-0003), siempre que el
    Anunciante mismo siga vivo Y tenga Teléfono.

    El chequeo de Teléfono en el Anunciante (ADR-0007, `.scratch/announce-
    rapido` ticket 03) importa porque este canal es SMS -- un Anunciante
    solo-WhatsApp existe y puede ser vivo/alcanzable en general, pero no por
    ESTE canal todavía (no hay envío por WhatsApp implementado). Sin este
    chequeo, `preparar_notificacion` devolvería `(None, mensaje)` como
    "destino" en vez de `None` (nada que enviar).
    """
    if paquete.recipient_phone:
        destinatario = (
            session.query(Persona)
            .filter(Persona.telefono == paquete.recipient_phone)
            .one_or_none()
        )
        if destinatario is not None:
            return destinatario

    anunciante = session.get(Persona, paquete.announced_by_persona_id)
    if anunciante is not None and anunciante.eliminado_en is None and anunciante.telefono:
        return anunciante
    return None


def preparar_notificacion(
    session: Session, paquete: Paquete, evento: EstadoPaquete
) -> tuple[str, str] | None:
    """Resuelve SI `evento` debe notificarse y con QUÉ — `(destino, mensaje)`,
    o `None` si no hay nada que enviar (sin destino alcanzable, o canal SMS
    desactivado para este evento). Nunca toca un `NotificationSender`, así
    que es rápida (solo lecturas de BD) — separada de `notificar_evento` a
    propósito (corrección en vivo 2026-08-02) para que las rutas web puedan
    resolver esto DENTRO del request (síncrono) y diferir el envío real
    (lento, red — ver el proveedor SMS bloqueado que motivó esto) a un
    `BackgroundTask`, sin bloquear el response por él. Ver
    `app/web/notifications.enviar_en_segundo_plano` y su uso en
    `app/web/routes/announce.py`, `announce_new.py`, `packages.py`.

    Un `evento` que no dispara notificación SÍ propaga su `ValueError` (error
    de uso, no fallo de infra) — mismo comportamiento que antes tenía
    `notificar_evento` en ese caso.
    """
    mensaje = construir_mensaje(session, evento, paquete)

    persona = resolver_destino_notificable(session, paquete)
    if persona is None:
        return None
    if not preferencia_activa(session, persona.id, CanalNotificacion.SMS, evento):
        return None

    return persona.telefono, mensaje


def notificar_evento(
    session: Session, paquete: Paquete, evento: EstadoPaquete, sender: NotificationSender
) -> None:
    """Notifica `evento` para `paquete` a través de `sender`, respetando la
    preferencia de quien de verdad recibiría el mensaje.

    Sin destino alcanzable, o con el canal SMS desactivado para este evento
    (Grupo 13, matriz Canal × Evento) → no envía nada, sin error. Best-effort
    en el envío: si `sender.enviar` lanza, la excepción se ignora aquí — la
    transición del Paquete ya se completó y no debe bloquearse por esto. Un
    `evento` que no dispara notificación SÍ propaga su `ValueError` (error de
    uso, no fallo de infra).

    SÍNCRONA (llama a `sender.enviar` de inmediato) — usada por los tests de
    dominio y por cualquier caller que de verdad quiera esperar el envío. Las
    rutas web de producción usan `preparar_notificacion` + un `BackgroundTask`
    en su lugar (ver arriba), para no bloquear el response con la latencia
    del proveedor SMS.
    """
    resultado = preparar_notificacion(session, paquete, evento)
    if resultado is None:
        return
    destino, mensaje = resultado

    try:
        sender.enviar(destino, mensaje)
    except Exception:
        pass


def obtener_texto_actual(session: Session, evento: EstadoPaquete, motivo: str = None) -> str:
    """El texto de plantilla vigente para `(evento, motivo)` — personalizado
    si existe, si no el default. Usado por `/administracion/notificaciones`
    para precargar el formulario de edición."""
    plantilla = (
        session.query(PlantillaNotificacion)
        .filter(
            PlantillaNotificacion.evento == evento.value,
            PlantillaNotificacion.motivo == motivo,
        )
        .one_or_none()
    )
    return plantilla.texto if plantilla is not None else plantilla_por_defecto(evento, motivo)


def guardar_plantilla(
    session: Session, evento: EstadoPaquete, motivo: str, texto: str
) -> PlantillaNotificacion:
    """Crea o actualiza la `PlantillaNotificacion` de `(evento, motivo)`.

    Carrera (dos ediciones simultáneas de la misma plantilla, mismo patrón
    que `persona_service.get_or_create_persona`): si el `INSERT` choca contra
    `uq_plantillas_notificacion_evento_motivo_nulo` (o la constraint normal
    para `motivo` no-nulo), se reintenta como UPDATE sobre la fila que la
    otra transacción ya creó, en vez de propagar el `IntegrityError`."""
    plantilla = (
        session.query(PlantillaNotificacion)
        .filter(
            PlantillaNotificacion.evento == evento.value,
            PlantillaNotificacion.motivo == motivo,
        )
        .one_or_none()
    )
    if plantilla is None:
        plantilla = PlantillaNotificacion(evento=evento.value, motivo=motivo)
        session.add(plantilla)
        plantilla.texto = texto
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            plantilla = (
                session.query(PlantillaNotificacion)
                .filter(
                    PlantillaNotificacion.evento == evento.value,
                    PlantillaNotificacion.motivo == motivo,
                )
                .one()
            )
            plantilla.texto = texto
            session.flush()
        return plantilla

    plantilla.texto = texto
    session.flush()
    return plantilla
