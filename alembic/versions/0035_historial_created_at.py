"""plantillas_notificacion_historial -- corrige creado_en -> created_at

DESCENDIENTE de `0034_plantilla_historial` (`down_revision`). El árbol
permanece de raíz única (ADR-0002).

Bug real reportado por el cliente en vivo (2026-08-28, /diagnosing-bugs):
guardar cualquier plantilla en `/administracion/notificaciones` tiraba 500
-- `UndefinedColumn: column "created_at" of relation
"plantillas_notificacion_historial" does not exist`.

Causa raíz: la migración `0034_plantilla_historial` ORIGINALMENTE creaba la
columna `creado_en` (español, inconsistente con `created_at`/`updated_at`
que usa el resto del dominio para este mismo rol). Un code-review posterior
la corrigió editando el ARCHIVO de la migración 0034 in-place para que
creara `created_at` en vez de `creado_en` -- pero para cualquier base de
datos que YA había corrido la versión original de 0034 antes de ese
edit (como el Postgres persistente de desarrollo local), la tabla física
se quedó con `creado_en`: Alembic solo registra qué revisión ya corrió
(`alembic_version`), nunca vuelve a ejecutar una migración ya aplicada
aunque su archivo cambie después. El Postgres EFÍMERO de la suite de tests
siempre se construye desde cero con el archivo de 0034 ya corregido, por
eso los tests seguían en verde mientras el ambiente real fallaba.

Lección: nunca editar una migración ya aplicada a una base real -- corregir
hacia adelante con una migración nueva, como esta. El bloque condicional de
abajo hace esta migración segura en AMBOS escenarios: una BD que ya corrió
la 0034 original (tiene `creado_en`, se renombra) y una BD que corre la
0034 ya corregida por primera vez (ya tiene `created_at`, no hay nada que
hacer).

Revision ID: 0035_historial_created_at
Revises: 0034_plantilla_historial
Create Date: 2026-08-28
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0035_historial_created_at"
down_revision = "0034_plantilla_historial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'plantillas_notificacion_historial'
                  AND column_name = 'creado_en'
            ) THEN
                ALTER TABLE plantillas_notificacion_historial
                    RENAME COLUMN creado_en TO created_at;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Sin registro de qué nombre tenía la columna antes de esta migración en
    # CADA base -- downgrade siempre deja `created_at` (el nombre correcto),
    # nunca vuelve a `creado_en`. Mismo criterio que 0032: un downgrade que
    # reintroduce a propósito el bug que esta migración corrige no tendría
    # sentido.
    pass
