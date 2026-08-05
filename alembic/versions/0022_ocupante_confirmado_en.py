"""ocupante_confirmado_en — confirmación de Ocupante (.scratch/apartamento-catalogo-confirmacion, ticket 06)

DESCENDIENTE de `0021_seed_catalogo_apartamentos` (`down_revision`). El
árbol permanece de raíz única (ADR-0002). Agrega `confirmado_en` (nullable)
a `ocupantes` -- mismo patrón que `desvinculado_en` (0018): `NULL` = pending
(sin verificar todavía), con fecha = confirmado en ese instante. Ver
`ocupante_service.confirmar_ocupante`.

Revision ID: 0022_ocupante_confirmado_en
Revises: 0021_seed_catalogo_apartamentos
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0022_ocupante_confirmado_en"
down_revision = "0021_seed_catalogo_apartamentos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ocupantes",
        sa.Column("confirmado_en", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ocupantes", "confirmado_en")
