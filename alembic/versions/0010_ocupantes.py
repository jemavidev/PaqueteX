"""ocupantes — residentes de un Apartamento con Teléfono opcional (ADR-0006)

DESCENDIENTE de `0009_eliminar_tracking_number` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). Implementa la entidad Ocupante resuelta en
`CONTEXT.md` y `docs/adr/0006-ocupante-residentes-sin-persona-propia.md`:
`persona_id` nullable (NULL cuando el Ocupante no tiene Teléfono propio), y un
índice único parcial que garantiza máximo 1 Ocupante `es_principal` por
Apartamento a nivel de base de datos.

Revision ID: 0010_ocupantes
Revises: 0009_eliminar_tracking_number
Create Date: 2026-07-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0010_ocupantes"
down_revision = "0009_eliminar_tracking_number"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ocupantes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("apartamento_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("persona_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column(
            "es_principal", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["apartamento_id"], ["apartamentos.id"], name="fk_ocupantes_apartamento"
        ),
        sa.ForeignKeyConstraint(
            ["persona_id"], ["personas.id"], name="fk_ocupantes_persona"
        ),
    )
    op.create_index(
        "uq_ocupantes_principal_por_apartamento",
        "ocupantes",
        ["apartamento_id"],
        unique=True,
        postgresql_where=sa.text("es_principal"),
    )


def downgrade() -> None:
    op.drop_index("uq_ocupantes_principal_por_apartamento", table_name="ocupantes")
    op.drop_table("ocupantes")
