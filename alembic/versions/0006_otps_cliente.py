"""otps_cliente — OTP de verificación de teléfono para clientes

DESCENDIENTE de `0005_usuario_credenciales` (`down_revision =
"0005_usuario_credenciales"`). El árbol permanece de raíz única (ADR-0002). Crea
`otps_cliente`: `telefono` (indexado, NO único), `codigo_hash` (nunca en claro),
`intentos`/`max_intentos`, `expira_en`, `verificado_en` (nullable). Índice de
nombre idéntico al del ORM (`ix_otps_cliente_telefono`) para la paridad esquema↔ORM.

Revision ID: 0006_otps_cliente
Revises: 0005_usuario_credenciales
Create Date: 2026-07-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0006_otps_cliente"
down_revision = "0005_usuario_credenciales"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "otps_cliente",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("telefono", sa.String(length=20), nullable=False),
        sa.Column("codigo_hash", sa.String(length=255), nullable=False),
        sa.Column("intentos", sa.Integer(), nullable=False),
        sa.Column("max_intentos", sa.Integer(), nullable=False),
        sa.Column("expira_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verificado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_otps_cliente_telefono", "otps_cliente", ["telefono"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_otps_cliente_telefono", table_name="otps_cliente")
    op.drop_table("otps_cliente")
