# -*- coding: utf-8 -*-
"""
FilaEstanteDesactivada — filas del estante que el ADMIN desactivó (issue 416, `.scratch/pendientes-cliente`).

Solo se guardan las DESACTIVADAS: sin fila acá, la fila está activa (un estante recién instalado tiene las 7). Una fila
es sus dos lados (x1 y x2, `posicion.py`). Reglas en `posicion_service.py`.
"""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Column, DateTime, SmallInteger

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FilaEstanteDesactivada(Base):
    __tablename__ = "filas_estante_desactivadas"

    __table_args__ = (
        CheckConstraint("fila BETWEEN 1 AND 7", name="ck_filas_estante_desactivadas_fila"),
    )

    fila = Column(SmallInteger, primary_key=True, autoincrement=False)
    desactivada_en = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        return f"<FilaEstanteDesactivada fila={self.fila}>"
