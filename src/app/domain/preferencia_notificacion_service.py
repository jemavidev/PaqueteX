# -*- coding: utf-8 -*-
"""
Servicio de dominio de preferencias de notificación por Canal × Evento
(Grupo 13, Ronda 2 de `ajustes-post-referencia-funcional`).
"""

from sqlalchemy.orm import Session

from .ocupante import Ocupante
from .paquete import EstadoPaquete
from .preferencia_notificacion import CanalNotificacion, PersonaPreferenciaNotificacion

EVENTOS = (
    EstadoPaquete.ANUNCIADO,
    EstadoPaquete.RECIBIDO,
    EstadoPaquete.ENTREGADO,
    EstadoPaquete.CANCELADO,
)


def _default_activo(canal: CanalNotificacion, evento: EstadoPaquete) -> bool:
    """Default cuando no hay preferencia guardada -- ninguna Persona nueva
    necesita backfill de filas, así que este default es lo único que
    determina su comportamiento hasta que alguien lo cambie explícitamente.

    2026-08-10 (pedido del cliente, solo aplica a SMS/teléfono): antes,
    SMS quedaba activo para los 4 eventos por default. Ahora solo
    ANUNCIADO -- confirma que un teléfono nuevo es alcanzable con al menos
    1 SMS real, sin generar envíos (ni costo) en Recibido/Entregado/
    Cancelado hasta que se activen a propósito. El resto de canales
    (WhatsApp incluido) sigue inactivo por default, sin cambios."""
    return canal is CanalNotificacion.SMS and evento is EstadoPaquete.ANUNCIADO


def preferencia_activa(
    session: Session, persona_id, canal: CanalNotificacion, evento: EstadoPaquete
) -> bool:
    """¿Debe notificarse a `persona_id` por `canal` cuando ocurre `evento`?

    Sin fila guardada para esta combinación, resuelve al default de
    `_default_activo`."""
    pref = (
        session.query(PersonaPreferenciaNotificacion)
        .filter(
            PersonaPreferenciaNotificacion.persona_id == persona_id,
            PersonaPreferenciaNotificacion.canal == canal.value,
            PersonaPreferenciaNotificacion.evento == evento.value,
        )
        .one_or_none()
    )
    if pref is not None:
        return pref.activo
    return _default_activo(canal, evento)


def preferencia_efectiva_ocupante(
    session: Session, ocupante: Ocupante, canal: CanalNotificacion, evento: EstadoPaquete
) -> bool:
    """¿Debe notificarse por `canal` cuando ocurre `evento`, a nombre de
    `ocupante`? (.scratch/mis-datos, ticket 06)

    Si `ocupante` tiene teléfono propio, resuelve por SUS PROPIAS
    preferencias (`preferencia_activa` sobre su `persona_id`) -- tiene su
    propia Persona desde que se le asoció el teléfono.

    Si NO tiene teléfono, sus avisos le llegan al teléfono del PRINCIPAL
    activo de su Apartamento de todos modos -- así que usa las preferencias
    YA CONFIGURADAS de ese principal. Sin principal resoluble (no debería
    pasar en la práctica: todo Apartamento con Ocupantes activos siempre
    tiene uno), cae al mismo default que `preferencia_activa` (ver
    `_default_activo`)."""
    if ocupante.persona_id is not None:
        return preferencia_activa(session, ocupante.persona_id, canal, evento)

    principal = (
        session.query(Ocupante)
        .filter(
            Ocupante.apartamento_id == ocupante.apartamento_id,
            Ocupante.es_principal.is_(True),
            Ocupante.desvinculado_en.is_(None),
        )
        .one_or_none()
    )
    if principal is None or principal.persona_id is None:
        return _default_activo(canal, evento)
    return preferencia_activa(session, principal.persona_id, canal, evento)


