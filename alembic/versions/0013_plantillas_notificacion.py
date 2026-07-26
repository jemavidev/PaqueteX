"""plantillas_notificacion — textos de mensaje editables por evento/motivo

DESCENDIENTE de `0012_tipo_condicion_fotos` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). Grupo 8 de
`ajustes-post-referencia-funcional/REQUERIMIENTOS.md`: tabla de override —
si no hay fila para un `(evento, motivo)`, `construir_mensaje` usa el texto
por defecto hardcodeado (sin cambios).

Revision ID: 0013_plantillas_notificacion
Revises: 0012_tipo_condicion_fotos
Create Date: 2026-07-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0013_plantillas_notificacion"
down_revision = "0012_tipo_condicion_fotos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plantillas_notificacion",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("evento", sa.String(length=20), nullable=False),
        sa.Column("motivo", sa.String(length=40), nullable=True),
        sa.Column("texto", sa.String(length=500), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "evento", "motivo", name="uq_plantillas_notificacion_evento_motivo"
        ),
    )


def downgrade() -> None:
    op.drop_table("plantillas_notificacion")
