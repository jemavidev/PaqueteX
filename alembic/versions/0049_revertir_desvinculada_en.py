"""revierte desvinculada_en en personas — nunca se permite llegar a cero canales

DESCENDIENTE de `0048_persona_desvinculada_en`. El árbol permanece de raíz
única (ADR-0002). Retira por completo la columna `desvinculada_en` (agregada
en 0048): conversación 2026-09-14, pedido explícito del cliente -- "evitar
que se puedan borrar los datos de teléfono/usuario de WhatsApp, dejando que
solo sea posible editar a un número válido, pero nunca eliminarlo".

Con esa regla, ninguna Persona puede llegar nunca a cero canales a través de
la UI (`desvincular_telefono_ocupante`/`desvincular_whatsapp_ocupante` ahora
SOLO permiten canal doble -- rechazan siempre si es el único canal, en vez de
dejar la Persona huérfana), así que el mecanismo entero de "Persona oculta,
reconectable" que introdujo 0048 deja de tener ningún caso que lo dispare.
Se retira en vez de dejarlo sin uso -- ver `ocupante_service.py` para el
detalle completo de la decisión y su historial (2 vueltas de diseño previas
antes de llegar acá: anonimizar, preservar-visible, ocultar-y-reconectar,
cada una descartada en vivo antes de esta simplificación final).

Revision ID: 0049_revertir_desvinculada_en
Revises: 0048_persona_desvinculada_en
Create Date: 2026-09-14
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0049_revertir_desvinculada_en"
down_revision = "0048_persona_desvinculada_en"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("personas", "desvinculada_en")


def downgrade() -> None:
    op.add_column(
        "personas",
        sa.Column("desvinculada_en", sa.DateTime(timezone=True), nullable=True),
    )
