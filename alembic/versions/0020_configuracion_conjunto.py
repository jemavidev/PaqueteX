"""configuracion_conjunto — nombre vigente del Conjunto, singleton editable por ADMIN

DESCENDIENTE de `0019_persona_auto_recepcion` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). Tabla de override, mismo espíritu que
`plantillas_notificacion`: sin fila, `configuracion_conjunto_service.
obtener_nombre_conjunto` usa el default hardcodeado ("El Club") -- no se
siembra ninguna fila acá, la fila solo aparece cuando un ADMIN renombra por
primera vez (`.scratch/apartamento-catalogo-confirmacion/issues/01`).

Revision ID: 0020_configuracion_conjunto
Revises: 0019_persona_auto_recepcion
Create Date: 2026-08-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0020_configuracion_conjunto"
down_revision = "0019_persona_auto_recepcion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "configuracion_conjunto",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("configuracion_conjunto")
