# -*- coding: utf-8 -*-
"""reset_sms_no_anuncio -- filas existentes de SMS activo fuera de ANUNCIADO
se resetean a inactivo (pedido del cliente 2026-08-26)

DESCENDIENTE de `0031_eliminar_segundo_contacto` (`down_revision`). Migración
de DATOS (no de esquema): a partir de ahora, un Residente en `/mis-datos` y
un Staff Operador en `/residentes/{id}` solo pueden activar/desactivar SMS
para el evento ANUNCIADO -- Recibido/Entregado/Cancelado quedan bloqueados
salvo que un ADMIN los toque (ver `preferencia_notificacion_service.
canal_evento_editable`). Cualquier fila que ya tuviera SMS activo en esos 3
eventos (de antes de esta regla) se resetea a inactivo para que nadie quede
con un envío real habilitado que ya no puede ni ver ni apagar por su cuenta
-- si algún caso puntual sí lo necesita, un ADMIN lo reactiva a propósito
después desde la matriz completa.

Revision ID: 0032_reset_sms_no_anuncio
Revises: 0031_eliminar_segundo_contacto
Create Date: 2026-08-26
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0032_reset_sms_no_anuncio"
down_revision = "0031_eliminar_segundo_contacto"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE persona_preferencia_notificacion SET activo = false "
        "WHERE canal = 'SMS' AND evento IN ('RECIBIDO', 'ENTREGADO', 'CANCELADO') "
        "AND activo = true"
    )


def downgrade() -> None:
    # Sin registro de cuáles filas eran `true` antes del reset -- pérdida de
    # datos aceptada a propósito (pedido explícito del cliente), no hay
    # downgrade real posible.
    pass
