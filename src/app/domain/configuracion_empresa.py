# -*- coding: utf-8 -*-
"""
ConfiguracionEmpresa — datos legales de la empresa que OPERA PaqueteX
(razón social, NIT, dirección, contacto), singleton (rebuild PaqueteXv.2,
grilling 2026-09-18, issue 350).

Distinta a `ConfiguracionConjunto` a propósito: el Conjunto es el edificio
residencial atendido (su nombre agrupa `Apartamento`s); esto es la empresa
responsable del tratamiento de datos ante la Ley 1581 de 2012 (hoy Papyrus
Soluciones Integrales S.A.S.) -- mezclarlas en una sola tabla ensuciaría el
significado de ambas, aunque en este despliegue compartan pantalla de
administración y hasta la misma dirección física. Mismo patrón singleton
que `ConfiguracionConjunto`/`TarifaCobro`: PK fija, tabla de override.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID

from .base import Base

ID_SINGLETON = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConfiguracionEmpresa(Base):
    __tablename__ = "configuracion_empresa"

    id = Column(UUID(as_uuid=True), primary_key=True, default=lambda: ID_SINGLETON)
    razon_social = Column(String(200), nullable=False)
    # NULLABLE a propósito -- el NIT quedó pendiente de confirmar en el
    # grilling de las páginas legales (`/privacidad` lo mostraba como
    # placeholder "(por confirmar)"); el formulario debe poder guardar el
    # resto de los campos sin bloquear por este.
    nit = Column(String(50), nullable=True)
    direccion = Column(String(300), nullable=True)
    email_contacto = Column(String(200), nullable=True)
    telefono_contacto = Column(String(50), nullable=True)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<ConfiguracionEmpresa razon_social={self.razon_social!r}>"
