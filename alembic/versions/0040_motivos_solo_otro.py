"""motivos_cancelacion -- reducir el catálogo inicial a un solo motivo genérico

DESCENDIENTE de `0039_motivos_cancelacion` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). `.scratch/motivos-cancelacion-catalogo`,
conversación en vivo 2026-09-03 (pedido explícito del cliente, tras ver la
pantalla ya construida): 4 motivos separados ("Anuncio erróneo", "Devuelto
al transportador", "No reclamado", "Otro") era más de lo necesario -- un
solo motivo genérico alcanza, ya que "Otro" + su texto libre ya captura la
razón real cuando el STAFF la escribe, o queda genérico si no.

Se borran 3 de las 4 filas sembradas por `0039`, dejando únicamente "Otro"
-- deliberadamente el que sobrevive: su comportamiento de texto libre en el
modal "Cancelar paquete" está hardcodeado al literal "Otro" (JS de
`packages/_resultados.html` + `cancel_action` en `packages.py`), así que
conservarlo intacto evita tocar esa lógica ya probada. Los paquetes ya
cancelados con los otros 3 motivos, y las plantillas de notificación que el
cliente ya haya personalizado para ellos, quedan intactos (borrado duro del
catálogo, mismo criterio ya documentado en `motivo_cancelacion_service.
eliminar_motivo`: `cancel_reason`/`PlantillaNotificacion.motivo` no son FK,
así que no se ven afectados por borrar la fila del catálogo).

Revision ID: 0040_motivos_solo_otro
Revises: 0039_motivos_cancelacion
Create Date: 2026-09-03
"""

import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0040_motivos_solo_otro"
down_revision = "0039_motivos_cancelacion"
branch_labels = None
depends_on = None

_ETIQUETAS_A_BORRAR = ["Anuncio erróneo", "Devuelto al transportador", "No reclamado"]


def upgrade() -> None:
    motivos_cancelacion = sa.table("motivos_cancelacion", sa.column("etiqueta"))
    op.execute(
        motivos_cancelacion.delete().where(
            motivos_cancelacion.c.etiqueta.in_(_ETIQUETAS_A_BORRAR)
        )
    )


def downgrade() -> None:
    # Lossy en orden/timestamp exactos (mismo criterio ya aceptado en
    # 0032/0033/0039): re-siembra las 3 etiquetas solo si no existen ya
    # (un admin pudo haber creado una fila nueva con el mismo texto
    # mientras tanto -- `crear_motivo` no permite duplicados exactos).
    motivos_cancelacion = sa.table(
        "motivos_cancelacion", sa.column("id"), sa.column("etiqueta"), sa.column("creado_en")
    )
    conn = op.get_bind()
    existentes = {
        row[0]
        for row in conn.execute(
            sa.select(motivos_cancelacion.c.etiqueta).where(
                motivos_cancelacion.c.etiqueta.in_(_ETIQUETAS_A_BORRAR)
            )
        )
    }
    ahora = datetime.now(timezone.utc)
    filas = [
        {"id": uuid.uuid4(), "etiqueta": etiqueta, "creado_en": ahora + timedelta(milliseconds=i)}
        for i, etiqueta in enumerate(_ETIQUETAS_A_BORRAR)
        if etiqueta not in existentes
    ]
    if filas:
        op.bulk_insert(motivos_cancelacion, filas)
