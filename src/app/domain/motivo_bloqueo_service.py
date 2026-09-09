# -*- coding: utf-8 -*-
"""
Servicio de dominio del catálogo de motivos de bloqueo (módulo "Bloquear
clientes", `.scratch/bloquear-clientes`).

CRUD simple sobre `MotivoBloqueo` -- mismo criterio exacto que
`motivo_cancelacion_service`: sin campo activo/inactivo (borrado siempre
duro), sin historial de auditoría. A diferencia de los motivos de
cancelación, NO hay restricción de "catálogo nunca vacío" -- bloquear a
alguien es una acción puntual del staff, no algo que dependa de que el
catálogo tenga necesariamente contenido en todo momento (aunque en la
práctica, sin ningún motivo, no se podría bloquear a nadie -- decisión
aceptada, mismo espíritu que `MotivoAnulacionCobro`).
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .motivo_bloqueo import MotivoBloqueo

_MAX_LEN = 40


def listar_motivos_bloqueo(session: Session) -> list[MotivoBloqueo]:
    return session.query(MotivoBloqueo).order_by(MotivoBloqueo.creado_en.asc()).all()


def motivo_bloqueo_valido(session: Session, etiqueta: str) -> bool:
    if not etiqueta:
        return False
    return (
        session.query(MotivoBloqueo).filter(MotivoBloqueo.etiqueta == etiqueta).first()
        is not None
    )


def crear_motivo_bloqueo(session: Session, etiqueta: str) -> MotivoBloqueo:
    """Raises ValueError si la etiqueta queda vacía tras `strip()`, supera
    los 40 caracteres, o ya existe otro motivo con el mismo texto exacto."""
    limpio = (etiqueta or "").strip()
    if not limpio:
        raise ValueError("El motivo no puede quedar vacío.")
    if len(limpio) > _MAX_LEN:
        raise ValueError(f"El motivo no puede superar los {_MAX_LEN} caracteres.")

    ya_existe = (
        session.query(MotivoBloqueo).filter(MotivoBloqueo.etiqueta == limpio).first()
        is not None
    )
    if ya_existe:
        raise ValueError(f'Ya existe un motivo con el texto "{limpio}".')

    motivo = MotivoBloqueo(etiqueta=limpio)
    session.add(motivo)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ValueError(f'Ya existe un motivo con el texto "{limpio}".')
    return motivo


def eliminar_motivo_bloqueo(session: Session, motivo_id) -> None:
    """Raises ValueError si `motivo_id` no existe."""
    motivo = session.get(MotivoBloqueo, motivo_id)
    if motivo is None:
        raise ValueError("Motivo no encontrado.")
    session.delete(motivo)
    session.flush()
