"""paquete_posicion -- Posición de almacenamiento al Recibir

DESCENDIENTE de `0065_operaciones_respaldo` (`down_revision`). El árbol permanece de raíz única (ADR-0002).
`.scratch/posicion-almacenamiento`, ticket 01: el compartimento del estante (11…72) donde el Operador guarda el
Paquete al Recibir. Nullable y sin backfill: los paquetes previos y los importados de v1 quedan "Sin ubicación".
El CHECK repite LITERAL la lista de `app.domain.posicion` (una migración es historia congelada: no importa
código de la app que puede cambiar después); `test_parity_esquema_orm` vigila que no diverjan.

Revision ID: 0066_paquete_posicion
Revises: 0065_operaciones_respaldo
Create Date: 2026-09-26
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0066_paquete_posicion"
down_revision = "0065_operaciones_respaldo"
branch_labels = None
depends_on = None

_CHECK = (
    "posicion IS NULL OR posicion IN "
    "('11', '12', '21', '22', '31', '32', '41', '42', '51', '52', '61', '62', '71', '72')"
)


def upgrade() -> None:
    op.add_column("paquetes", sa.Column("posicion", sa.String(length=2), nullable=True))
    op.create_check_constraint("ck_paquetes_posicion_valida", "paquetes", _CHECK)


def downgrade() -> None:
    op.drop_constraint("ck_paquetes_posicion_valida", "paquetes", type_="check")
    op.drop_column("paquetes", "posicion")
