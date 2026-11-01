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
from .paquete import EstadoPaquete, Paquete
from .posicion import FILAS, POSICION_PISO, normalizar_posicion


class SinFilasActivas(ValueError):
    def __init__(self):
        super().__init__("Debe quedar al menos una fila activa: la posición es obligatoria al recibir.")


class PosicionNoCambiable(ValueError):
    """Cambiar posición rechazado (desactivada, vacía o el paquete no está Recibido). El mensaje es para el Operador."""


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
    """¿Se puede elegir hoy esta Posición (ya validada, `posicion.py`) al Recibir o al cambiarla? El piso
    (`POSICION_PISO`) no es una fila: siempre."""
    if posicion == POSICION_PISO:
        return True
    return int(posicion[0]) not in filas_desactivadas(session)


def cambiar_posicion(session: Session, paquete: Paquete, valor: str | None) -> str:
    """Mueve un paquete RECIBIDO a otra Posición del estante (o al piso) -- pedido del cliente 2026-10-31, desde el
    chip 📍. Mismas reglas que al Recibir (válida y habilitada); sin historial del cambio (pedido explícito).

    Returns:
        La Posición guardada.

    Raises:
        PosicionInvalida (`posicion.py`): si no es una del estante.
        PosicionNoCambiable: vacía, de una fila desactivada, o el paquete no está Recibido (sin efecto).
    """
    if paquete.estado != EstadoPaquete.RECIBIDO:
        raise PosicionNoCambiable("Solo se puede cambiar la posición de un paquete recibido.")
    posicion = normalizar_posicion(valor)
    if posicion is None:
        raise PosicionNoCambiable("Elige la posición del estante donde está el paquete.")
    if not posicion_habilitada(session, posicion):
        raise PosicionNoCambiable(f"La posición «{posicion}» está desactivada.")
    paquete.posicion = posicion
    session.flush()
    return posicion
