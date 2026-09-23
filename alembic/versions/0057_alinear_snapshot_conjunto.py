"""alinear snapshot_conjunto -- paquetes con el nombre viejo del Conjunto tras un renombre

DESCENDIENTE de `0056_costo_promedio_sms` (`down_revision`). El árbol permanece
de raíz única (ADR-0002). Issue 378 (`.scratch/pendientes-cliente`): hasta este
arreglo, `renombrar_conjunto` actualizaba `apartamentos.conjunto` pero no
`paquetes.snapshot_conjunto`, así que los paquetes anteriores a un renombre
quedaron con el nombre viejo y ninguna búsqueda por su terna encontraba ya su
unidad ("Este paquete no tiene apartamento resuelto en su snapshot."). Solo
datos, sin cambios de esquema: si el catálogo tiene UN único Conjunto, todo
paquete con otro `snapshot_conjunto` (no nulo) pasa a ese nombre. Con más de un
Conjunto no se toca nada (no habría forma segura de saber cuál corresponde).
Sin downgrade de datos: el nombre viejo no se puede reconstruir, y volver a él
reintroduciría el defecto.

Revision ID: 0057_alinear_snapshot_conjunto
Revises: 0056_costo_promedio_sms
Create Date: 2026-09-22
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0057_alinear_snapshot_conjunto"
down_revision = "0056_costo_promedio_sms"
branch_labels = None
depends_on = None


def alinear_snapshot_conjunto(conexion) -> None:
    """Separada de `upgrade()` para que la prueba de datos la corra sobre su propia conexión."""
    conjuntos = [fila[0] for fila in conexion.execute(sa.text("SELECT DISTINCT conjunto FROM apartamentos"))]
    if len(conjuntos) != 1:
        return
    conexion.execute(
        sa.text(
            "UPDATE paquetes SET snapshot_conjunto = :conjunto "
            "WHERE snapshot_conjunto IS NOT NULL AND snapshot_conjunto <> :conjunto"
        ),
        {"conjunto": conjuntos[0]},
    )


def upgrade() -> None:
    alinear_snapshot_conjunto(op.get_bind())


def downgrade() -> None:
    pass
