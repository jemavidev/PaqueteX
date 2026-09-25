# -*- coding: utf-8 -*-
"""
Operaciones de la pantalla "Respaldos" que corren en segundo plano (`.scratch/respaldos-y-restauracion`, tickets
09-11): "Respaldar ahora" y la copia de fotos al servidor, más el registro de cada descarga de fotos (la "última
descarga", por sistema, de la que parte "solo las nuevas").

Se guardan en la base (no en memoria) para que el estado sobreviva a un reinicio de la app.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TipoOperacion(str, enum.Enum):
    RESPALDO = "respaldo"
    COPIA_FOTOS = "copia_fotos"
    DESCARGA_FOTOS = "descarga_fotos"


class EstadoOperacion(str, enum.Enum):
    EN_CURSO = "en_curso"
    OK = "ok"
    FALLO = "fallo"


class OperacionRespaldo(Base):
    __tablename__ = "operaciones_respaldo"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tipo = Column(String(20), nullable=False)
    estado = Column(String(20), nullable=False)
    solicitado_por = Column(String(255), nullable=True)
    inicio = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    fin = Column(DateTime(timezone=True), nullable=True)
    avance_actual = Column(Integer, nullable=True)
    avance_total = Column(Integer, nullable=True)
    detalle = Column(Text, nullable=True)
