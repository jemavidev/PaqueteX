"""apartamentos + membresía actual de la Persona (Apartamento actual opcional)

DESCENDIENTE de la raíz `0001_baseline` (`down_revision = "0001_baseline"`). El
árbol permanece de raíz única (ADR-0002): esta migración cuelga de la baseline,
NO la edita. Añade:

  - la tabla ligera `apartamentos` (Conjunto → Torre → Apartamento) con unicidad
    sobre la terna normalizada `(conjunto, torre, apartamento)`;
  - la FK nullable `apartamento_actual_id` en `personas` (Apartamento actual,
    opcional y mutable).

Constraints con nombre explícito e IDÉNTICO al del ORM (`app.domain`) para que el
guard de paridad esquema↔ORM no reporte drift.

Revision ID: 0002_apartamentos
Revises: 0001_baseline
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0002_apartamentos"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "apartamentos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # Identidad de negocio: la terna normalizada (casing/espacios ya canónicos).
        sa.Column("conjunto", sa.String(length=120), nullable=False),
        sa.Column("torre", sa.String(length=60), nullable=False),
        sa.Column("apartamento", sa.String(length=60), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Dedup: misma terna → un solo Apartamento (get-or-create).
        sa.UniqueConstraint(
            "conjunto", "torre", "apartamento", name="uq_apartamentos_terna"
        ),
    )

    # Apartamento actual de la Persona: opcional (nullable) y mutable.
    op.add_column(
        "personas",
        sa.Column(
            "apartamento_actual_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_personas_apartamento_actual",
        "personas",
        "apartamentos",
        ["apartamento_actual_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_personas_apartamento_actual", "personas", type_="foreignkey"
    )
    op.drop_column("personas", "apartamento_actual_id")
    op.drop_table("apartamentos")
