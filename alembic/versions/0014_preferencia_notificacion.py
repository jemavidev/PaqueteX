"""persona_preferencia_notificacion — matriz Canal x Evento

DESCENDIENTE de `0013_plantillas_notificacion` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). Grupo 13 de
`ajustes-post-referencia-funcional/REQUERIMIENTOS.md`: reemplaza el booleano
único `personas.notificaciones_activas` por una matriz Canal x Evento. Tabla
dispersa a propósito -- sin fila para una combinación, el servicio de dominio
resuelve el default histórico (SMS activo, resto inactivo), así que esta
migración NO necesita backfill de datos.

Revision ID: 0014_preferencia_notificacion
Revises: 0013_plantillas_notificacion
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0014_preferencia_notificacion"
down_revision = "0013_plantillas_notificacion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "persona_preferencia_notificacion",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("persona_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canal", sa.String(length=20), nullable=False),
        sa.Column("evento", sa.String(length=20), nullable=False),
        sa.Column("activo", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["persona_id"],
            ["personas.id"],
            name="fk_persona_preferencia_notificacion_persona",
        ),
        sa.UniqueConstraint(
            "persona_id",
            "canal",
            "evento",
            name="uq_persona_preferencia_notificacion_persona_canal_evento",
        ),
    )


def downgrade() -> None:
    op.drop_table("persona_preferencia_notificacion")
