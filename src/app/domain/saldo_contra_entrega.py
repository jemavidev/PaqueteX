# -*- coding: utf-8 -*-
"""
MovimientoSaldoContraEntrega — registro append-only de depósitos y pagos del
saldo a favor de una Persona, usado para paquetes "pago contra entrega"
(módulo "Gestión de dinero contra entrega", `.scratch/dinero-contra-entrega`).

`monto` es un entero CON SIGNO: positivo suma al saldo (depósito, pago del
residente al recuperar una deuda), negativo resta (pago a un mensajero,
vuelta entregada). El saldo de una Persona es la SUMA de todos sus
movimientos (`saldo_contra_entrega_service.saldo_de_persona`) -- no hay
ninguna columna desnormalizada en `Persona` que se pueda desincronizar.

Append-only: ninguna ruta edita ni borra un movimiento ya creado (mismo
criterio de auditoría que `Cobro` del módulo de cobro/bodegaje) -- un error
se corrige con un movimiento nuevo que lo compense, nunca reescribiendo el
historial. Por eso no tiene `updated_at`.

El saldo pertenece a la Persona que lo deposita, pero es utilizable para
pagar el contra entrega de cualquier residente de su mismo apartamento --
decisión explícita del cliente (ver `personas_con_historial_en_apartamento`).
`paquete_id` es nullable: un depósito puede no estar asociado a ningún
paquete todavía.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKeyConstraint, Index, Integer
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MovimientoSaldoContraEntrega(Base):
    __tablename__ = "movimientos_saldo_contra_entrega"

    __table_args__ = (
        ForeignKeyConstraint(["persona_id"], ["personas.id"], name="fk_movimientos_saldo_persona"),
        ForeignKeyConstraint(["paquete_id"], ["paquetes.id"], name="fk_movimientos_saldo_paquete"),
        ForeignKeyConstraint(
            ["registrado_por_usuario_id"],
            ["usuarios.id"],
            name="fk_movimientos_saldo_registrado_por",
        ),
        Index("ix_movimientos_saldo_persona_id", "persona_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    persona_id = Column(UUID(as_uuid=True), nullable=False)
    monto = Column(Integer, nullable=False)
    paquete_id = Column(UUID(as_uuid=True), nullable=True)
    registrado_por_usuario_id = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        return f"<MovimientoSaldoContraEntrega persona_id={self.persona_id} monto={self.monto}>"
