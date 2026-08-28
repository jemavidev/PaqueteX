"""usuarios -- columnas telefono + whatsapp (contacto propio del staff)

DESCENDIENTE de `0035_historial_created_at` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). `.scratch/notificaciones-enviar-prueba`,
ticket 01: el ADMIN podrá pre-llenar el destino de un mensaje de prueba
(`/administracion/notificaciones`) con su propio teléfono/WhatsApp, guardados
en `/mi-sesion` junto al nombre que ya edita ahí.

Ambas columnas NULLABLE desde el inicio -- sin backfill posible (no existe
ningún dato previo de teléfono/WhatsApp de staff), todo Usuario existente
arranca con ambas en NULL. SIN relación con el modelo de identidad de
Persona (Teléfono/WhatsApp como llave, ADR-0003/ADR-0007): sin unicidad, sin
implicación de login/OTP -- son contacto de texto libre.

Revision ID: 0036_usuario_telefono_whatsapp
Revises: 0035_historial_created_at
Create Date: 2026-08-28
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0036_usuario_telefono_whatsapp"
down_revision = "0035_historial_created_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("usuarios", sa.Column("telefono", sa.String(length=20), nullable=True))
    op.add_column("usuarios", sa.Column("whatsapp", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("usuarios", "whatsapp")
    op.drop_column("usuarios", "telefono")
