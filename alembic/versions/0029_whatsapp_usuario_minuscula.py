"""normalizar_whatsapp_usuario_minuscula -- backfill a minúscula del usuario de WhatsApp existente

DESCENDIENTE de `0028_paquete_phone_nullable` (`down_revision`). Migración DE DATOS
(sin cambio de esquema), mismo espíritu que `0017_normalizar_casing_nombres`: antes
de esta fecha, `personas.whatsapp_usuario` se guardaba tal cual lo tipeó cada ruta
-- la misma Persona podía terminar con "JesusVillalobos" en un lado y
"jesusvillalobos" en otro, sin que el sistema los reconociera como el mismo
usuario (issue 162, .scratch/pendientes-cliente: Meta identifica un usuario de
WhatsApp SIN distinguir mayúsculas de minúsculas). El write-site del dominio
(`app.domain.persona_service._normalizar_whatsapp_usuario`) ya normaliza hacia
adelante; esta migración pone al día lo que ya existía en la base antes del fix.

El UPDATE es idempotente (la cláusula WHERE solo toca filas que en efecto
cambian) y puede correr más de una vez sin efecto adicional. Sin manejo especial
de colisión a propósito (mismo criterio que 0017): si dos Personas ya
distintas terminaran con el mismo `whatsapp_usuario` en minúscula, el índice
único parcial (`uq_personas_whatsapp_usuario`) rechaza el UPDATE en seco --
seria una corrección de datos aparte (fusionar Personas), no algo para
resolver en silencio acá.

Revision ID: 0029_whatsapp_usuario_minuscula
Revises: 0028_paquete_phone_nullable
Create Date: 2026-08-23
"""

from alembic import op

# revision identifiers, used by Alembic.
# Nombre corto a propósito: `alembic_version.version_num` es VARCHAR(32) --
# un id más largo (se probó "0029_normalizar_whatsapp_usuario_minuscula",
# 42 caracteres) revienta el UPDATE de esa tabla con "value too long for
# type character varying(32)" (bug real encontrado corriendo la suite).
revision = "0029_whatsapp_usuario_minuscula"
down_revision = "0028_paquete_phone_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE personas
        SET whatsapp_usuario = LOWER(whatsapp_usuario)
        WHERE whatsapp_usuario IS NOT NULL AND whatsapp_usuario <> LOWER(whatsapp_usuario)
        """
    )


def downgrade() -> None:
    # Migración de datos de un solo sentido: el casing original de lo que
    # existía antes del backfill no queda registrado en ningún lado, así
    # que no hay forma de reconstruirlo. No-op a propósito.
    pass
