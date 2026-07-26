"""eliminar tracking_number de paquetes — reemplazado por access_code

DESCENDIENTE de `0008_persona_notif_activas` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). `tracking_number` nunca cumplió una función
de negocio real (se generaba y mostraba, pero `access_code` es el único código
de consulta usado — ver .scratch/anunciar-resolucion-destinatario-staff/spec.md,
Grupo 1 de REQUERIMIENTOS.md). Se elimina la columna y su UniqueConstraint.

Revision ID: 0009_eliminar_tracking_number
Revises: 0008_persona_notif_activas
Create Date: 2026-07-26
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0009_eliminar_tracking_number"
down_revision = "0008_persona_notif_activas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_paquetes_tracking_number", "paquetes", type_="unique")
    op.drop_column("paquetes", "tracking_number")


def downgrade() -> None:
    op.add_column(
        "paquetes",
        sa.Column("tracking_number", sa.String(length=50), nullable=True),
    )
    op.create_unique_constraint(
        "uq_paquetes_tracking_number", "paquetes", ["tracking_number"]
    )
