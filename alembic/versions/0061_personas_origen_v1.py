"""personas.origen_v1_id -- vínculo con el cliente de la v1 (importador espejo)

DESCENDIENTE de `0060_usuario_sesion_version` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). `.scratch/importador-v1-espejo`, ticket 01:
el importador espejo v1 → v2 identifica cada Persona importada por el id del
`customers` de la v1 para ser idempotente. Nullable (las Personas nativas de la
v2 no lo tienen) y único cuando no es nulo. Permanente: se queda tras el corte
para rastreo, nunca se muestra en pantalla.

Revision ID: 0061_personas_origen_v1
Revises: 0060_usuario_sesion_version
Create Date: 2026-09-23
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0061_personas_origen_v1"
down_revision = "0060_usuario_sesion_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("personas", sa.Column("origen_v1_id", sa.String(64), nullable=True))
    op.create_index(
        "uq_personas_origen_v1_id",
        "personas",
        ["origen_v1_id"],
        unique=True,
        postgresql_where=sa.text("origen_v1_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_personas_origen_v1_id", table_name="personas")
    op.drop_column("personas", "origen_v1_id")
