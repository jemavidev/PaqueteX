"""envios_aviso_especial -- SMS de avisos especiales que sí salieron ("Último SMS" por cliente)

DESCENDIENTE de `0070_mensajes_especiales` (`down_revision`). El árbol permanece de raíz única (ADR-0002).
`.scratch/avisos-especiales`, ticket 05. `registros_sms.tipo` gana el valor `AVISO_ESPECIAL` sin migración (es
VARCHAR, `native_enum=False`).

Revision ID: 0071_envios_aviso_especial
Revises: 0070_mensajes_especiales
Create Date: 2026-10-11
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0071_envios_aviso_especial"
down_revision = "0070_mensajes_especiales"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "envios_aviso_especial",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("persona_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mensaje_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nombre_mensaje", sa.String(length=60), nullable=False),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["persona_id"], ["personas.id"], name="fk_envios_aviso_especial_persona", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["mensaje_id"], ["mensajes_especiales.id"], name="fk_envios_aviso_especial_mensaje"),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], name="fk_envios_aviso_especial_usuario"),
    )
    op.create_index(
        "ix_envios_aviso_especial_persona_creado_en", "envios_aviso_especial", ["persona_id", "creado_en"]
    )


def downgrade() -> None:
    op.drop_index("ix_envios_aviso_especial_persona_creado_en", table_name="envios_aviso_especial")
    op.drop_table("envios_aviso_especial")
