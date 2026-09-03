"""plantillas_notificacion -- CANCELADO pasa a un solo mensaje (motivo NULL)

DESCENDIENTE de `0040_motivos_solo_otro` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). `.scratch/motivos-cancelacion-catalogo`,
conversación en vivo 2026-09-03 (pedido explícito del cliente): el motivo
elegido al cancelar un paquete NO selecciona una plantilla distinta -- ya se
resuelve dentro del texto vía la variable `{motivo}` (`notificacion_service.
_variables`), igual que `{recipient_name}`/`{access_code}`. CANCELADO pasa a
tener un único mensaje por canal, igual que ANUNCIADO/RECIBIDO/ENTREGADO
(que siempre tuvieron `motivo IS NULL`).

Por cada canal (SMS/EMAIL/WHATSAPP) puede haber varias filas CANCELADO ya
guardadas (una por motivo del enum/catálogo viejo). Se promueve UNA a
`motivo = NULL` -- la de etiqueta "Otro" si existe para ese canal (el único
motivo que sobrevivió a la reducción de `0040`), si no la primera que haya
por `id`. Las demás quedan intactas, huérfanas (mismo criterio ya aceptado
para el catálogo en `motivo_cancelacion_service.eliminar_motivo`: no se
borran, solo dejan de mostrarse) -- a propósito, para no violar la FK de
`plantillas_notificacion_historial.plantilla_id` (sin `ON DELETE CASCADE`)
que ya pueda apuntarles.

Revision ID: 0041_cancelado_una_plantilla
Revises: 0040_motivos_solo_otro
Create Date: 2026-09-03
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0041_cancelado_una_plantilla"
down_revision = "0040_motivos_solo_otro"
branch_labels = None
depends_on = None

_CANALES = ["SMS", "EMAIL", "WHATSAPP"]


def upgrade() -> None:
    conn = op.get_bind()
    plantillas = sa.table(
        "plantillas_notificacion",
        sa.column("id"),
        sa.column("evento"),
        sa.column("motivo"),
        sa.column("canal"),
    )

    for canal in _CANALES:
        filas = conn.execute(
            sa.select(plantillas.c.id, plantillas.c.motivo)
            .where(plantillas.c.evento == "CANCELADO", plantillas.c.canal == canal)
            .order_by(plantillas.c.id)
        ).all()
        if not filas:
            continue
        elegida = next((f for f in filas if f.motivo == "Otro"), filas[0])
        conn.execute(
            plantillas.update().where(plantillas.c.id == elegida.id).values(motivo=None)
        )


def downgrade() -> None:
    # Lossy (mismo criterio ya aceptado en 0032/0033/0039/0040): no hay forma
    # de saber qué motivo tenía originalmente la fila promovida a NULL sin
    # guardarlo aparte, y el resto de las filas (huérfanas) ya conservaban
    # su motivo original sin tocar. Vuelve a poner motivo="Otro" -- el
    # comportamiento previo a este cambio queda restaurado en la práctica
    # (era el único motivo vivo en el catálogo al momento de este cambio).
    conn = op.get_bind()
    plantillas = sa.table(
        "plantillas_notificacion",
        sa.column("evento"),
        sa.column("motivo"),
    )
    conn.execute(
        plantillas.update()
        .where(plantillas.c.evento == "CANCELADO", plantillas.c.motivo.is_(None))
        .values(motivo="Otro")
    )
