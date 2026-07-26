"""notificaciones_activas en personas — preferencia de notificaciones

DESCENDIENTE de `0007_persona_eliminado_en` (`down_revision =
"0007_persona_eliminado_en"`). El árbol permanece de raíz única (ADR-0002).
Añade `notificaciones_activas` (booleano, NOT NULL, default `True` — preserva
el comportamiento actual: todo residente recibe notificaciones salvo que las
desactive explícitamente).

Revision ID: 0008_persona_notif_activas
Revises: 0007_persona_eliminado_en
Create Date: 2026-07-26
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0008_persona_notif_activas"
down_revision = "0007_persona_eliminado_en"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "personas",
        sa.Column(
            "notificaciones_activas",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("personas", "notificaciones_activas")
