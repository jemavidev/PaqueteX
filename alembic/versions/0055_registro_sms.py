"""registro_sms -- registro append-only de envíos SMS, con el proveedor

DESCENDIENTE de `0054_fuentes_contactos_externos` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). `.scratch/estadisticas-cobro-dashboard`,
ticket 11: crea `registros_sms`, append-only (ningún update/delete) -- sin
texto del mensaje ni teléfono completo, solo lo necesario para las cuentas
del tablero de estadísticas de cobro (tickets 14-16). `tipo`/`evento` son
`String` simples (mismo criterio que `paquetes.package_type`): un valor
nuevo del enum Python (ticket 12: OTP/PRUEBA) no exige otra migración.

Revision ID: 0055_registro_sms
Revises: 0054_fuentes_contactos_externos
Create Date: 2026-09-20
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0055_registro_sms"
down_revision = "0054_fuentes_contactos_externos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registros_sms",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("evento", sa.String(length=20), nullable=True),
        sa.Column("paquete_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("proveedor", sa.String(length=30), nullable=True),
        sa.Column("exitoso", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["paquete_id"], ["paquetes.id"], name="fk_registros_sms_paquete"
        ),
    )
    op.create_index(
        "ix_registros_sms_created_at", "registros_sms", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_registros_sms_created_at", table_name="registros_sms")
    op.drop_table("registros_sms")
