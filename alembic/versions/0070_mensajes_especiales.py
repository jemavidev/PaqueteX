"""mensajes_especiales -- avisos guardados para los días sin servicio, con historial

DESCENDIENTE de `0069_pin_dispositivo` (`down_revision`). El árbol permanece de raíz única (ADR-0002).
`.scratch/avisos-especiales`, ticket 01.

Revision ID: 0070_mensajes_especiales
Revises: 0069_pin_dispositivo
Create Date: 2026-10-11
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0070_mensajes_especiales"
down_revision = "0069_pin_dispositivo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mensajes_especiales",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("nombre", sa.String(length=60), nullable=False),
        sa.Column("texto", sa.String(length=500), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("eliminado_en", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_mensajes_especiales_nombre_vigente",
        "mensajes_especiales",
        [sa.text("lower(nombre)")],
        unique=True,
        postgresql_where=sa.text("eliminado_en IS NULL"),
    )
    op.create_table(
        "mensajes_especiales_historial",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("mensaje_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("accion", sa.String(length=20), nullable=False),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("nombre_anterior", sa.String(length=60), nullable=True),
        sa.Column("nombre_nuevo", sa.String(length=60), nullable=True),
        sa.Column("texto_anterior", sa.String(length=500), nullable=True),
        sa.Column("texto_nuevo", sa.String(length=500), nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["mensaje_id"], ["mensajes_especiales.id"], name="fk_mensajes_especiales_historial_mensaje"
        ),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], name="fk_mensajes_especiales_historial_usuario"),
    )


def downgrade() -> None:
    op.drop_table("mensajes_especiales_historial")
    op.drop_index("uq_mensajes_especiales_nombre_vigente", table_name="mensajes_especiales")
    op.drop_table("mensajes_especiales")
