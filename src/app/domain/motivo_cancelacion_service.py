# -*- coding: utf-8 -*-
"""
Servicio de dominio del catálogo de motivos de cancelación (Seam A).

CRUD simple sobre `MotivoCancelacion` -- sin código estable separado de la
etiqueta, sin activo/inactivo (borrado siempre duro), sin historial de
auditoría (decisiones explícitas del cliente, `.scratch/motivos-cancelacion-
catalogo/spec.md`). La única regla de negocio real es no dejar el catálogo
vacío: cancelar un paquete sigue exigiendo un motivo obligatorio
(`paquete_lifecycle.cancel`), así que el modal "Cancelar paquete" de
`/paquetes` nunca puede quedarse sin ninguna opción para elegir.

Renombrar (`editar_motivo`) o borrar (`eliminar_motivo`) un motivo NO
re-engancha ni limpia las filas de `PlantillaNotificacion`/`Paquete` que ya
usaban su texto anterior -- son búsquedas por texto exacto, sin FK de por
medio (mismo criterio que ya usaba el enum fijo que este catálogo reemplaza).
Efecto aceptado explícitamente por el cliente a cambio de simplicidad.
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .motivo_cancelacion import MotivoCancelacion

_MAX_LEN = 40


def listar_motivos(session: Session) -> list[MotivoCancelacion]:
    """Todos los motivos del catálogo, en orden de creación (ascendente) --
    el mismo orden en que aparecen en el picker de cancelación y en las
    filas CANCELADO de `/administracion/notificaciones`."""
    return (
        session.query(MotivoCancelacion)
        .order_by(MotivoCancelacion.creado_en.asc())
        .all()
    )


def motivo_valido(session: Session, etiqueta: str) -> bool:
    """¿Existe hoy en el catálogo un motivo con este texto exacto? -- usado
    por `packages.py::cancel_action` para rechazar server-side un motivo que
    ya no existe (ej. borrado por otro ADMIN justo antes del submit). El
    caso especial "Otro" (texto libre) se valida aparte, contra su propia
    regla, no contra este catálogo."""
    if not etiqueta:
        return False
    return (
        session.query(MotivoCancelacion)
        .filter(MotivoCancelacion.etiqueta == etiqueta)
        .first()
        is not None
    )


def _validar_etiqueta(etiqueta: str) -> str:
    limpio = (etiqueta or "").strip()
    if not limpio:
        raise ValueError("El motivo no puede quedar vacío.")
    if len(limpio) > _MAX_LEN:
        raise ValueError(f"El motivo no puede superar los {_MAX_LEN} caracteres.")
    return limpio


def _existe_duplicado(session: Session, etiqueta: str, excluir_id=None) -> bool:
    query = session.query(MotivoCancelacion).filter(MotivoCancelacion.etiqueta == etiqueta)
    if excluir_id is not None:
        query = query.filter(MotivoCancelacion.id != excluir_id)
    return query.first() is not None


def crear_motivo(session: Session, etiqueta: str) -> MotivoCancelacion:
    """Crea un motivo nuevo.

    Raises:
        ValueError: si la etiqueta queda vacía tras `strip()`, supera los 40
            caracteres, o ya existe otro motivo con el mismo texto exacto.
    """
    limpio = _validar_etiqueta(etiqueta)
    if _existe_duplicado(session, limpio):
        raise ValueError(f'Ya existe un motivo con el texto "{limpio}".')

    motivo = MotivoCancelacion(etiqueta=limpio)
    session.add(motivo)
    try:
        session.flush()
    except IntegrityError:
        # Carrera: otro ADMIN creó el mismo texto exacto entre el chequeo de
        # arriba y este flush -- se rechaza igual que un duplicado detectado
        # a tiempo, en vez de dejar propagar un 500 crudo.
        session.rollback()
        raise ValueError(f'Ya existe un motivo con el texto "{limpio}".')
    return motivo


def editar_motivo(session: Session, motivo_id, etiqueta: str) -> MotivoCancelacion:
    """Renombra un motivo existente.

    Raises:
        ValueError: si `motivo_id` no existe, la etiqueta queda vacía tras
            `strip()`, supera los 40 caracteres, o ya existe OTRO motivo con
            el mismo texto exacto.
    """
    motivo = session.get(MotivoCancelacion, motivo_id)
    if motivo is None:
        raise ValueError("Motivo no encontrado.")

    limpio = _validar_etiqueta(etiqueta)
    if _existe_duplicado(session, limpio, excluir_id=motivo.id):
        raise ValueError(f'Ya existe un motivo con el texto "{limpio}".')

    motivo.etiqueta = limpio
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ValueError(f'Ya existe un motivo con el texto "{limpio}".')
    return motivo


def eliminar_motivo(session: Session, motivo_id) -> None:
    """Borra un motivo (borrado duro -- no toca `PlantillaNotificacion` ni
    `Paquete` ya existentes, que conservan el texto que ya tenían).

    Raises:
        ValueError: si `motivo_id` no existe, o si es el último motivo que
            queda en el catálogo (cancelar un paquete exige un motivo
            obligatorio -- el picker nunca puede quedar sin opciones).
    """
    motivo = session.get(MotivoCancelacion, motivo_id)
    if motivo is None:
        raise ValueError("Motivo no encontrado.")

    total = session.query(MotivoCancelacion).count()
    if total <= 1:
        raise ValueError("No se puede borrar el último motivo de cancelación.")

    session.delete(motivo)
    session.flush()
