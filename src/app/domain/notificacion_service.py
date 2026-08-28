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
`CANCELADO`, `None` para el resto -- ANUNCIADO tuvo brevemente dos
variantes según quién anunciaba (Grupo 19, Ronda 2), revertido en issue
202 (`.scratch/pendientes-cliente`, pedido explícito del cliente: el aviso
siempre llega al mismo destinatario final sin importar quién anunció, así
que una sola plantilla alcanza).
"""

import uuid

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .notification_sender import NotificationSender
from .ocupante_service import ocupante_activo_de_persona
from .paquete import EstadoPaquete, Paquete
from .persona import Persona
from .plantilla_notificacion import PlantillaNotificacion
from .plantilla_notificacion_historial import PlantillaNotificacionHistorial
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

# Asunto por defecto — SOLO relevante para canal=EMAIL (SMS/WhatsApp no
# tienen asunto). El cuerpo del mensaje (arriba) es el MISMO texto informativo
# para los 3 canales por decisión explícita del cliente (.scratch/plantillas-
# notificacion-multicanal) -- no se re-redacta un cuerpo aparte por canal,
# así que no existe un "ASUNTOS_DEFAULT por canal": el asunto es la única
# pieza de contenido exclusiva de Email.
ASUNTOS_DEFAULT = {
    EstadoPaquete.ANUNCIADO: "Anunciaste un paquete",
    EstadoPaquete.RECIBIDO: "Tu paquete ya está en portería",
    EstadoPaquete.ENTREGADO: "Tu paquete fue entregado",
    EstadoPaquete.CANCELADO: "Tu paquete fue cancelado",
}


def _default_de(evento: EstadoPaquete, tabla: dict) -> str:
    """Lookup compartido por `plantilla_por_defecto` y `asunto_por_defecto`
    -- solo cambia la tabla de donde se lee (cuerpo vs. asunto)."""
    if evento not in tabla:
        raise ValueError(f"El evento {evento!r} no dispara notificación.")
    return tabla[evento]


def plantilla_por_defecto(evento: EstadoPaquete, motivo: str = None) -> str:
    """El texto de plantilla por defecto (sin personalizar) para `evento` —
    usado por `/administracion/notificaciones` para precargar el formulario.

    `motivo` no distingue nada acá -- todo motivo de un mismo evento
    comparte el mismo default (solo importa para PERSONALIZAR, ej. cada
    `MotivoCancelacion` puede tener su propio texto guardado). Se mantiene
    el parámetro por compatibilidad con los callers existentes
    (`obtener_texto_actual`, etc.), aunque ya no se lee.
    """
    return _default_de(evento, PLANTILLAS_DEFAULT)


def asunto_por_defecto(evento: EstadoPaquete, motivo: str = None) -> str:
    """El asunto de Email por defecto (sin personalizar) para `evento` --
    mismo criterio de `motivo` que `plantilla_por_defecto`. Sin significado
    para SMS/WhatsApp (no tienen asunto); usado solo cuando `canal == EMAIL`.
    """
    return _default_de(evento, ASUNTOS_DEFAULT)


def _motivo_legible(motivo: str) -> str:
    """`NO_RECLAMADO` -> `No reclamado` -- compartido por `_variables` y
    `variables_ejemplo`, mismo texto en ambos."""
    return (motivo or "").replace("_", " ").capitalize()


def _variables(paquete: Paquete) -> dict:
    return {
        "recipient_name": paquete.recipient_name,
        "access_code": paquete.access_code,
        "motivo": _motivo_legible(paquete.cancel_reason),
    }


def variables_ejemplo(motivo: str = None) -> dict:
    """Mismo shape que `_variables`, con datos de ejemplo en vez de un
    `Paquete` real -- usado por la vista previa de Email de
    `/administracion/notificaciones` (`.scratch/plantillas-notificacion-
    multicanal`, ticket 03) para resolver `{recipient_name}`/`{access_code}`/
    `{motivo}` antes de envolver el resultado en el layout de marca. `motivo`
    solo importa para `CANCELADO` -- mismo criterio que `_variables`, que lo
    calcula igual sin importar el evento (una plantilla que no lo referencia
    simplemente no lo usa)."""
    return {
        "recipient_name": "Juan Pérez",
        "access_code": "AB12CD",
        "motivo": _motivo_legible(motivo),
    }


def resolver_plantilla(texto: str, variables: dict) -> str:
    """`texto.format(**variables)`, tolerante: si `texto` trae una llave que
    no calza con `variables` (o una `{`/`}` suelta), se devuelve tal cual en
    vez de reventar -- a diferencia de `construir_mensaje` (envío real de
    SMS, que SÍ deja propagar el error porque ese texto ya pasó por el
    guardado de `/administracion/notificaciones`), esta función la usa la
    vista previa de Email (ticket 03) sobre texto que el admin puede estar
    editando a medio escribir, donde reventar la pantalla completa por una
    llave mal cerrada sería peor que mostrar el texto sin resolver."""
    try:
        return texto.format(**variables)
    except (KeyError, IndexError, ValueError):
        return texto


def _buscar_plantilla(
    session: Session, evento: EstadoPaquete, motivo: str, canal: CanalNotificacion
) -> PlantillaNotificacion | None:
    """La `PlantillaNotificacion` de `(evento, motivo, canal)`, o `None` si no
    ha sido personalizada -- shape compartido por `construir_mensaje`,
    `obtener_texto_actual`, `obtener_asunto_actual` y `guardar_plantilla`."""
    return (
        session.query(PlantillaNotificacion)
        .filter(
            PlantillaNotificacion.evento == evento.value,
            PlantillaNotificacion.motivo == motivo,
            PlantillaNotificacion.canal == canal.value,
        )
        .one_or_none()
    )


def construir_mensaje(session: Session, evento: EstadoPaquete, paquete: Paquete) -> str:
    """El texto del mensaje para `evento` — personalizado si hay una
    `PlantillaNotificacion` para `(evento, motivo)`, si no el default.

    Raises:
        ValueError: si `evento` no es uno de los que notifican.
    """
    if evento not in _EVENTOS_QUE_NOTIFICAN:
        raise ValueError(f"El evento {evento!r} no dispara notificación.")

    motivo_buscado = paquete.cancel_reason if evento is EstadoPaquete.CANCELADO else None

    plantilla = _buscar_plantilla(session, evento, motivo_buscado, CanalNotificacion.SMS)
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


def obtener_texto_actual(
    session: Session,
    evento: EstadoPaquete,
    motivo: str = None,
    canal: CanalNotificacion = CanalNotificacion.SMS,
) -> str:
    """El texto de plantilla vigente para `(evento, motivo, canal)` —
    personalizado si existe, si no el default de ese canal. Usado por
    `/administracion/notificaciones` para precargar el formulario de edición.

    `canal` por defecto `SMS` — mantiene el comportamiento y la firma
    posicional de antes de la extensión multicanal (`.scratch/plantillas-
    notificacion-multicanal`) para cualquier caller que no lo pase."""
    plantilla = _buscar_plantilla(session, evento, motivo, canal)
    return plantilla.texto if plantilla is not None else plantilla_por_defecto(evento, motivo)


def obtener_asunto_actual(session: Session, evento: EstadoPaquete, motivo: str = None) -> str:
    """El asunto de Email vigente para `(evento, motivo)` — personalizado si
    existe una `PlantillaNotificacion` de `canal=EMAIL`, si no el default.
    Sin equivalente en SMS/WhatsApp (no tienen asunto)."""
    plantilla = _buscar_plantilla(session, evento, motivo, CanalNotificacion.EMAIL)
    if plantilla is not None and plantilla.asunto:
        return plantilla.asunto
    return asunto_por_defecto(evento, motivo)


def mensaje_de_prueba(
    session: Session, evento: EstadoPaquete, motivo: str, canal: CanalNotificacion
) -> tuple[str, str | None]:
    """`(texto, asunto)` para un ENVÍO DE PRUEBA real de `/administracion/
    notificaciones` (.scratch/notificaciones-enviar-prueba, ticket 02) — la
    plantilla YA GUARDADA de `(evento, motivo, canal)` (nunca un borrador sin
    guardar), con sus variables resueltas a datos de ejemplo
    (`variables_ejemplo`) igual que hacía el preview de Email ya retirado
    (issue 204, `.scratch/pendientes-cliente`) -- mismas piezas
    (`obtener_texto_actual`/`obtener_asunto_actual` + `resolver_plantilla`),
    ahora en el dominio en vez de la capa web porque el envío real también
    las necesita, no solo una vista.

    `asunto` es `None` para SMS/WhatsApp (sin equivalente en esos canales,
    mismo criterio que `obtener_asunto_actual`)."""
    variables = variables_ejemplo(motivo)
    texto = resolver_plantilla(obtener_texto_actual(session, evento, motivo, canal), variables)
    asunto = None
    if canal is CanalNotificacion.EMAIL:
        asunto = resolver_plantilla(obtener_asunto_actual(session, evento, motivo), variables)
    return texto, asunto


def guardar_plantilla(
    session: Session,
    evento: EstadoPaquete,
    motivo: str,
    texto: str,
    canal: CanalNotificacion = CanalNotificacion.SMS,
    asunto: str = None,
    usuario_id: uuid.UUID | None = None,
) -> PlantillaNotificacion:
    """Crea o actualiza la `PlantillaNotificacion` de `(evento, motivo, canal)`,
    y deja un registro en `PlantillaNotificacionHistorial` por cada guardado
    exitoso (`.scratch/plantillas-notificacion-multicanal`, ticket 04) --
    append-only, nunca se edita ni se borra.

    `canal`/`asunto` van DESPUÉS de `texto` (no antes) a propósito: preserva
    la firma posicional `(session, evento, motivo, texto)` de antes de la
    extensión multicanal, así que cualquier caller existente que no pase
    `canal` sigue guardando SMS exactamente como antes. `asunto` solo importa
    para `canal == EMAIL`. `usuario_id` es opcional (default `None`) por el
    mismo motivo -- un historial con `usuario_id=NULL` es honesto para un
    caller sin actor real (tests de dominio, scripts), no un dato inventado.

    Carrera (dos ediciones simultáneas de la misma plantilla, mismo patrón
    que `persona_service.get_or_create_persona`): si el `INSERT` choca contra
    `uq_plantillas_notificacion_evento_motivo_nulo` (o la constraint normal
    para `motivo` no-nulo), se reintenta como UPDATE sobre la fila que la
    otra transacción ya creó, en vez de propagar el `IntegrityError`."""
    plantilla = _buscar_plantilla(session, evento, motivo, canal)
    if plantilla is None:
        plantilla = PlantillaNotificacion(evento=evento.value, motivo=motivo, canal=canal.value)
        session.add(plantilla)
        plantilla.texto = texto
        plantilla.asunto = asunto
        try:
            session.flush()
            texto_anterior, asunto_anterior = None, None
        except IntegrityError:
            session.rollback()
            plantilla = _buscar_plantilla(session, evento, motivo, canal)
            texto_anterior, asunto_anterior = plantilla.texto, plantilla.asunto
            plantilla.texto = texto
            plantilla.asunto = asunto
            session.flush()
    else:
        texto_anterior, asunto_anterior = plantilla.texto, plantilla.asunto
        plantilla.texto = texto
        plantilla.asunto = asunto
        session.flush()

    session.add(
        PlantillaNotificacionHistorial(
            plantilla_id=plantilla.id,
            evento=evento.value,
            motivo=motivo,
            canal=canal.value,
            usuario_id=usuario_id,
            texto_anterior=texto_anterior,
            texto_nuevo=texto,
            asunto_anterior=asunto_anterior,
            asunto_nuevo=asunto,
        )
    )
    session.flush()
    return plantilla
