# -*- coding: utf-8 -*-
"""
Estado de las operaciones en segundo plano de la pantalla "Respaldos" (`.scratch/respaldos-y-restauracion`, tickets
09-11). La ruta web la inicia (una sola en curso por tipo) y el proceso que la ejecuta la termina con su resultado.

Si el proceso muere sin terminarla (ej. un reinicio del contenedor), queda "en curso" para siempre: pasado su tiempo
máximo se trata como interrumpida y deja lanzar otra.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .operacion_respaldo import EstadoOperacion, OperacionRespaldo, TipoOperacion

# Cuánto puede durar una operación antes de darla por interrumpida. La primera copia de fotos (varios GB) es la larga.
DURACION_MAXIMA = {
    TipoOperacion.RESPALDO: timedelta(minutes=30),
    TipoOperacion.COPIA_FOTOS: timedelta(hours=6),
    TipoOperacion.DESCARGA_FOTOS: timedelta(hours=6),
}


class OperacionEnCurso(Exception):
    """Ya hay una operación de ese tipo corriendo."""


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def ultima_operacion(session: Session, tipo: TipoOperacion) -> OperacionRespaldo | None:
    return (
        session.query(OperacionRespaldo)
        .filter(OperacionRespaldo.tipo == tipo.value)
        .order_by(OperacionRespaldo.inicio.desc())
        .first()
    )


def en_curso(operacion: OperacionRespaldo | None, ahora: datetime | None = None) -> bool:
    if operacion is None or operacion.estado != EstadoOperacion.EN_CURSO.value:
        return False
    return (ahora or _ahora()) - operacion.inicio < DURACION_MAXIMA[TipoOperacion(operacion.tipo)]


def iniciar_operacion(session: Session, tipo: TipoOperacion, solicitado_por: str) -> OperacionRespaldo:
    if en_curso(ultima_operacion(session, tipo)):
        raise OperacionEnCurso(tipo.value)
    operacion = OperacionRespaldo(
        tipo=tipo.value, estado=EstadoOperacion.EN_CURSO.value, solicitado_por=solicitado_por, inicio=_ahora()
    )
    session.add(operacion)
    session.flush()
    return operacion


def terminar_operacion(session: Session, operacion_id: uuid.UUID, ok: bool, detalle: str | None = None) -> None:
    operacion = session.get(OperacionRespaldo, operacion_id)
    if operacion is None:
        return
    operacion.estado = (EstadoOperacion.OK if ok else EstadoOperacion.FALLO).value
    operacion.detalle = detalle
    operacion.fin = _ahora()
    session.flush()


def registrar_avance(session: Session, operacion_id: uuid.UUID, actual: int, total: int) -> None:
    operacion = session.get(OperacionRespaldo, operacion_id)
    if operacion is not None:
        operacion.avance_actual, operacion.avance_total = actual, total
        session.flush()
