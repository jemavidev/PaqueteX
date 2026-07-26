# -*- coding: utf-8 -*-
"""
OtpCliente — código de verificación de teléfono para clientes (rebuild PaqueteXv.2).

Un residente pide un código de 6 dígitos a su Teléfono y lo confirma para
verificar que es su dueño. El código se guarda SOLO hasheado (nunca en claro,
mismo principio que las contraseñas de staff). `telefono` NO es único: un mismo
teléfono puede pedir varios OTPs en el tiempo; el vigente es el más reciente que
no esté verificado, no haya expirado y no haya agotado sus intentos.

Referencia de forma (no de esquema): el `CustomerOTP` del modelo viejo confirma
que código+intentos+expiración es el diseño de dominio correcto — pero aquí la
expiración se CALCULA contra `expira_en` en cada verificación, nunca se cachea en
un booleano (`is_expired`) que podría desincronizarse.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import Index

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OtpCliente(Base):
    __tablename__ = "otps_cliente"

    __table_args__ = (
        Index("ix_otps_cliente_telefono", "telefono"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # NO único: un teléfono puede pedir varios OTPs a lo largo del tiempo.
    telefono = Column(String(20), nullable=False)

    # El código NUNCA se guarda en claro.
    codigo_hash = Column(String(255), nullable=False)

    intentos = Column(Integer, nullable=False, default=0)
    max_intentos = Column(Integer, nullable=False, default=5)

    expira_en = Column(DateTime(timezone=True), nullable=False)
    # Marca de consumo: no nulo = ya se usó para abrir sesión (no reutilizable).
    verificado_en = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        return f"<OtpCliente id={self.id} telefono={self.telefono!r}>"
