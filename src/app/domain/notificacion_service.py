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
que debe recibir el aviso, y `notificar_evento` respeta su preferencia
(`notificaciones_activas`). Regla unificada: el **Anunciante** recibe el aviso
siempre que no haya un Destinatario con teléfono propio y alcanzable — cubre
tanto "nombre sin teléfono" (nunca tuvo) como "fue anonimizado después" (ya no
lo tiene), con la MISMA función, no dos reglas separadas.

`construir_mensaje` busca primero una `PlantillaNotificacion` personalizada
para `(evento, motivo si CANCELADO)`; si no existe, usa el texto por defecto
de abajo (comportamiento histórico, intacto) — la tabla es un OVERRIDE, nunca
la única fuente de verdad.
"""

from sqlalchemy.orm import Session

from .notification_sender import NotificationSender
from .paquete import EstadoPaquete, Paquete
from .persona import Persona
from .plantilla_notificacion import PlantillaNotificacion

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
    EstadoPaquete.ANUNCIADO: (
        "Anunciaste un paquete ({recipient_name}). "
        "Tu código de acceso: {access_code}. — PAQUETEX"
    ),
    EstadoPaquete.RECIBIDO: (
        "Tu paquete ({recipient_name}) ya está en portería. "
        "Puedes reclamarlo cuando quieras. — PAQUETEX"
    ),
    EstadoPaquete.ENTREGADO: "Tu paquete ({recipient_name}) fue entregado. ¡Gracias! — PAQUETEX",
    EstadoPaquete.CANCELADO: (
        "Tu paquete ({recipient_name}) fue cancelado. Motivo: {motivo}. — PAQUETEX"
    ),
}


def plantilla_por_defecto(evento: EstadoPaquete) -> str:
    """El texto de plantilla por defecto (sin personalizar) para `evento` —
    usado por `/administracion/notificaciones` para precargar el formulario."""
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

    motivo_buscado = paquete.cancel_reason if evento is EstadoPaquete.CANCELADO else None
    plantilla = (
        session.query(PlantillaNotificacion)
        .filter(
            PlantillaNotificacion.evento == evento.value,
            PlantillaNotificacion.motivo == motivo_buscado,
        )
        .one_or_none()
    )
    texto = plantilla.texto if plantilla is not None else PLANTILLAS_DEFAULT[evento]
    return texto.format(**_variables(paquete))


def resolver_destino(paquete: Paquete) -> str:
    """El teléfono que dice el snapshot: el del Destinatario, o si no tiene
    (nombre sin teléfono), el del Anunciante. Función PURA (sin sesión) — no
    sabe si ese teléfono sigue perteneciendo a una identidad viva; para la
    decisión real de a quién notificar, ver `resolver_destino_notificable`."""
    return paquete.recipient_phone or paquete.announced_by_phone


def resolver_destino_notificable(session: Session, paquete: Paquete) -> Persona | None:
    """La Persona VIVA que debe recibir el aviso, o `None` si no queda nadie
    alcanzable.

    Prioriza al Destinatario si tiene teléfono propio Y ese teléfono sigue
    perteneciendo a una identidad viva (una Persona anonimizada ya no tiene ese
    teléfono — la búsqueda no la encuentra, sin necesidad de filtrar
    `eliminado_en` aparte). Si no hay Destinatario alcanzable —porque nunca tuvo
    teléfono, o porque lo tenía pero fue anonimizado después—, cae al
    **Anunciante** (FK real `announced_by_persona_id`, ADR-0003), siempre que el
    Anunciante mismo siga vivo.
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
    if anunciante is not None and anunciante.eliminado_en is None:
        return anunciante
    return None


def notificar_evento(
    session: Session, paquete: Paquete, evento: EstadoPaquete, sender: NotificationSender
) -> None:
    """Notifica `evento` para `paquete` a través de `sender`, respetando la
    preferencia de quien de verdad recibiría el mensaje.

    Sin destino alcanzable, o con `notificaciones_activas=False` → no envía
    nada, sin error. Best-effort en el envío: si `sender.enviar` lanza, la
    excepción se ignora aquí — la transición del Paquete ya se completó y no
    debe bloquearse por esto. Un `evento` que no dispara notificación SÍ
    propaga su `ValueError` (error de uso, no fallo de infra).
    """
    mensaje = construir_mensaje(session, evento, paquete)

    persona = resolver_destino_notificable(session, paquete)
    if persona is None or not persona.notificaciones_activas:
        return

    try:
        sender.enviar(persona.telefono, mensaje)
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
    return plantilla.texto if plantilla is not None else plantilla_por_defecto(evento)


def guardar_plantilla(
    session: Session, evento: EstadoPaquete, motivo: str, texto: str
) -> PlantillaNotificacion:
    """Crea o actualiza la `PlantillaNotificacion` de `(evento, motivo)`."""
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
    session.flush()
    return plantilla
