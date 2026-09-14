"""desvinculada_en en personas — se quedó sin Ocupante por perder su único contacto

DESCENDIENTE de `0047_saldo_contra_entrega`. El árbol permanece de raíz
única (ADR-0002). Añade `desvinculada_en` (timestamp, nullable) a
`personas`: marca cuando una Persona quedó sin ningún Ocupante que la
referencie porque perdió su único contacto (Teléfono/WhatsApp) vía
`desvincular_telefono_ocupante`/`desvincular_whatsapp_ocupante` --
DISTINTO de `eliminado_en` (derecho al olvido, deliberado, irreversible,
ADR-0005) y de `baja_administrativa_en` (pausa reversible, acción
explícita del staff): este es un efecto INCIDENTAL, no un pedido de
"olvidar" a nadie -- por eso el Teléfono/WhatsApp real NUNCA se toca acá
(a diferencia de `anonimizar_persona`), para que la misma Persona se
reconecte sola si el mismo contacto vuelve a asociarse a un Ocupante
después (`agregar_ocupante` limpia esta marca en ese momento). Mientras
esté puesta, la Persona no aparece en ningún listado de `/residentes`
(pedido explícito del cliente, conversación 2026-09-12 -- probado en vivo
con "Daniela"/"Angélica": preservarla SIN ocultarla dejaba dos filas
visibles con el mismo nombre, confuso).

Revision ID: 0048_persona_desvinculada_en
Revises: 0047_saldo_contra_entrega
Create Date: 2026-09-12
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0048_persona_desvinculada_en"
down_revision = "0047_saldo_contra_entrega"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "personas",
        sa.Column("desvinculada_en", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("personas", "desvinculada_en")
