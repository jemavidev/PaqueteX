"""plantillas_notificacion — índice único parcial para motivo NULL

DESCENDIENTE de `0022_ocupante_confirmado_en` (`down_revision`). El árbol
permanece de raíz única (ADR-0002).

Cierra un hueco real (encontrado en auditoría, `.scratch/pendientes-cliente`):
`uq_plantillas_notificacion_evento_motivo` es un UniqueConstraint sobre
`(evento, motivo)`, pero Postgres trata cada `NULL` como distinto de
cualquier otro `NULL` para efectos de unicidad -- así que esa constraint NO
protege contra dos filas con el mismo `evento` cuando `motivo IS NULL`
(el caso de ANUNCIADO/RECIBIDO/ENTREGADO, que nunca llevan motivo). Si eso
llegara a pasar (solo alcanzable por una carrera real: dos administradores
editando la misma plantilla al mismo instante), `notificacion_service.
construir_mensaje`/`obtener_texto_actual` -- ambos usan `.one_or_none()` --
reventarían con `MultipleResultsFound`.

Mismo patrón que `uq_ocupantes_principal_por_apartamento` (0010): un índice
único PARCIAL cubre exactamente el caso que el UniqueConstraint normal deja
sin proteger. La constraint existente (`evento`, `motivo`) sigue intacta y
sigue protegiendo el caso `motivo` no-nulo (CANCELADO).

Revision ID: 0023_plantilla_motivo_nulo
Revises: 0022_ocupante_confirmado_en
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0023_plantilla_motivo_nulo"
down_revision = "0022_ocupante_confirmado_en"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_plantillas_notificacion_evento_motivo_nulo",
        "plantillas_notificacion",
        ["evento"],
        unique=True,
        postgresql_where=sa.text("motivo IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_plantillas_notificacion_evento_motivo_nulo",
        table_name="plantillas_notificacion",
    )
