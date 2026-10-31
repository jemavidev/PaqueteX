"""fotos_pendientes -- el servidor sabe qué fotos de Recibir siguen en la cola de un equipo

DESCENDIENTE de `0071_envios_aviso_especial` (`down_revision`). El árbol permanece de raíz única (ADR-0002).
Diagnóstico de fotos perdidas (cola del equipo que expiraba a las 24 h): `paquetes.fotos_pendientes` cuenta las
fotos que el equipo dejó en su cola al confirmar "Recibir" y `paquetes.recibido_sin_fotos` marca un paquete
recibido sin fotos a propósito. `server_default` para que las filas existentes queden en 0 / false.

Revision ID: 0072_fotos_pendientes
Revises: 0071_envios_aviso_especial
Create Date: 2026-10-30
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0072_fotos_pendientes"
down_revision = "0071_envios_aviso_especial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "paquetes",
        sa.Column("fotos_pendientes", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "paquetes",
        sa.Column("recibido_sin_fotos", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("paquetes", "recibido_sin_fotos")
    op.drop_column("paquetes", "fotos_pendientes")
