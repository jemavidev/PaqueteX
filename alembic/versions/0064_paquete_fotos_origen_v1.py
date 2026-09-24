"""paquete_fotos.origen_v1_id -- vínculo con el `file_uploads` de la v1

DESCENDIENTE de `0063_usuarios_origen_v1` (`down_revision`). El árbol permanece
de raíz única (ADR-0002). `.scratch/importador-v1-espejo`, ticket 06: el
importador espejo copia cada foto de la v1 al bucket de la v2 una sola vez y la
identifica por el id de `file_uploads`. Nullable y único cuando no es nulo.

Revision ID: 0064_paquete_fotos_origen_v1
Revises: 0063_usuarios_origen_v1
Create Date: 2026-09-23
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0064_paquete_fotos_origen_v1"
down_revision = "0063_usuarios_origen_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paquete_fotos", sa.Column("origen_v1_id", sa.String(64), nullable=True))
    op.create_index(
        "uq_paquete_fotos_origen_v1_id",
        "paquete_fotos",
        ["origen_v1_id"],
        unique=True,
        postgresql_where=sa.text("origen_v1_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_paquete_fotos_origen_v1_id", table_name="paquete_fotos")
    op.drop_column("paquete_fotos", "origen_v1_id")
