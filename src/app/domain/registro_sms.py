# -*- coding: utf-8 -*-
"""
RegistroSms — registro append-only de cada intento REAL de envío de SMS,
con el proveedor que lo entregó (módulo "Registro de envíos SMS",
`.scratch/estadisticas-cobro-dashboard`, ticket 11 -- sin este registro
ninguna tarjeta de SMS del tablero de estadísticas de cobro, tickets 14-16,
es posible: hoy los remitentes solo devuelven éxito o excepción, sin dejar
ningún rastro de CUÁL proveedor entregó cuando hay más de uno en la cadena
de failover).

Deliberadamente MÍNIMO -- nunca guarda el texto del mensaje ni el teléfono
completo del destinatario (issue de privacidad explícita del ticket): solo
lo necesario para las cuentas del tablero (por proveedor, por tipo, por
rango de fechas).

`tipo` arrancó con un solo valor (`AVISO_PAQUETE`, ticket 11); el ticket 12
agrega `OTP` (código de acceso al iniciar sesión) -- un valor nuevo del
enum Python, SIN migración, porque `native_enum=False` guarda la columna
como `VARCHAR` simple (mismo criterio que `Paquete.package_type`/
`TipoPaquete`), nunca un tipo ENUM nativo de Postgres que exigiría `ALTER
TYPE` para crecer. El mensaje de PRUEBA que un ADMIN se manda desde
`/administracion/notificaciones` NO es un tercer valor -- cuenta como
`AVISO_PAQUETE` (para el conteo del tablero), simplemente sin `paquete_id`
(spec.md, ticket 12: "una prueba no tiene paquete real").

`evento`/`paquete_id` son nullable a propósito: solo los llena un aviso
real de una transición de Paquete -- ni `OTP` ni un mensaje de prueba
tienen paquete/evento detrás. `proveedor` es `None` únicamente cuando los
tres proveedores configurados fallaron (`exitoso=False`) -- nunca cuando
SÍ hubo entrega.

Append-only, igual que `Cobro`/`MovimientoSaldoContraEntrega`: ninguna ruta
edita ni borra una fila ya creada. Por eso no tiene `updated_at`.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKeyConstraint, Index, String
from sqlalchemy.dialects.postgresql import UUID

from .base import Base
from .paquete import EstadoPaquete


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TipoRegistroSms(str, enum.Enum):
    AVISO_PAQUETE = "AVISO_PAQUETE"
    # Código de acceso (OTP) al iniciar sesión en `/otp/solicitar` -- ticket
    # 12. Nunca lleva `evento`/`paquete_id`.
    OTP = "OTP"


class RegistroSms(Base):
    __tablename__ = "registros_sms"

    __table_args__ = (
        # Issue 380: SET NULL -- borrar un Paquete Anunciado conserva el registro (y el costo) de su SMS.
        ForeignKeyConstraint(
            ["paquete_id"], ["paquetes.id"], name="fk_registros_sms_paquete", ondelete="SET NULL"
        ),
        Index("ix_registros_sms_created_at", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tipo = Column(Enum(TipoRegistroSms, native_enum=False, length=20), nullable=False)
    # Mismo enum que `Paquete.estado` -- el evento que disparó el aviso
    # (ANUNCIADO/RECIBIDO/ENTREGADO/CANCELADO), nunca uno propio duplicado.
    evento = Column(Enum(EstadoPaquete, native_enum=False, length=20), nullable=True)
    paquete_id = Column(UUID(as_uuid=True), nullable=True)
    # Clave del catálogo (`proveedores_catalogo.py`: "AWS_SNS"/"LIWA"/
    # "TWILIO") -- texto plano, no FK, mismo criterio que `ProveedorConfig.
    # proveedor`.
    proveedor = Column(String(30), nullable=True)
    exitoso = Column(Boolean, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        return (
            f"<RegistroSms tipo={self.tipo!r} evento={self.evento!r} "
            f"proveedor={self.proveedor!r} exitoso={self.exitoso!r}>"
        )
