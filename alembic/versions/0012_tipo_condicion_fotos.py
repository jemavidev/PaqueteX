"""package_type/package_condition en paquetes + tabla paquete_fotos

DESCENDIENTE de `0011_corregir_destinatario` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). Grupo 2 de
`ajustes-post-referencia-funcional/REQUERIMIENTOS.md`: tipo y condición del
paquete físico, capturados al RECIBIR (no al anunciar, por eso ambas columnas
son nullable), mismas categorías que el sistema legacy
(`app/models/package.py`). `paquete_fotos` guarda cero o más fotos por
Paquete.

Revision ID: 0012_tipo_condicion_fotos
Revises: 0011_corregir_destinatario
Create Date: 2026-07-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0012_tipo_condicion_fotos"
down_revision = "0011_corregir_destinatario"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "paquetes",
        sa.Column("package_type", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "paquetes",
        sa.Column("package_condition", sa.String(length=20), nullable=True),
    )
    op.create_table(
        "paquete_fotos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("paquete_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["paquete_id"], ["paquetes.id"], name="fk_paquete_fotos_paquete"
        ),
    )


def downgrade() -> None:
    op.drop_table("paquete_fotos")
    op.drop_column("paquetes", "package_condition")
    op.drop_column("paquetes", "package_type")
