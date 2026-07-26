"""cancel_reason en paquetes — motivo obligatorio de cancelación

DESCENDIENTE de `0003_paquetes` (`down_revision = "0003_paquetes"`). El árbol
permanece de raíz única (ADR-0002): añade la columna `cancel_reason` (VARCHAR
nullable) que la transición `cancel` llena con el motivo. Nombre de columna
idéntico al del ORM (`app.domain.paquete`) para que el guard de paridad no
reporte drift.

Revision ID: 0004_cancel_reason
Revises: 0003_paquetes
Create Date: 2026-07-24
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004_cancel_reason"
down_revision = "0003_paquetes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "paquetes",
        sa.Column("cancel_reason", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("paquetes", "cancel_reason")
