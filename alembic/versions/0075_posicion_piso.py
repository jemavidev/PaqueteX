"""posicion_piso -- la Posición "00" = EL PISO

DESCENDIENTE de `0074_entrega_sin_cobrar` (`down_revision`). El árbol permanece de raíz única (ADR-0002).
Pedido del cliente 2026-10-31: además de las 14 Posiciones del estante (11…72), "00" para los paquetes que se guardan
en el piso. Solo cambia el CHECK de `paquetes.posicion` (creado en `0066_paquete_posicion`); la columna no cambia.
Mismo criterio que 0066: el CHECK repite LITERAL la lista (una migración es historia congelada).

Downgrade: el CHECK viejo no admite "00" -- los paquetes que estén en el piso quedan "Sin ubicación" (NULL) antes de
restaurarlo. Esa pérdida es aceptada: es solo dónde está guardado el paquete, y bajar esta migración es excepcional.

Revision ID: 0075_posicion_piso
Revises: 0074_entrega_sin_cobrar
Create Date: 2026-10-31
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0075_posicion_piso"
down_revision = "0074_entrega_sin_cobrar"
branch_labels = None
depends_on = None

_NOMBRE = "ck_paquetes_posicion_valida"

_CHECK_CON_PISO = (
    "posicion IS NULL OR posicion IN "
    "('00', '11', '12', '21', '22', '31', '32', '41', '42', '51', '52', '61', '62', '71', '72')"
)

_CHECK_SIN_PISO = (
    "posicion IS NULL OR posicion IN "
    "('11', '12', '21', '22', '31', '32', '41', '42', '51', '52', '61', '62', '71', '72')"
)


def upgrade() -> None:
    op.drop_constraint(_NOMBRE, "paquetes", type_="check")
    op.create_check_constraint(_NOMBRE, "paquetes", _CHECK_CON_PISO)


def downgrade() -> None:
    op.execute("UPDATE paquetes SET posicion = NULL WHERE posicion = '00'")
    op.drop_constraint(_NOMBRE, "paquetes", type_="check")
    op.create_check_constraint(_NOMBRE, "paquetes", _CHECK_SIN_PISO)
