"""persona_bloqueo -- bloqueo reversible de un residente + catálogo de motivos

DESCENDIENTE de `0045_contactos_externos` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). `.scratch/bloquear-clientes`, ticket 01:
agrega `bloqueado_en`/`desbloqueo_autorizado_en`/`terminos_aceptados_en`
(timestamps, nullable) y `motivo_bloqueo` (texto, nullable -- mismo criterio
que `Paquete.cancel_reason`: copia del texto, no una FK) a `personas`. Crea
`motivos_bloqueo`, mismo molde que `motivos_cancelacion`.

Revision ID: 0046_persona_bloqueo
Revises: 0045_contactos_externos
Create Date: 2026-09-08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0046_persona_bloqueo"
down_revision = "0045_contactos_externos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "personas", sa.Column("bloqueado_en", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "personas",
        sa.Column("desbloqueo_autorizado_en", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "personas",
        sa.Column("terminos_aceptados_en", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("personas", sa.Column("motivo_bloqueo", sa.String(length=40), nullable=True))

    op.create_table(
        "motivos_bloqueo",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("etiqueta", sa.String(length=40), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("etiqueta", name="uq_motivos_bloqueo_etiqueta"),
    )


def downgrade() -> None:
    op.drop_table("motivos_bloqueo")
    op.drop_column("personas", "motivo_bloqueo")
    op.drop_column("personas", "terminos_aceptados_en")
    op.drop_column("personas", "desbloqueo_autorizado_en")
    op.drop_column("personas", "bloqueado_en")
