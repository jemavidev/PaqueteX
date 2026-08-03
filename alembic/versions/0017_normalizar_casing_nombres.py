"""normalizar_casing_nombres -- backfill de mayúsculas en texto libre existente

DESCENDIENTE de `0016_password_resets` (`down_revision`). Migración DE DATOS
(sin cambio de esquema): antes de esta fecha, `personas.nombre`,
`ocupantes.nombre`, `usuarios.nombre`, `paquetes.recipient_name` y
`paquetes.guide_number` se guardaban tal cual los tipeó cada ruta -- la misma
Persona podía llegar a tener "Camila", "CAMILA" o "camila " según por dónde
se creó/editó. Los write-sites del dominio (`persona_service`,
`ocupante_service`, `staff_service`, `paquete_service`, `paquete_lifecycle`)
ya normalizan hacia adelante (`app.domain.texto.normalizar_nombre` --
MAYÚSCULAS, espacios colapsados/recortados, mismo tratamiento que
`apartamento.normalizar_terna` ya usaba para conjunto/torre/apartamento);
esta migración pone al día lo que ya existía en la base antes del fix.

`personas.eliminado_en IS NOT NULL` (Personas anonimizadas, ADR-0005) queda
EXCLUIDO a propósito: su `nombre` es el centinela fijo `"Cliente eliminado"`
(`persona_service._NOMBRE_ANONIMIZADO`), no un dato de usuario -- normalizarlo
no aporta nada y rompería los tests que lo pinchean por igualdad exacta.

Cada UPDATE es idempotente (la cláusula WHERE solo toca filas que en efecto
cambian) y puede correr más de una vez sin efecto adicional.

Revision ID: 0017_normalizar_casing_nombres
Revises: 0016_password_resets
Create Date: 2026-08-03
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0017_normalizar_casing_nombres"
down_revision = "0016_password_resets"
branch_labels = None
depends_on = None

_CANON = "UPPER(regexp_replace(TRIM({col}), '\\s+', ' ', 'g'))"


def upgrade() -> None:
    canon_nombre = _CANON.format(col="nombre")
    op.execute(
        f"""
        UPDATE personas
        SET nombre = {canon_nombre}
        WHERE eliminado_en IS NULL AND nombre <> {canon_nombre}
        """
    )
    op.execute(
        f"""
        UPDATE ocupantes
        SET nombre = {canon_nombre}
        WHERE nombre <> {canon_nombre}
        """
    )
    op.execute(
        f"""
        UPDATE usuarios
        SET nombre = {canon_nombre}
        WHERE nombre <> {canon_nombre}
        """
    )

    canon_recipient = _CANON.format(col="recipient_name")
    op.execute(
        f"""
        UPDATE paquetes
        SET recipient_name = {canon_recipient}
        WHERE recipient_name <> {canon_recipient}
        """
    )
    canon_guide = _CANON.format(col="guide_number")
    op.execute(
        f"""
        UPDATE paquetes
        SET guide_number = {canon_guide}
        WHERE guide_number IS NOT NULL AND guide_number <> {canon_guide}
        """
    )


def downgrade() -> None:
    # Migración de datos de un solo sentido: el casing/espaciado original
    # de lo que existía antes del backfill no queda registrado en ningún
    # lado, así que no hay forma de reconstruirlo. No-op a propósito.
    pass
