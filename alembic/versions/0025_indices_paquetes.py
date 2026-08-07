"""índices de consulta en paquetes/paquete_fotos

DESCENDIENTE de `0024_ocupante_persona_unica` (`down_revision`). El árbol
permanece de raíz única (ADR-0002).

Auditoría de base de datos (.scratch/pendientes-cliente, 2026-08-07): estas
columnas se filtran/ordenan/joinean en código real pero no tenían índice.
La más urgente es `guide_number` -- filtro de `/consultar`, la única vista
PÚBLICA sin sesión (`app/web/routes/search.py`), así que cada búsqueda por
número de guía era un full table scan. Las demás cubren `/paquetes` (staff,
`app/web/routes/packages.py`) y `/mis-paquetes` (cliente,
`app/web/routes/customer_paquetes.py`): `announced_by_phone`/
`recipient_phone` (`.in_()` por teléfonos del apartamento), `estado`
(filtro por pestaña), `announced_at` (`order_by` en las dos vistas).
`paquete_fotos.paquete_id` es la FK que respalda `listar_fotos` -- Postgres
no indexa una FK automáticamente, y se consulta una vez por paquete en
ambas vistas.

Índices planos, no parciales ni únicos -- son puro apoyo de consulta, no
refuerzan ningún invariante de negocio (a diferencia de los índices únicos
parciales de 0010/0024).

Revision ID: 0025_indices_paquetes
Revises: 0024_ocupante_persona_unica
Create Date: 2026-08-07
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0025_indices_paquetes"
down_revision = "0024_ocupante_persona_unica"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_paquetes_guide_number", "paquetes", ["guide_number"])
    op.create_index(
        "ix_paquetes_announced_by_phone", "paquetes", ["announced_by_phone"]
    )
    op.create_index("ix_paquetes_recipient_phone", "paquetes", ["recipient_phone"])
    op.create_index("ix_paquetes_estado", "paquetes", ["estado"])
    op.create_index("ix_paquetes_announced_at", "paquetes", ["announced_at"])
    op.create_index(
        "ix_paquete_fotos_paquete_id", "paquete_fotos", ["paquete_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_paquete_fotos_paquete_id", table_name="paquete_fotos")
    op.drop_index("ix_paquetes_announced_at", table_name="paquetes")
    op.drop_index("ix_paquetes_estado", table_name="paquetes")
    op.drop_index("ix_paquetes_recipient_phone", table_name="paquetes")
    op.drop_index("ix_paquetes_announced_by_phone", table_name="paquetes")
    op.drop_index("ix_paquetes_guide_number", table_name="paquetes")
