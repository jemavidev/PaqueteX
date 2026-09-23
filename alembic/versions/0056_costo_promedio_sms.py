"""costo_promedio_sms_cop -- costo promedio por SMS en ProveedorConfig (AWS SNS)

DESCENDIENTE de `0055_registro_sms` (`down_revision`). El árbol permanece
de raíz única (ADR-0002). `.scratch/estadisticas-cobro-dashboard`, ticket
13: nueva columna nullable en `proveedores_notificacion_config` -- el único
dato de configuración de esta feature que vive en BASE DE DATOS (no en
`.env`), para que guardarlo nunca dispare el mecanismo SSH de credenciales
ni el reinicio del contenedor. Solo tiene sentido para AWS SNS
(`ProveedorInfo.campo_costo_sms`), pero se agrega a la tabla completa --
igual que `habilitado`/`orden`, ningún proveedor está obligado a usarla.

Revision ID: 0056_costo_promedio_sms
Revises: 0055_registro_sms
Create Date: 2026-09-20
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0056_costo_promedio_sms"
down_revision = "0055_registro_sms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "proveedores_notificacion_config",
        sa.Column("costo_promedio_sms_cop", sa.Numeric(12, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("proveedores_notificacion_config", "costo_promedio_sms_cop")
