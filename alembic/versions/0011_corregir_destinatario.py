"""corrected_at/corrected_by_usuario_id en paquetes — corregir destinatario

DESCENDIENTE de `0010_ocupantes` (`down_revision`). El árbol permanece de raíz
única (ADR-0002). Excepción ACOTADA y auditada a la inmutabilidad de
ADR-0001 (ver .scratch/announce-staff-completo/spec.md): el staff puede
corregir `recipient_name`/`recipient_phone` de un Paquete SOLO mientras sigue
`ANUNCIADO` — estas dos columnas registran quién y cuándo, igual que las demás
transiciones (nunca un actor hardcodeado).

Revision ID: 0011_corregir_destinatario
Revises: 0010_ocupantes
Create Date: 2026-07-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0011_corregir_destinatario"
down_revision = "0010_ocupantes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "paquetes",
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "paquetes",
        sa.Column("corrected_by_usuario_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_paquetes_corrected_by_usuario",
        "paquetes",
        "usuarios",
        ["corrected_by_usuario_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_paquetes_corrected_by_usuario", "paquetes", type_="foreignkey")
    op.drop_column("paquetes", "corrected_by_usuario_id")
    op.drop_column("paquetes", "corrected_at")
