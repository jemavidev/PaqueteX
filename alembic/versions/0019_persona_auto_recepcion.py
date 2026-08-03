"""persona_autoriza_recepcion_automatica — toggle informativo para el staff (.scratch/mis-datos, ticket 12)

DESCENDIENTE de `0018_ocupante_desvinculado_en` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). Agrega `autoriza_recepcion_automatica`
(booleano, default `False`) a `personas`: la propia Persona autoriza de
antemano que el staff anuncie/reciba paquetes a su nombre sin necesidad de
llamarla primero para pedir permiso verbal. Puramente informativo/visible
para el staff -- no es un gate técnico, el staff ya puede anunciar/recibir
para cualquiera sin restricción alguna hoy.

Revision ID: 0019_persona_auto_recepcion
Revises: 0018_ocupante_desvinculado_en
Create Date: 2026-08-03
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0019_persona_auto_recepcion"
down_revision = "0018_ocupante_desvinculado_en"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "personas",
        sa.Column(
            "autoriza_recepcion_automatica",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("personas", "autoriza_recepcion_automatica")
