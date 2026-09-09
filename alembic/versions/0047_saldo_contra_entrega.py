"""saldo_contra_entrega -- movimientos de saldo a favor para pago contra entrega

DESCENDIENTE de `0046_persona_bloqueo` (`down_revision`). El árbol permanece
de raíz única (ADR-0002). `.scratch/dinero-contra-entrega`, ticket 01: crea
`movimientos_saldo_contra_entrega`, append-only (ningún update/delete) --
el saldo de una Persona es la suma de sus movimientos, sin columna
desnormalizada en `personas`.

Revision ID: 0047_saldo_contra_entrega
Revises: 0046_persona_bloqueo
Create Date: 2026-09-08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0047_saldo_contra_entrega"
down_revision = "0046_persona_bloqueo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "movimientos_saldo_contra_entrega",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("persona_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("monto", sa.Integer(), nullable=False),
        sa.Column("paquete_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("registrado_por_usuario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["persona_id"], ["personas.id"], name="fk_movimientos_saldo_persona"
        ),
        sa.ForeignKeyConstraint(
            ["paquete_id"], ["paquetes.id"], name="fk_movimientos_saldo_paquete"
        ),
        sa.ForeignKeyConstraint(
            ["registrado_por_usuario_id"],
            ["usuarios.id"],
            name="fk_movimientos_saldo_registrado_por",
        ),
    )
    op.create_index(
        "ix_movimientos_saldo_persona_id",
        "movimientos_saldo_contra_entrega",
        ["persona_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_movimientos_saldo_persona_id", table_name="movimientos_saldo_contra_entrega"
    )
    op.drop_table("movimientos_saldo_contra_entrega")
