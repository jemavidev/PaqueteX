"""contactos_externos -- consolidación de contactos externos (Google Contacts + PaqueteX v1.0)

DESCENDIENTE de `0044_cobro_bodegaje` (`down_revision`). El árbol permanece
de raíz única (ADR-0002). `.scratch/contactos-externos`, ticket 01: crea
`contactos_externos` (nombre, whatsapp_usuario, fuentes) y
`contactos_externos_telefonos` (1 a muchos, teléfono único = llave de
fusión). Completamente independiente de `personas`/`ocupantes`.

Revision ID: 0045_contactos_externos
Revises: 0044_cobro_bodegaje
Create Date: 2026-09-08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0045_contactos_externos"
down_revision = "0044_cobro_bodegaje"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contactos_externos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("whatsapp_usuario", sa.String(length=120), nullable=True),
        sa.Column("fuentes", postgresql.ARRAY(sa.String(length=40)), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "contactos_externos_telefonos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("contacto_externo_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telefono", sa.String(length=20), nullable=False),
        sa.UniqueConstraint(
            "telefono", name="uq_contactos_externos_telefonos_telefono"
        ),
        sa.ForeignKeyConstraint(
            ["contacto_externo_id"],
            ["contactos_externos.id"],
            name="fk_contactos_externos_telefonos_contacto",
        ),
    )


def downgrade() -> None:
    op.drop_table("contactos_externos_telefonos")
    op.drop_table("contactos_externos")
