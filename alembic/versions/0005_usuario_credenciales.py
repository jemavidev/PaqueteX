"""credenciales de staff en usuarios (email único + password_hash)

DESCENDIENTE de `0004_cancel_reason` (`down_revision = "0004_cancel_reason"`). El
árbol permanece de raíz única (ADR-0002). Añade a `usuarios` las credenciales de
acceso del staff: `email` (único, nullable) y `password_hash` (nullable). Nombre
de constraint idéntico al del ORM (`uq_usuarios_email`) para la paridad esquema↔ORM.

Revision ID: 0005_usuario_credenciales
Revises: 0004_cancel_reason
Create Date: 2026-07-25
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0005_usuario_credenciales"
down_revision = "0004_cancel_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("usuarios", sa.Column("email", sa.String(length=255), nullable=True))
    op.add_column(
        "usuarios", sa.Column("password_hash", sa.String(length=255), nullable=True)
    )
    op.create_unique_constraint("uq_usuarios_email", "usuarios", ["email"])


def downgrade() -> None:
    op.drop_constraint("uq_usuarios_email", "usuarios", type_="unique")
    op.drop_column("usuarios", "password_hash")
    op.drop_column("usuarios", "email")
