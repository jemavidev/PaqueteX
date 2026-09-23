"""usuarios.sesion_version -- cambiar la contraseña cierra las demás sesiones

DESCENDIENTE de `0059_registros_sms_fk_set_null` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). Issue 383 (`.scratch/pendientes-cliente`):
la sesión es una cookie firmada sin estado en el servidor, así que no había forma
de revocarla. Cada cambio de contraseña sube esta versión y la sesión guarda la
suya: una sesión con versión vieja deja de valer. Default 0 = la versión que
traen, sin saberlo, las cookies emitidas antes de este cambio.

Revision ID: 0060_usuario_sesion_version
Revises: 0059_registros_sms_fk_set_null
Create Date: 2026-09-23
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0060_usuario_sesion_version"
down_revision = "0059_registros_sms_fk_set_null"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("usuarios", sa.Column("sesion_version", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("usuarios", "sesion_version")
