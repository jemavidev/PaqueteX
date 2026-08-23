"""ocupante_principal_activo -- el índice único de Principal por Apartamento ahora exige activo

DESCENDIENTE de `0029_whatsapp_usuario_minuscula` (`down_revision`). Migración de
ESQUEMA (recrea un índice, sin tocar datos): `uq_ocupantes_principal_por_
apartamento` (0010) garantizaba "máximo 1 Ocupante `es_principal` por
Apartamento", pero SIN filtrar `desvinculado_en IS NULL` -- un Principal ya dado
de baja (su fila conserva `es_principal=True` como historial, `dar_de_baja_
ocupante` nunca la limpia) contaba igual que uno activo. Consecuencia real
(issue 166, .scratch/pendientes-cliente, bug reportado en vivo): una unidad que
alguna vez tuvo Principal y luego se vació por completo quedaba "atascada" --
nadie nuevo podía volver a promoverse ahí NUNCA, ni por `confirmar_ocupante` ni
por `promover_al_recibir` (que además chocaban con este mismo índice al
intentarlo, `UniqueViolation`). Mismo criterio que ya usaba el índice hermano
`uq_ocupantes_persona_activo` (0024), que sí filtraba activos desde el inicio.

Revision ID: 0030_ocupante_principal_activo
Revises: 0029_whatsapp_usuario_minuscula
Create Date: 2026-08-23
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0030_ocupante_principal_activo"
down_revision = "0029_whatsapp_usuario_minuscula"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_ocupantes_principal_por_apartamento", table_name="ocupantes")
    op.create_index(
        "uq_ocupantes_principal_por_apartamento",
        "ocupantes",
        ["apartamento_id"],
        unique=True,
        postgresql_where=sa.text("es_principal AND desvinculado_en IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_ocupantes_principal_por_apartamento", table_name="ocupantes")
    op.create_index(
        "uq_ocupantes_principal_por_apartamento",
        "ocupantes",
        ["apartamento_id"],
        unique=True,
        postgresql_where=sa.text("es_principal"),
    )
