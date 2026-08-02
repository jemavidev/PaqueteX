"""password_resets -- token de recuperación de contraseña de staff

DESCENDIENTE de `0015_usuario_activo` (`down_revision`). El árbol permanece de
raíz única (ADR-0002). Crea `password_resets`: `usuario_id` (FK a
`usuarios.id`), `token_hash` (SHA-256 del token crudo, indexado -- se busca
por igualdad, ver docstring de `app.domain.password_reset`), `expira_en`,
`usado_en` (nullable, marca de consumo). Índice de nombre idéntico al del ORM
(`ix_password_resets_token_hash`) para la paridad esquema↔ORM.

Revision ID: 0016_password_resets
Revises: 0015_usuario_activo
Create Date: 2026-08-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0016_password_resets"
down_revision = "0015_usuario_activo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "password_resets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "usuario_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("usuarios.id"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expira_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("usado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_password_resets_token_hash", "password_resets", ["token_hash"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_password_resets_token_hash", table_name="password_resets")
    op.drop_table("password_resets")
