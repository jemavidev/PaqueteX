"""contactos_externos_whatsapp -- WhatsApp como segunda llave de fusión

DESCENDIENTE de `0050_bloqueo_liberado_por` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). `.scratch/contactos-externos-import-
export`: crea `contactos_externos_whatsapps` (1 a muchos, mismo patrón --
incluido el plural del nombre de tabla -- que `contactos_externos_telefonos`:
usuario de WhatsApp único = segunda llave de fusión) y retira la columna
`whatsapp_usuario` que antes vivía directo en `contactos_externos` (dato de
un solo valor, sin deduplicación -- ninguna fuente hasta ahora la había
poblado, así que no hay datos que migrar EN ESTE MOMENTO).

Revision ID: 0051_contactos_externos_whatsapp
Revises: 0050_bloqueo_liberado_por
Create Date: 2026-09-16
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0051_contactos_externos_whatsapp"
down_revision = "0050_bloqueo_liberado_por"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contactos_externos_whatsapps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("contacto_externo_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("whatsapp_usuario", sa.String(length=120), nullable=False),
        sa.UniqueConstraint(
            "whatsapp_usuario", name="uq_contactos_externos_whatsapps_whatsapp_usuario"
        ),
        sa.ForeignKeyConstraint(
            ["contacto_externo_id"],
            ["contactos_externos.id"],
            name="fk_contactos_externos_whatsapps_contacto",
        ),
    )
    op.drop_column("contactos_externos", "whatsapp_usuario")


def downgrade() -> None:
    # Lossy si esta feature ya se usó (`.scratch/contactos-externos-import-
    # export`): cualquier usuario de WhatsApp cargado por el import queda
    # DESTRUIDO -- la columna que se recrea vuelve vacía, no se migra ningún
    # dato de vuelta desde `contactos_externos_whatsapps` (que además tiene
    # 1 a muchos, no calza 1 a 1 con la columna vieja). Aceptado a propósito,
    # mismo criterio que `0033_plantilla_multicanal.py`: downgrade es para
    # revertir un deploy recién hecho, no para preservar datos ya en uso.
    op.add_column(
        "contactos_externos",
        sa.Column("whatsapp_usuario", sa.String(length=120), nullable=True),
    )
    op.drop_table("contactos_externos_whatsapps")
