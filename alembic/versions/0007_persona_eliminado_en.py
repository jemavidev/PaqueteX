"""eliminado_en en personas — marca de anonimización (ADR-0005)

DESCENDIENTE de `0006_otps_cliente` (`down_revision = "0006_otps_cliente"`). El
árbol permanece de raíz única (ADR-0002). Añade `eliminado_en` (timestamp,
nullable) a `personas`: marca que la Persona fue anonimizada (ADR-0005) — la
fila NUNCA se borra (FK real `fk_paquetes_anunciante` desde `paquetes`).

Revision ID: 0007_persona_eliminado_en
Revises: 0006_otps_cliente
Create Date: 2026-07-25
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0007_persona_eliminado_en"
down_revision = "0006_otps_cliente"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "personas", sa.Column("eliminado_en", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("personas", "eliminado_en")