def matriz_preferencias(session: Session, persona_id) -> dict:
    """La matriz completa Canal × Evento para `persona_id`, con los defaults
    ya resueltos — lista para renderizar los 16 checkboxes de `/mis-datos`.

    Devuelve `{(canal, evento): bool}`, ambos como los `str` valor de sus
    enums (para que la plantilla Jinja no necesite importar los enums)."""
    guardadas = {
        (p.canal.value, p.evento): p.activo
        for p in session.query(PersonaPreferenciaNotificacion)
        .filter(PersonaPreferenciaNotificacion.persona_id == persona_id)
        .all()
    }
    matriz = {}
    for canal in CanalNotificacion:
        for evento in EVENTOS:
            clave = (canal.value, evento.value)
            matriz[clave] = guardadas.get(clave, _default_activo(canal, evento))
    return matriz


def guardar_preferencia(
    session: Session,
    persona_id,
    canal: CanalNotificacion,
    evento: EstadoPaquete,
    activo: bool,
) -> PersonaPreferenciaNotificacion:
    """Crea o actualiza la preferencia de `(persona_id, canal, evento)`."""
    pref = (
        session.query(PersonaPreferenciaNotificacion)
        .filter(
            PersonaPreferenciaNotificacion.persona_id == persona_id,
            PersonaPreferenciaNotificacion.canal == canal.value,
            PersonaPreferenciaNotificacion.evento == evento.value,
        )
        .one_or_none()
    )
    if pref is None:
        pref = PersonaPreferenciaNotificacion(
            persona_id=persona_id, canal=canal.value, evento=evento.value
        )
        session.add(pref)
    pref.activo = activo
    session.flush()
    return pref


def guardar_matriz_preferencias(
    session: Session, persona_id, activos: set, *, combinaciones: set | None = None
) -> None:
    """Guarda la matriz de una vez — `activos` es el conjunto de
    `(canal_str, evento_str)` que deben quedar activos; el resto de
    `combinaciones` (default: TODA la matriz `CanalNotificacion` × `EVENTOS`)
    queda inactivo. Mismo patrón que un checkbox HTML: lo ausente en el POST
    es "desmarcado".

    `combinaciones` restringe qué filas se tocan -- lo que quede FUERA no se
    lee ni se escribe. Sin esto, un actor sin permiso sobre una combinación
    (ver `canal_evento_editable`) la resetearía a inactivo por simple
    omisión con cualquier guardado suyo, aunque no la haya tocado a
    propósito -- pisando lo que un ADMIN haya configurado ahí."""
    if combinaciones is None:
        combinaciones = {(canal.value, evento.value) for canal in CanalNotificacion for evento in EVENTOS}
    for canal in CanalNotificacion:
        for evento in EVENTOS:
            clave = (canal.value, evento.value)
            if clave not in combinaciones:
                continue
            guardar_preferencia(session, persona_id, canal, evento, clave in activos)


def canal_evento_editable(canal: CanalNotificacion, evento: EstadoPaquete, *, es_admin: bool) -> bool:
    """¿Puede un actor NO-ADMIN (Residente en `/mis-datos`, Staff Operador en
    `/residentes/{id}`) activar/desactivar esta combinación desde la matriz?

    2026-08-26 (pedido del cliente): SMS es el único canal con proveedor real
    conectado (ver docstring del módulo de `preferencia_notificacion.py`) --
    encenderlo fuera de ANUNCIADO genera costo y mensajes reales que nadie
    pidió a propósito. Un Residente o un Operador solo controlan SMS×Anuncio;
    Recibido/Entregado/Cancelado quedan bloqueados salvo que un ADMIN los
    toque -- el resto de canales no cambia (ninguno tiene esta restricción)."""
    if es_admin or canal is not CanalNotificacion.SMS:
        return True
    return evento is EstadoPaquete.ANUNCIADO


def eventos_bloqueados_para(*, es_admin: bool) -> set[tuple[str, str]]:
    """Combinaciones `(canal.value, evento.value)` que la matriz debe
    renderizar deshabilitadas para este actor -- ver `canal_evento_editable`.
    Vacío para un ADMIN (controla la matriz completa)."""
    return {
        (canal.value, evento.value)
        for canal in CanalNotificacion
        for evento in EVENTOS
        if not canal_evento_editable(canal, evento, es_admin=es_admin)
    }
