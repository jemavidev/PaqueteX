"""eliminar_segundo_contacto -- columna sin ningún uso funcional real

DESCENDIENTE de `0030_ocupante_principal_activo` (`down_revision`). Migración
destructiva de ESQUEMA (drop de columna, con pérdida de datos): `personas.
segundo_contacto` (issue 170, .scratch/pendientes-cliente) -- confirmado antes
de tocar código que ningún flujo real lo leía (ni notificaciones, ni OTP, ni
`announce`/recibir/entregar), no estaba expuesto al propio cliente en
`/mis-datos` (solo staff podía verlo/tocarlo), y su única función era ser un
término extra en la búsqueda de `/residentes`. Pedido explícito del cliente:
"si procede con la eliminación" (columna + código + tests), tras confirmar que
no era requerido en ningún flujo.

A diferencia de `documento`/`tipo_documento` (Grupo 12, Ronda 2 de
`ajustes-post-referencia-funcional`, que se quedaron como columnas muertas sin
migración destructiva por ser "dato histórico neutral"), acá sí se eliminó la
columna -- el cliente pidió explícitamente la limpieza completa.

Revision ID: 0031_eliminar_segundo_contacto
Revises: 0030_ocupante_principal_activo
Create Date: 2026-08-23
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0031_eliminar_segundo_contacto"
down_revision = "0030_ocupante_principal_activo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("personas", "segundo_contacto")


def downgrade() -> None:
    op.add_column(
        "personas", sa.Column("segundo_contacto", sa.String(length=120), nullable=True)
    )
