# -*- coding: utf-8 -*-
"""
Filas del estante activas/desactivadas (issue 416, `.scratch/pendientes-cliente`).

El ADMIN, desde Administración → Posiciones, decide qué filas del estante se pueden usar. Una Posición de una fila
desactivada no se ofrece en el modal Recibir y la ruta de Recibir la rechaza. Los paquetes que YA están en esa fila no
cambian. La única regla: nunca quedarse sin ninguna fila activa -- la Posición es obligatoria al recibir
(`.scratch/posicion-almacenamiento`), así que sin filas no se podría recibir nada.
"""

from sqlalchemy.orm import Session

from .fila_estante import FilaEstanteDesactivada
from .posicion import FILAS


class SinFilasActivas(ValueError):
    def __init__(self):
        super().__init__("Debe quedar al menos una fila activa: la posición es obligatoria al recibir.")


def filas_desactivadas(session: Session) -> frozenset[int]:
    return frozenset(f for (f,) in session.query(FilaEstanteDesactivada.fila))


def guardar_filas_activas(session: Session, activas: set[int]) -> None:
    """Deja activas EXACTAMENTE `activas` (números de fila 1..FILAS) y desactiva las demás.

    Raises:
        SinFilasActivas: si `activas` no deja ninguna fila válida activa (sin efecto).
    """
    activas = {f for f in activas if 1 <= f <= FILAS}
    if not activas:
        raise SinFilasActivas()
    desactivar = set(range(1, FILAS + 1)) - activas
    actuales = filas_desactivadas(session)
    for fila in actuales - desactivar:
        session.query(FilaEstanteDesactivada).filter(FilaEstanteDesactivada.fila == fila).delete()
    for fila in desactivar - actuales:
        session.add(FilaEstanteDesactivada(fila=fila))
    session.flush()


def posicion_habilitada(session: Session, posicion: str) -> bool:
    """¿Se puede elegir hoy esta Posición (ya validada, `posicion.py`) al Recibir?"""
    return int(posicion[0]) not in filas_desactivadas(session)
