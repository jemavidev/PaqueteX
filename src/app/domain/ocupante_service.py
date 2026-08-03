# -*- coding: utf-8 -*-
"""
Servicio de dominio de Ocupante — padrón de residentes de un Apartamento con
Teléfono opcional (Seam A, ADR-0006).

Invariante: un Apartamento con al menos un Ocupante tiene SIEMPRE exactamente
uno marcado `es_principal`, y ese principal SIEMPRE tiene `persona_id` (un
Teléfono real). La base de datos lo garantiza con un índice único parcial
(`uq_ocupantes_principal_por_apartamento`); este módulo garantiza que la
aplicación nunca intente violarlo (promover exige `persona_id`, y degrada al
anterior en la misma transacción antes de marcar el nuevo).
"""

from sqlalchemy.orm import Session

from .apartamento import Apartamento
from .ocupante import Ocupante
from .persona_service import get_or_create_persona
from .texto import normalizar_nombre


def listar_ocupantes(session: Session, apartamento: Apartamento) -> list[Ocupante]:
    """Los Ocupantes de `apartamento`, principal primero."""
    return (
        session.query(Ocupante)
        .filter(Ocupante.apartamento_id == apartamento.id)
        .order_by(Ocupante.es_principal.desc(), Ocupante.created_at.asc())
        .all()
    )


def agregar_ocupante(
    session: Session, apartamento: Apartamento, nombre: str, telefono: str = None
) -> Ocupante:
    """Agrega un Ocupante a `apartamento`.

    Con `telefono`, reutiliza o crea la Persona correspondiente
    (`get_or_create_persona`) y liga `persona_id`. Sin `telefono`, crea un
    registro liviano (solo nombre) que no puede loguearse ni anunciar por sí
    mismo.

    Si `apartamento` no tiene NINGÚN Ocupante todavía, este primer Ocupante
    DEBE tener teléfono (`ValueError` si no) y queda marcado `es_principal`
    automáticamente — un Apartamento con Ocupantes siempre tiene un principal.
    Ocupantes agregados después nunca se auto-promueven (usar
    `promover_a_principal` explícitamente).

    Args:
        session: sesión de SQLAlchemy activa.
        apartamento: el Apartamento al que se agrega.
        nombre: nombre del Ocupante.
        telefono: teléfono del Ocupante (cualquier formato), o ``None``.

    Returns:
        El Ocupante recién creado.

    Raises:
        ValueError: si es el primer Ocupante del Apartamento y no trae teléfono.
    """
    es_el_primero = not listar_ocupantes(session, apartamento)
    if es_el_primero and not (telefono or "").strip():
        raise ValueError(
            "El primer Ocupante de un Apartamento debe tener Teléfono "
            "(queda como principal automáticamente)."
        )

    persona = None
    if (telefono or "").strip():
        persona = get_or_create_persona(session, telefono, nombre)

    ocupante = Ocupante(
        apartamento_id=apartamento.id,
        persona_id=persona.id if persona is not None else None,
        nombre=normalizar_nombre(nombre),
        es_principal=es_el_primero,
    )
    session.add(ocupante)
    session.flush()
    return ocupante


def promover_a_principal(session: Session, ocupante: Ocupante) -> Ocupante:
    """Promueve `ocupante` a principal de su Apartamento, degradando al
    principal anterior (si había uno) en la misma transacción.

    Raises:
        ValueError: si `ocupante` no tiene `persona_id` (sin Teléfono, no puede
            ser principal).
    """
    if ocupante.persona_id is None:
        raise ValueError(
            "Un Ocupante sin Teléfono no puede promoverse a principal."
        )

    anterior = (
        session.query(Ocupante)
        .filter(
            Ocupante.apartamento_id == ocupante.apartamento_id,
            Ocupante.es_principal.is_(True),
            Ocupante.id != ocupante.id,
        )
        .one_or_none()
    )
    if anterior is not None:
        anterior.es_principal = False
        session.flush()  # libera el índice único parcial antes de marcar el nuevo

    ocupante.es_principal = True
    session.flush()
    return ocupante
