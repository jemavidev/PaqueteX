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


def preferencia_activa(
    session: Session, persona_id, canal: CanalNotificacion, evento: EstadoPaquete
) -> bool:
    """¿Debe notificarse a `persona_id` por `canal` cuando ocurre `evento`?

    Sin fila guardada para esta combinación, resuelve al default histórico:
    SMS activo, cualquier otro canal inactivo (preserva el comportamiento de
    antes de este grupo sin necesitar backfill de datos)."""
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
    return canal is CanalNotificacion.SMS


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
    tiene uno), cae al mismo default histórico que `preferencia_activa`."""
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
        return canal is CanalNotificacion.SMS
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
            matriz[clave] = guardadas.get(clave, canal is CanalNotificacion.SMS)
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


def guardar_matriz_preferencias(session: Session, persona_id, activos: set) -> None:
    """Guarda TODA la matriz de una vez — `activos` es el conjunto de
    `(canal_str, evento_str)` que deben quedar activos; todo lo demás de la
    matriz completa (`CanalNotificacion` × `EVENTOS`) queda inactivo. Mismo
    patrón que un checkbox HTML: lo ausente en el POST es "desmarcado"."""
    for canal in CanalNotificacion:
        for evento in EVENTOS:
            guardar_preferencia(
                session, persona_id, canal, evento, (canal.value, evento.value) in activos
            )


def activar_canal_en_todos_los_eventos(
    session: Session, persona_id, canal: CanalNotificacion, activo: bool
) -> None:
    """Atajo usado por la vista simplificada de staff (`/residentes/{id}`):
    activa/desactiva `canal` para los 4 eventos a la vez."""
    for evento in EVENTOS:
        guardar_preferencia(session, persona_id, canal, evento, activo)
