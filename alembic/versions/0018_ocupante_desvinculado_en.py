"""ocupante_desvinculado_en — marcado histórico de "dar de baja" (.scratch/mis-datos, ticket 02)

DESCENDIENTE de `0017_normalizar_casing_nombres` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). Agrega `desvinculado_en` (nullable) a
`ocupantes`: un Ocupante dado de baja NUNCA se borra (mismo espíritu que
`anonimizar_persona`/ADR-0001, nunca reescribir/borrar historia real) — solo
se marca. `NULL` = activo; con fecha = dado de baja en ese instante, sus
datos quedan de solo consulta (ver `ocupante_service.dar_de_baja_ocupante`).

Revision ID: 0018_ocupante_desvinculado_en
Revises: 0017_normalizar_casing_nombres
Create Date: 2026-08-03
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0018_ocupante_desvinculado_en"
down_revision = "0017_normalizar_casing_nombres"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ocupantes",
        sa.Column("desvinculado_en", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ocupantes", "desvinculado_en")
