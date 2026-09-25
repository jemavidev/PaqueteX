"""operaciones_respaldo -- estado de las operaciones de la pantalla "Respaldos"

DESCENDIENTE de `0064_paquete_fotos_origen_v1` (`down_revision`). El árbol permanece de raíz única (ADR-0002).
`.scratch/respaldos-y-restauracion`, tickets 09-11: "Respaldar ahora" y la copia de fotos corren en segundo plano
y su estado (en curso / ok / falló, avance) debe sobrevivir a un reinicio de la app; las descargas de fotos dejan su
marca ("solo las nuevas" parte de la última).

Revision ID: 0065_operaciones_respaldo
Revises: 0064_paquete_fotos_origen_v1
Create Date: 2026-09-25
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0065_operaciones_respaldo"
down_revision = "0064_paquete_fotos_origen_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operaciones_respaldo",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("solicitado_por", sa.String(255), nullable=True),
        sa.Column("inicio", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fin", sa.DateTime(timezone=True), nullable=True),
        sa.Column("avance_actual", sa.Integer(), nullable=True),
        sa.Column("avance_total", sa.Integer(), nullable=True),
        sa.Column("detalle", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("operaciones_respaldo")
