"""cobro_bodegaje -- entidades del módulo de cobro y bodegaje

DESCENDIENTE de `0043_persona_baja_admin_en` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). `.scratch/cobro-bodegaje`, ticket 01:
crea `cobros` (registro inmutable 1↔1 con `paquetes`), `tarifas_cobro`
(singleton editable por ADMIN) y `motivos_anulacion_cobro` (catálogo, mismo
molde que `motivos_cancelacion`).

Revision ID: 0044_cobro_bodegaje
Revises: 0043_persona_baja_admin_en
Create Date: 2026-09-08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0044_cobro_bodegaje"
down_revision = "0043_persona_baja_admin_en"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tarifas_cobro",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("base_normal", sa.Integer(), nullable=False),
        sa.Column("base_extra_dimensionado", sa.Integer(), nullable=False),
        sa.Column("bodegaje_normal_24h", sa.Integer(), nullable=False),
        sa.Column("bodegaje_extra_dimensionado_24h", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "motivos_anulacion_cobro",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("etiqueta", sa.String(length=40), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("etiqueta", name="uq_motivos_anulacion_cobro_etiqueta"),
    )

    op.create_table(
        "cobros",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("paquete_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("monto_base", sa.Integer(), nullable=False),
        sa.Column("bloques_bodegaje", sa.Integer(), nullable=False),
        sa.Column("monto_bodegaje", sa.Integer(), nullable=False),
        sa.Column("monto_total", sa.Integer(), nullable=False),
        sa.Column("motivo_anulacion", sa.String(length=40), nullable=True),
        sa.Column("cobrado_por_usuario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cobrado_en", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("paquete_id", name="uq_cobros_paquete_id"),
        sa.ForeignKeyConstraint(["paquete_id"], ["paquetes.id"], name="fk_cobros_paquete"),
        sa.ForeignKeyConstraint(
            ["cobrado_por_usuario_id"],
            ["usuarios.id"],
            name="fk_cobros_cobrado_por_usuario",
        ),
    )


def downgrade() -> None:
    op.drop_table("cobros")
    op.drop_table("motivos_anulacion_cobro")
    op.drop_table("tarifas_cobro")
