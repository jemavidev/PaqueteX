"""seguridad_sesion -- tiempos del PIN de operador en la configuración del conjunto

DESCENDIENTE de `0067_filas_estante_desactivadas` (`down_revision`). El árbol permanece de raíz única (ADR-0002).
`.scratch/pin-operador-dispositivo`, ticket 01: segundos de inactividad antes del Bloqueo y días de vigencia del
registro de un dispositivo. NULLABLE, mismo criterio que el resto de la tabla: sin valor, el servicio cae a su default
en código (300 s y 15 días).

Revision ID: 0068_seguridad_sesion
Revises: 0067_filas_estante_desactivadas
Create Date: 2026-09-27
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0068_seguridad_sesion"
down_revision = "0067_filas_estante_desactivadas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("configuracion_conjunto", sa.Column("segundos_inactividad", sa.Integer(), nullable=True))
    op.add_column("configuracion_conjunto", sa.Column("dias_registro_dispositivo", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("configuracion_conjunto", "dias_registro_dispositivo")
    op.drop_column("configuracion_conjunto", "segundos_inactividad")
