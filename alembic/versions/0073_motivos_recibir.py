"""motivos_recibir -- por qué se recibió un paquete sin registrar el apartamento del cliente, o sin fotos

DESCENDIENTE de `0072_fotos_pendientes` (`down_revision`). El árbol permanece de raíz única (ADR-0002).
Pedido del cliente 2026-10-30: los Operadores se saltaban el apartamento al recibir, y "Recibir sin fotos" era una
casilla de un clic. Ahora saltarse cualquiera de los dos pide un motivo explícito, que queda en el paquete:
- `paquetes.sin_apartamento_motivo`: 'etiqueta' | 'ilegible' | 'otro: <texto>'.
- `paquetes.sin_fotos_motivo`: 'camara' | 'no_fotografiable' | 'otro: <texto>' (junto a `recibido_sin_fotos`, 0072).
NULL para todo lo existente. Quién lo hizo ya queda en `received_by_usuario_id`.

Revision ID: 0073_motivos_recibir
Revises: 0072_fotos_pendientes
Create Date: 2026-10-30
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0073_motivos_recibir"
down_revision = "0072_fotos_pendientes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paquetes", sa.Column("sin_apartamento_motivo", sa.String(200), nullable=True))
    op.add_column("paquetes", sa.Column("sin_fotos_motivo", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("paquetes", "sin_fotos_motivo")
    op.drop_column("paquetes", "sin_apartamento_motivo")
