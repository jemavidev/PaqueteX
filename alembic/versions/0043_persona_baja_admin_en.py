"""baja_administrativa_en en personas — baja reversible (.scratch/baja-administrativa)

DESCENDIENTE de `0042_indices_busqueda_paquetes`. El árbol permanece de raíz
única (ADR-0002). Añade `baja_administrativa_en` (timestamp, nullable) a
`personas`: marca una baja ADMINISTRATIVA reversible -- estado independiente
de `eliminado_en` (derecho al olvido, irreversible, ADR-0005). NUNCA toca
datos personales; la fila nunca se borra.

Revision ID: 0043_persona_baja_admin_en
Revises: 0042_indices_busqueda_paquetes
Create Date: 2026-09-07
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0043_persona_baja_admin_en"
down_revision = "0042_indices_busqueda_paquetes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "personas",
        sa.Column("baja_administrativa_en", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("personas", "baja_administrativa_en")
