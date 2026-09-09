# -*- coding: utf-8 -*-
"""
Cobro — registro inmutable de lo cobrado (o anulado) al Entregar un Paquete
(módulo "Gestión de cobro y bodegaje", `.scratch/cobro-bodegaje`).

Relación 1↔1 con `Paquete` (constraint único sobre `paquete_id`): existe
únicamente si el paquete llegó a `ENTREGADO` -- se crea en la misma
transacción que `paquete_lifecycle.deliver()` (ver
`packages.py::deliver_action`). Append-only: ninguna ruta edita ni borra un
`Cobro` ya creado (decisión explícita del cliente, sesión de `/grilling`) --
un error se acepta tal cual, sin mecanismo de corrección. Por eso no tiene
`updated_at`.

`monto_base`/`bloques_bodegaje`/`monto_bodegaje` son un SNAPSHOT del cálculo
en el momento de cobrar (`cobro_service.calcular_cobro`) -- cambiar
`TarifaCobro` después nunca reescribe un `Cobro` ya registrado.

`motivo_anulacion` es texto copiado (no FK), mismo criterio que
`Paquete.cancel_reason`: solo se llena cuando el staff anuló el cobro
completo a "$0" eligiendo un motivo de `MotivoAnulacionCobro` -- nunca
cuando el monto dio $0 porque el cálculo lo eximió (primera entrega), para
poder distinguir ambos casos en el dato en vez de inferirlo del monto.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Cobro(Base):
    __tablename__ = "cobros"

    __table_args__ = (
        UniqueConstraint("paquete_id", name="uq_cobros_paquete_id"),
        ForeignKeyConstraint(["paquete_id"], ["paquetes.id"], name="fk_cobros_paquete"),
        ForeignKeyConstraint(
            ["cobrado_por_usuario_id"], ["usuarios.id"], name="fk_cobros_cobrado_por_usuario"
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paquete_id = Column(UUID(as_uuid=True), nullable=False)
    monto_base = Column(Integer, nullable=False)
    bloques_bodegaje = Column(Integer, nullable=False, default=0)
    monto_bodegaje = Column(Integer, nullable=False, default=0)
    monto_total = Column(Integer, nullable=False)
    # Solo no-nulo cuando el staff anuló explícitamente a "$0" -- ver docstring.
    motivo_anulacion = Column(String(40), nullable=True)
    cobrado_por_usuario_id = Column(UUID(as_uuid=True), nullable=False)
    cobrado_en = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        return (
            f"<Cobro paquete_id={self.paquete_id} monto_total={self.monto_total} "
            f"motivo_anulacion={self.motivo_anulacion!r}>"
        )
