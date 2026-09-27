"""filas_estante_desactivadas -- filas del estante que el ADMIN desactivó

DESCENDIENTE de `0066_paquete_posicion` (`down_revision`). El árbol permanece de raíz única (ADR-0002).
Issue 416 (`.scratch/pendientes-cliente`): Administración → Posiciones. Solo se guardan las filas DESACTIVADAS; sin
filas, las 7 están activas (sin datos semilla).

Revision ID: 0067_filas_estante_desactivadas
Revises: 0066_paquete_posicion
Create Date: 2026-09-27
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0067_filas_estante_desactivadas"
down_revision = "0066_paquete_posicion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "filas_estante_desactivadas",
        sa.Column("fila", sa.SmallInteger(), primary_key=True, autoincrement=False),
        sa.Column("desactivada_en", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fila BETWEEN 1 AND 7", name="ck_filas_estante_desactivadas_fila"),
    )


def downgrade() -> None:
    op.drop_table("filas_estante_desactivadas")
