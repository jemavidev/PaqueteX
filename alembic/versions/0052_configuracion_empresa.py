"""configuracion_empresa + columnas operativas en configuracion_conjunto

DESCENDIENTE de `0051_contactos_externos_whatsapp` (`down_revision`). El
árbol permanece de raíz única (ADR-0002). Grilling 2026-09-18 (issue 350,
.scratch/pendientes-cliente/spec.md): datos hoy hardcodeados en las 4
plantillas públicas legales (razón social, NIT, dirección, contacto,
horarios de atención) pasan a ser editables desde `/administracion/
conjunto`. Igual que `0020_configuracion_conjunto`, ninguna fila se siembra
acá -- ambos servicios de dominio caen a sus defaults en código mientras
ningún ADMIN edite, mismo patrón de tabla de override.

Revision ID: 0052_configuracion_empresa
Revises: 0051_contactos_externos_whatsapp
Create Date: 2026-09-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0052_configuracion_empresa"
down_revision = "0051_contactos_externos_whatsapp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "configuracion_conjunto",
        sa.Column("horario_lunes_viernes", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "configuracion_conjunto",
        sa.Column("horario_sabados", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "configuracion_conjunto",
        sa.Column("horario_domingos", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "configuracion_conjunto",
        sa.Column("numero_whatsapp", sa.String(length=30), nullable=True),
    )

    op.create_table(
        "configuracion_empresa",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("razon_social", sa.String(length=200), nullable=False),
        sa.Column("nit", sa.String(length=50), nullable=True),
        sa.Column("direccion", sa.String(length=300), nullable=True),
        sa.Column("email_contacto", sa.String(length=200), nullable=True),
        sa.Column("telefono_contacto", sa.String(length=50), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("configuracion_empresa")
    op.drop_column("configuracion_conjunto", "numero_whatsapp")
    op.drop_column("configuracion_conjunto", "horario_domingos")
    op.drop_column("configuracion_conjunto", "horario_sabados")
    op.drop_column("configuracion_conjunto", "horario_lunes_viernes")
