"""paquetes.origen_v1_id -- vínculo con el paquete (o anuncio) de la v1

DESCENDIENTE de `0061_personas_origen_v1` (`down_revision`). El árbol permanece
de raíz única (ADR-0002). `.scratch/importador-v1-espejo`, ticket 02: el
importador espejo identifica cada Paquete importado por el id del `packages` de
la v1, o por `anuncio:<id>` si todavía es un anuncio de la v1 sin paquete.
Nullable (paquetes nativos) y único cuando no es nulo.

Revision ID: 0062_paquetes_origen_v1
Revises: 0061_personas_origen_v1
Create Date: 2026-09-23
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0062_paquetes_origen_v1"
down_revision = "0061_personas_origen_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paquetes", sa.Column("origen_v1_id", sa.String(64), nullable=True))
    op.create_index(
        "uq_paquetes_origen_v1_id",
        "paquetes",
        ["origen_v1_id"],
        unique=True,
        postgresql_where=sa.text("origen_v1_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_paquetes_origen_v1_id", table_name="paquetes")
    op.drop_column("paquetes", "origen_v1_id")
