"""personas.whatsapp_usuario -- usuario de WhatsApp del cliente

DESCENDIENTE de `0025_indices_paquetes` (`down_revision`). El árbol
permanece de raíz única (ADR-0002).

Pedido del cliente (.scratch/pendientes-cliente, 2026-08-07): campo nuevo,
editable solo por staff desde `/residentes/{id}` -- mismo patrón que
`email`/`segundo_contacto` (ampliable, nullable, sin backfill).

Revision ID: 0026_persona_whatsapp_usuario
Revises: 0025_indices_paquetes
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0026_persona_whatsapp_usuario"
down_revision = "0025_indices_paquetes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "personas",
        sa.Column("whatsapp_usuario", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("personas", "whatsapp_usuario")
