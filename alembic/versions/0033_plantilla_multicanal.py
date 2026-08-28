"""plantillas_notificacion -- columnas canal + asunto (multicanal SMS/Email/WhatsApp)

DESCENDIENTE de `0032_reset_sms_no_anuncio` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). `.scratch/plantillas-notificacion-
multicanal`, ticket 01: extiende el sistema de plantillas (hasta ahora solo
SMS) a Email y WhatsApp, reutilizando la misma tabla en vez de crear una
paralela -- reutiliza toda la lógica de unicidad/fallback ya probada.

`canal` se agrega NULLABLE primero, se backfillea a 'SMS' para las filas
existentes (todas eran SMS implícito antes de esta migración -- no existía
otro canal posible hasta ahora, así que no hay ambigüedad en el backfill), y
luego se marca NOT NULL -- no se puede agregar una columna NOT NULL directo
sobre una tabla con filas existentes sin backfill primero.

`asunto` es nullable siempre -- solo tiene sentido para EMAIL (SMS/WhatsApp
no tienen asunto).

La UniqueConstraint (evento, motivo) y el índice único parcial para
`motivo IS NULL` (0023) se reemplazan por versiones que incluyen `canal`:
ahora la misma combinación evento/motivo puede tener una fila por canal
(mismo motivo por el que 0023 existe: Postgres trata cada NULL como distinto
de cualquier otro para efectos de unicidad).

Revision ID: 0033_plantilla_multicanal
Revises: 0032_reset_sms_no_anuncio
Create Date: 2026-08-26
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0033_plantilla_multicanal"
down_revision = "0032_reset_sms_no_anuncio"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plantillas_notificacion", sa.Column("canal", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "plantillas_notificacion", sa.Column("asunto", sa.String(length=200), nullable=True)
    )
    op.execute("UPDATE plantillas_notificacion SET canal = 'SMS' WHERE canal IS NULL")
    op.alter_column("plantillas_notificacion", "canal", nullable=False)

    op.drop_constraint(
        "uq_plantillas_notificacion_evento_motivo",
        "plantillas_notificacion",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_plantillas_notificacion_evento_motivo_canal",
        "plantillas_notificacion",
        ["evento", "motivo", "canal"],
    )

    op.drop_index(
        "uq_plantillas_notificacion_evento_motivo_nulo",
        table_name="plantillas_notificacion",
    )
    op.create_index(
        "uq_plantillas_notificacion_evento_motivo_nulo",
        "plantillas_notificacion",
        ["evento", "canal"],
        unique=True,
        postgresql_where=sa.text("motivo IS NULL"),
    )


def downgrade() -> None:
    # Lossy si ya existen filas EMAIL/WHATSAPP para un evento/motivo que
    # también tiene fila SMS -- recrear la constraint vieja (evento, motivo)
    # chocaría contra esas filas. Aceptado a propósito (mismo criterio que
    # 0032): este downgrade solo es seguro sobre una base que nunca tuvo
    # canales fuera de SMS.
    op.drop_index(
        "uq_plantillas_notificacion_evento_motivo_nulo",
        table_name="plantillas_notificacion",
    )
    op.create_index(
        "uq_plantillas_notificacion_evento_motivo_nulo",
        "plantillas_notificacion",
        ["evento"],
        unique=True,
        postgresql_where=sa.text("motivo IS NULL"),
    )

    op.drop_constraint(
        "uq_plantillas_notificacion_evento_motivo_canal",
        "plantillas_notificacion",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_plantillas_notificacion_evento_motivo",
        "plantillas_notificacion",
        ["evento", "motivo"],
    )

    op.drop_column("plantillas_notificacion", "asunto")
    op.drop_column("plantillas_notificacion", "canal")
