"""recipient_whatsapp -- WhatsApp propio del destinatario en el snapshot del Paquete

DESCENDIENTE de `0057_alinear_snapshot_conjunto` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). Issue 379 (`.scratch/pendientes-cliente`):
"primera entrega" se decidía solo por `recipient_phone`, así que un cliente
solo-WhatsApp (ADR-0007) nunca la tenía. Columna nullable nueva (snapshot,
ADR-0001) + índice (la bandera de `/paquetes` la consulta en batch). Datos: los
paquetes SIN teléfono cuyo destinatario es el propio Anunciante (mismo nombre)
toman el WhatsApp del Anunciante -- el único caso que se puede reconstruir sin
adivinar por nombre a otra Persona.

Revision ID: 0058_recipient_whatsapp
Revises: 0057_alinear_snapshot_conjunto
Create Date: 2026-09-22
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0058_recipient_whatsapp"
down_revision = "0057_alinear_snapshot_conjunto"
branch_labels = None
depends_on = None


def llenar_recipient_whatsapp(conexion) -> None:
    """Separada de `upgrade()` para que la prueba de datos la corra sobre su propia conexión."""
    conexion.execute(
        sa.text(
            "UPDATE paquetes p SET recipient_whatsapp = pe.whatsapp_usuario "
            "FROM personas pe "
            "WHERE pe.id = p.announced_by_persona_id "
            "AND p.recipient_phone IS NULL AND p.recipient_whatsapp IS NULL "
            "AND pe.whatsapp_usuario IS NOT NULL AND p.recipient_name = pe.nombre"
        )
    )


def upgrade() -> None:
    op.add_column("paquetes", sa.Column("recipient_whatsapp", sa.String(120), nullable=True))
    op.create_index("ix_paquetes_recipient_whatsapp", "paquetes", ["recipient_whatsapp"])
    llenar_recipient_whatsapp(op.get_bind())


def downgrade() -> None:
    op.drop_index("ix_paquetes_recipient_whatsapp", table_name="paquetes")
    op.drop_column("paquetes", "recipient_whatsapp")
