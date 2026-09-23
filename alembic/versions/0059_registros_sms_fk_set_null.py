"""registros_sms.paquete_id -- ON DELETE SET NULL

DESCENDIENTE de `0058_recipient_whatsapp` (`down_revision`). El árbol permanece
de raíz única (ADR-0002). Issue 380 (`.scratch/pendientes-cliente`): borrar un
Paquete Anunciado (la única vez que un Paquete se borra de verdad, ver
`packages.py::delete_action`) fallaba con un error de llave foránea si su SMS de
anuncio había quedado registrado. Con SET NULL el registro -- y el costo del SMS
que ya se envió -- se conserva, solo sin paquete asociado.

Revision ID: 0059_registros_sms_fk_set_null
Revises: 0058_recipient_whatsapp
Create Date: 2026-09-23
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0059_registros_sms_fk_set_null"
down_revision = "0058_recipient_whatsapp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("fk_registros_sms_paquete", "registros_sms", type_="foreignkey")
    op.create_foreign_key(
        "fk_registros_sms_paquete", "registros_sms", "paquetes", ["paquete_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint("fk_registros_sms_paquete", "registros_sms", type_="foreignkey")
    op.create_foreign_key("fk_registros_sms_paquete", "registros_sms", "paquetes", ["paquete_id"], ["id"])
