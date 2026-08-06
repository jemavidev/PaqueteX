"""ocupantes — índice único parcial: a lo sumo un Ocupante activo por Persona

DESCENDIENTE de `0023_plantilla_motivo_nulo` (`down_revision`). El
árbol permanece de raíz única (ADR-0002).

Cierra a nivel de base de datos el invariante "un teléfono, un Apartamento
activo a la vez" (`.scratch/mis-datos`, ticket 02) -- hasta ahora solo lo
garantizaba `ocupante_service._persona_ya_es_ocupante_activo`, una consulta
previa a nivel de aplicación sin respaldo real en el esquema. Eso dejaba una
carrera abierta: dos altas concurrentes con el mismo teléfono podían colar 2
filas activas del mismo `persona_id` (en el mismo Apartamento o en dos
distintos), reventando cualquier llamada posterior a `ocupante_activo_de_
persona` (`.one_or_none()`) con `MultipleResultsFound` -- mismo bug que ya
se corrigió hoy para el caso secuencial (mismo teléfono agregado dos veces
seguidas al mismo Apartamento), ahora cerrado también para el caso
concurrente.

Mismo patrón que `uq_ocupantes_principal_por_apartamento` (0010): índice
único parcial, no una constraint plana -- `persona_id` es nullable (Ocupante
sin Teléfono propio) y un mismo Teléfono puede tener VARIOS Ocupante
históricos (dados de baja, `desvinculado_en` no nulo); el índice solo cubre
las filas donde de verdad importa la unicidad.

Revision ID: 0024_ocupante_persona_unica
Revises: 0023_plantilla_motivo_nulo
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0024_ocupante_persona_unica"
down_revision = "0023_plantilla_motivo_nulo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_ocupantes_persona_activo",
        "ocupantes",
        ["persona_id"],
        unique=True,
        postgresql_where=sa.text("persona_id IS NOT NULL AND desvinculado_en IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_ocupantes_persona_activo", table_name="ocupantes")
