"""plantillas_notificacion_historial -- auditoría append-only de guardados

DESCENDIENTE de `0033_plantilla_multicanal` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). `.scratch/plantillas-notificacion-
multicanal`, ticket 04: cada guardado exitoso de `PlantillaNotificacion`
(ticket 01) deja un registro aparte en esta tabla -- nunca se UPDATE ni
DELETE, solo INSERT. `evento`/`motivo`/`canal` quedan denormalizados para
poder consultar sin JOIN.

Revision ID: 0034_plantilla_historial
Revises: 0033_plantilla_multicanal
Create Date: 2026-08-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0034_plantilla_historial"
down_revision = "0033_plantilla_multicanal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plantillas_notificacion_historial",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("plantilla_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evento", sa.String(length=20), nullable=False),
        sa.Column("motivo", sa.String(length=40), nullable=True),
        sa.Column("canal", sa.String(length=20), nullable=False),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("texto_anterior", sa.String(length=500), nullable=True),
        sa.Column("texto_nuevo", sa.String(length=500), nullable=False),
        sa.Column("asunto_anterior", sa.String(length=200), nullable=True),
        sa.Column("asunto_nuevo", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["plantilla_id"],
            ["plantillas_notificacion.id"],
            name="fk_plantillas_notificacion_historial_plantilla",
        ),
        sa.ForeignKeyConstraint(
            ["usuario_id"],
            ["usuarios.id"],
            name="fk_plantillas_notificacion_historial_usuario",
        ),
    )


def downgrade() -> None:
    op.drop_table("plantillas_notificacion_historial")
