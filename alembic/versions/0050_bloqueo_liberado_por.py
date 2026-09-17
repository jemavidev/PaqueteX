"""persona_bloqueo_liberado_por -- registro de quién liberó un bloqueo desde staff

DESCENDIENTE de `0049_revertir_desvinculada_en` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). Conversación 2026-09-15 (`codebase-
design`): staff necesita poder desbloquear a un residente que no puede o no
quiere autoservirse por el portal (OTP + aceptar términos), sin pasar por
`aceptar_terminos_y_desbloquear` -- esa función registra una aceptación de
términos REAL, y hacerla pasar por un click de staff dejaría un
`terminos_aceptados_en` falso (el residente nunca leyó nada).

Agrega `bloqueo_liberado_en`/`bloqueo_liberado_por_usuario_id` (nullable,
igual que el resto de los timestamps de bloqueo) a `personas` -- se
sobreescriben en cada liberación (mismo criterio que
`terminos_aceptados_en`: solo la más reciente, sin tabla de historial
aparte). No hay `ON DELETE` explícito a propósito: un Usuario de staff no
se borra nunca hoy (solo se desactiva, `Usuario.activo`), así que no hace
falta decidir ese caso todavía.

Revision ID: 0050_bloqueo_liberado_por
Revises: 0049_revertir_desvinculada_en
Create Date: 2026-09-15
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0050_bloqueo_liberado_por"
down_revision = "0049_revertir_desvinculada_en"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "personas", sa.Column("bloqueo_liberado_en", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "personas",
        sa.Column(
            "bloqueo_liberado_por_usuario_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.create_foreign_key(
        "fk_personas_bloqueo_liberado_por",
        "personas",
        "usuarios",
        ["bloqueo_liberado_por_usuario_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_personas_bloqueo_liberado_por", "personas", type_="foreignkey")
    op.drop_column("personas", "bloqueo_liberado_por_usuario_id")
    op.drop_column("personas", "bloqueo_liberado_en")
