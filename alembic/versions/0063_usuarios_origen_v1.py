"""usuarios.origen_v1_id -- vínculo con el usuario (username) de la v1

DESCENDIENTE de `0062_paquetes_origen_v1` (`down_revision`). El árbol permanece
de raíz única (ADR-0002). `.scratch/importador-v1-espejo`, ticket 03: el
importador espejo enlaza cada `changed_by` del historial de la v1 a un Usuario
de la v2 -- uno existente (por email) o uno inactivo creado para conservar la
autoría. `operator_1` es el Usuario técnico "Operador v1 (sin identificar)".
Nullable y único cuando no es nulo.

Revision ID: 0063_usuarios_origen_v1
Revises: 0062_paquetes_origen_v1
Create Date: 2026-09-23
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0063_usuarios_origen_v1"
down_revision = "0062_paquetes_origen_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("usuarios", sa.Column("origen_v1_id", sa.String(64), nullable=True))
    op.create_index(
        "uq_usuarios_origen_v1_id",
        "usuarios",
        ["origen_v1_id"],
        unique=True,
        postgresql_where=sa.text("origen_v1_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_usuarios_origen_v1_id", table_name="usuarios")
    op.drop_column("usuarios", "origen_v1_id")
