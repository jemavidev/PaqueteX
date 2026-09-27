"""pin_dispositivo -- PIN de operador, dispositivos registrados y eventos de seguridad

DESCENDIENTE de `0068_seguridad_sesion` (`down_revision`). El árbol permanece de raíz única (ADR-0002).
`.scratch/pin-operador-dispositivo`, ticket 02.

Revision ID: 0069_pin_dispositivo
Revises: 0068_seguridad_sesion
Create Date: 2026-09-27
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0069_pin_dispositivo"
down_revision = "0068_seguridad_sesion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("usuarios", sa.Column("pin_huella", sa.String(length=64), nullable=True))
    op.add_column("usuarios", sa.Column("pin_actualizado_en", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "usuarios", sa.Column("registros_version", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "usuarios", sa.Column("debe_cambiar_pin", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.create_unique_constraint("uq_usuarios_pin_huella", "usuarios", ["pin_huella"])

    op.create_table(
        "dispositivos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ultimo_uso_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("intentos_pin_fallidos", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "registros_dispositivo",
        sa.Column("dispositivo_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("registrado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("registros_version", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["dispositivo_id"], ["dispositivos.id"], ondelete="CASCADE", name="fk_registros_dispositivo_dispositivo"
        ),
        sa.ForeignKeyConstraint(
            ["usuario_id"], ["usuarios.id"], ondelete="CASCADE", name="fk_registros_dispositivo_usuario"
        ),
    )
    op.create_table(
        "eventos_seguridad",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tipo", sa.String(length=30), nullable=False),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dispositivo_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["usuario_id"], ["usuarios.id"], ondelete="CASCADE", name="fk_eventos_seguridad_usuario"
        ),
        sa.ForeignKeyConstraint(
            ["dispositivo_id"], ["dispositivos.id"], ondelete="SET NULL", name="fk_eventos_seguridad_dispositivo"
        ),
    )
    op.create_index("ix_eventos_seguridad_tipo_creado_en", "eventos_seguridad", ["tipo", "creado_en"])


def downgrade() -> None:
    op.drop_index("ix_eventos_seguridad_tipo_creado_en", table_name="eventos_seguridad")
    op.drop_table("eventos_seguridad")
    op.drop_table("registros_dispositivo")
    op.drop_table("dispositivos")
    op.drop_constraint("uq_usuarios_pin_huella", "usuarios", type_="unique")
    op.drop_column("usuarios", "debe_cambiar_pin")
    op.drop_column("usuarios", "registros_version")
    op.drop_column("usuarios", "pin_actualizado_en")
    op.drop_column("usuarios", "pin_huella")
