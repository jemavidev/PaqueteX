"""usuarios.activo -- desactivar cuentas de staff sin borrarlas

DESCENDIENTE de `0014_preferencia_notificacion` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). Grupo 18 de
`ajustes-post-referencia-funcional/REQUERIMIENTOS.md`: soft-disable, nunca
DELETE -- las FK de actor (`received_by_usuario_id`, etc.) dependen de que
el Usuario exista para la auditoría (Grupo 11). Columna con `server_default`
para que el staff ya sembrado quede `activo=true` sin backfill manual.

Revision ID: 0015_usuario_activo
Revises: 0014_preferencia_notificacion
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0015_usuario_activo"
down_revision = "0014_preferencia_notificacion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "usuarios",
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("usuarios", "activo")
