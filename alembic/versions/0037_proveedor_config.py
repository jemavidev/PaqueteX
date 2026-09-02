"""proveedores_notificacion_config(_historial) -- habilitado/orden por proveedor

DESCENDIENTE de `0036_usuario_telefono_whatsapp` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). `.scratch/administracion-proveedores/
spec.md`, issue 01: mueve "¿está habilitado este proveedor, en qué orden?"
de una constante fija en código a una tabla editable desde
`/administracion/proveedores` (issue 03) -- las credenciales en sí SIGUEN
solo en `.env` del servidor (Fase 2, issue 04/05), esta tabla nunca guarda
un secreto.

Siembra el estado que YA está en producción hoy (AWS SNS -> LIWA -> Twilio,
pedido explícito del cliente 2026-08-06, ver `.scratch/sms-failover-twilio-
sns/spec.md`; SMTP único proveedor de Email) -- desplegar esta migración
sola, sin el refactor del issue 02 que la conecta al envío real, no cambia
ningún comportamiento observable.

Revision ID: 0037_proveedor_config
Revises: 0036_usuario_telefono_whatsapp
Create Date: 2026-09-01
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0037_proveedor_config"
down_revision = "0036_usuario_telefono_whatsapp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proveedores_notificacion_config",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("canal", sa.String(length=20), nullable=False),
        sa.Column("proveedor", sa.String(length=30), nullable=False),
        sa.Column("habilitado", sa.Boolean(), nullable=False),
        sa.Column("orden", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.UniqueConstraint("canal", "proveedor", name="uq_proveedores_config_canal_proveedor"),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["usuarios.id"],
            name="fk_proveedores_config_updated_by",
        ),
    )

    op.create_table(
        "proveedores_notificacion_config_historial",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("canal", sa.String(length=20), nullable=False),
        sa.Column("proveedor", sa.String(length=30), nullable=False),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("habilitado_anterior", sa.Boolean(), nullable=True),
        sa.Column("habilitado_nuevo", sa.Boolean(), nullable=False),
        sa.Column("orden_anterior", sa.Integer(), nullable=True),
        sa.Column("orden_nuevo", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["usuario_id"],
            ["usuarios.id"],
            name="fk_proveedores_config_historial_usuario",
        ),
    )

    proveedores_config = sa.table(
        "proveedores_notificacion_config",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("canal", sa.String),
        sa.column("proveedor", sa.String),
        sa.column("habilitado", sa.Boolean),
        sa.column("orden", sa.Integer),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    ahora = datetime.now(timezone.utc)
    op.bulk_insert(
        proveedores_config,
        [
            {
                "id": uuid.uuid4(),
                "canal": "SMS",
                "proveedor": "AWS_SNS",
                "habilitado": True,
                "orden": 1,
                "updated_at": ahora,
            },
            {
                "id": uuid.uuid4(),
                "canal": "SMS",
                "proveedor": "LIWA",
                "habilitado": True,
                "orden": 2,
                "updated_at": ahora,
            },
            {
                "id": uuid.uuid4(),
                "canal": "SMS",
                "proveedor": "TWILIO",
                "habilitado": True,
                "orden": 3,
                "updated_at": ahora,
            },
            {
                "id": uuid.uuid4(),
                "canal": "EMAIL",
                "proveedor": "SMTP",
                "habilitado": True,
                "orden": None,
                "updated_at": ahora,
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("proveedores_notificacion_config_historial")
    op.drop_table("proveedores_notificacion_config")
