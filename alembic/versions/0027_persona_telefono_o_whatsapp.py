"""personas.telefono nullable -- Teléfono o WhatsApp, nunca los dos vacíos

DESCENDIENTE de `0026_persona_whatsapp_usuario` (`down_revision`). El árbol
permanece de raíz única (ADR-0002).

Relaja ADR-0003 ("El Teléfono es la llave universal de la Persona") de forma
acotada -- ver `docs/adr/0007-persona-telefono-o-whatsapp.md`
(`.scratch/announce-rapido`, ticket 01): una Persona ahora puede existir con
solo `whatsapp_usuario` (sin Teléfono), pero nunca sin NINGUNO de los dos --
esa combinación sigue rechazada, tanto por la nueva constraint como por el
código de dominio (`get_or_create_persona`/`get_or_create_persona_por_whatsapp`
siempre completan uno de los dos antes de persistir).

`whatsapp_usuario` gana un índice único parcial (mismo estilo que
`uq_ocupantes_principal_por_apartamento`, 0010): dos Personas no pueden
compartir el mismo usuario de WhatsApp, igual que ya pasa con el Teléfono.

Revision ID: 0027_persona_telefono_o_whatsapp
Revises: 0026_persona_whatsapp_usuario
Create Date: 2026-08-09
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0027_persona_telefono_o_whatsapp"
down_revision = "0026_persona_whatsapp_usuario"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("personas", "telefono", existing_type=sa.String(length=20), nullable=True)
    op.create_check_constraint(
        "ck_personas_telefono_o_whatsapp",
        "personas",
        "telefono IS NOT NULL OR whatsapp_usuario IS NOT NULL",
    )
    op.create_index(
        "uq_personas_whatsapp_usuario",
        "personas",
        ["whatsapp_usuario"],
        unique=True,
        postgresql_where=sa.text("whatsapp_usuario IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_personas_whatsapp_usuario", table_name="personas")
    op.drop_constraint("ck_personas_telefono_o_whatsapp", "personas", type_="check")
    # Falla con IntegrityError si ya existe alguna Persona solo-WhatsApp
    # (telefono NULL) -- correcto: revertir esta migración con esos datos
    # presentes perdería la única identidad de esas filas, así que debe
    # fallar en vez de hacerlo en silencio.
    op.alter_column("personas", "telefono", existing_type=sa.String(length=20), nullable=False)
