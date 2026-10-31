"""entrega_sin_cobrar -- por qué se entregó un paquete sin cobrar toda la deuda del cliente, y cuánto quedó debiendo

DESCENDIENTE de `0073_motivos_recibir` (`down_revision`). El árbol permanece de raíz única (ADR-0002).
Pedido del cliente 2026-10-31: si el destinatario tiene saldo contra entrega negativo (una deuda), Entregar ahora
obliga a cobrarla -- entregar sin cobrar todo (abono parcial o nada) pide un motivo explícito, que queda en el paquete:
- `paquetes.motivo_no_cobro`: 'sin_efectivo' | 'paga_despues' | 'autorizado' | 'otro', con ': <nota>' si el staff
  escribió una nota (obligatoria solo para 'otro').
- `paquetes.saldo_no_cobrado`: lo que quedó debiendo al entregar (deuda menos el abono).
NULL para todo lo existente y para cualquier entrega sin deuda o cobrada completa. Quién lo hizo ya queda en
`delivered_by_usuario_id`.

Revision ID: 0074_entrega_sin_cobrar
Revises: 0073_motivos_recibir
Create Date: 2026-10-31
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0074_entrega_sin_cobrar"
down_revision = "0073_motivos_recibir"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paquetes", sa.Column("motivo_no_cobro", sa.String(200), nullable=True))
    op.add_column("paquetes", sa.Column("saldo_no_cobrado", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("paquetes", "saldo_no_cobrado")
    op.drop_column("paquetes", "motivo_no_cobro")
