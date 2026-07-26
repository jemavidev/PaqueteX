"""baseline: personas — el Teléfono es la llave universal de la Persona

Raíz ÚNICA del árbol Alembic nuevo (ADR-0002). `down_revision = None` aparece
exactamente una vez en todo el árbol. Las rebanadas siguientes (apartamento,
paquete-snapshot, usuario, eventos, auth, fotos) añaden migraciones
DESCENDIENTES de esta raíz — el árbol permanece de raíz única.

Esta baseline construye SOLO `personas` (alcance del ticket 01).

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "personas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # La llave universal: Teléfono canónico, único y NOT NULL.
        sa.Column("telefono", sa.String(length=20), nullable=False),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        # Campos ampliables (nullable).
        sa.Column("email", sa.String(length=120), nullable=True),
        sa.Column("documento", sa.String(length=40), nullable=True),
        sa.Column("tipo_documento", sa.String(length=10), nullable=True),
        sa.Column("segundo_contacto", sa.String(length=120), nullable=True),
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
        sa.UniqueConstraint("telefono", name="uq_personas_telefono"),
    )


def downgrade() -> None:
    op.drop_table("personas")
