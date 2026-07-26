# -*- coding: utf-8 -*-
"""
Notificación de eventos del Paquete (Seam A) — mensaje + destino, sin infra.

Solo tres eventos notifican: `RECIBIDO`, `ENTREGADO`, `CANCELADO`. `ANUNCIADO` NO
notifica — el cliente ya lo sabe, acaba de hacerlo él mismo (cabo brief §15.1).

El envío es **best-effort**: si el `NotificationSender` falla, `notificar_evento`
NO propaga — la transición del Paquete ya se completó y no debe bloquearse por
un proveedor caído.

`resolver_destino_notificable` resuelve la Persona VIVA (no anonimizada, ADR-0005)
que debe recibir el aviso, y `notificar_evento` respeta su preferencia
(`notificaciones_activas`). Regla unificada: el **Anunciante** recibe el aviso
siempre que no haya un Destinatario con teléfono propio y alcanzable — cubre
tanto "nombre sin teléfono" (nunca tuvo) como "fue anonimizado después" (ya no
lo tiene), con la MISMA función, no dos reglas separadas.
"""

from sqlalchemy.orm import Session

from .notification_sender import NotificationSender
from .paquete import EstadoPaquete, Paquete
from .persona import Persona

_EVENTOS_QUE_NOTIFICAN = (
    EstadoPaquete.RECIBIDO,
    EstadoPaquete.ENTREGADO,
    EstadoPaquete.CANCELADO,
)


def construir_mensaje(evento: EstadoPaquete, paquete: Paquete) -> str:
    """El texto del mensaje para `evento`, claro y sin jerga técnica.

    Raises:
        ValueError: si `evento` no es uno de los que notifican
            (`RECIBIDO`/`ENTREGADO`/`CANCELADO`).
    """
    if evento is EstadoPaquete.RECIBIDO:
        return (
            f"Tu paquete ({paquete.recipient_name}) ya está en portería. "
            "Puedes reclamarlo cuando quieras. — PAQUETEX"
        )
    if evento is EstadoPaquete.ENTREGADO:
        return f"Tu paquete ({paquete.recipient_name}) fue entregado. ¡Gracias! — PAQUETEX"
    if evento is EstadoPaquete.CANCELADO:
        motivo = (paquete.cancel_reason or "").replace("_", " ").capitalize()
        return (
            f"Tu paquete ({paquete.recipient_name}) fue cancelado. "
            f"Motivo: {motivo}. — PAQUETEX"
        )
    raise ValueError(f"El evento {evento!r} no dispara notificación.")


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
    debe bloquearse por esto. Un `evento` que no dispara notificación (p.ej.
    `ANUNCIADO`) SÍ propaga su `ValueError` (error de uso, no fallo de infra).
    """
    mensaje = construir_mensaje(evento, paquete)

    persona = resolver_destino_notificable(session, paquete)
    if persona is None or not persona.notificaciones_activas:
        return

    try:
        sender.enviar(persona.telefono, mensaje)
    except Exception:
        pass
