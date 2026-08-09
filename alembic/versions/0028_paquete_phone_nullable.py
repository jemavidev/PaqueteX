"""paquetes.announced_by_phone nullable -- Anunciante puede ser solo-WhatsApp

DESCENDIENTE de `0027_persona_telefono_o_whatsapp` (`down_revision`). El
árbol permanece de raíz única (ADR-0002).

Consecuencia directa de ADR-0007 (`.scratch/announce-rapido`, ticket 03):
`announce()` ahora puede identificar al Anunciante por `whatsapp_usuario` en
vez de Teléfono -- ese Anunciante no tiene Teléfono que congelar en el
snapshot, así que `announced_by_phone` queda `NULL` para esos Paquetes.
`announced_by_persona_id` NO cambia (sigue `NOT NULL`): toda Persona real,
tenga o no Teléfono, sigue siendo referenciable por FK.

Revision ID: 0028_paquete_phone_nullable
Revises: 0027_persona_telefono_o_whatsapp
Create Date: 2026-08-09
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0028_paquete_phone_nullable"
down_revision = "0027_persona_telefono_o_whatsapp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "paquetes", "announced_by_phone", existing_type=sa.String(length=20), nullable=True
    )


def downgrade() -> None:
    # Falla con IntegrityError si ya existe algún Paquete anunciado por una
    # Persona solo-WhatsApp (announced_by_phone NULL) -- correcto, mismo
    # criterio que 0027: revertir con esos datos presentes debe fallar en
    # vez de perderlos en silencio.
    op.alter_column(
        "paquetes", "announced_by_phone", existing_type=sa.String(length=20), nullable=False
    )
