"""índices de trigramas para contactos_externos.nombre y personas.telefono

DESCENDIENTE de `0052_configuracion_empresa` (`down_revision`). El árbol
permanece de raíz única (ADR-0002).

Auditoría de desempeño (2026-09-19, seguimiento a la migración
`0042_indices_busqueda_paquetes`): ese mismo patrón -- `ILIKE '%texto%'`
con comodín al INICIO, que ningún índice B-tree puede acelerar -- existe
en otros 2 lugares que 0042 no cubrió porque nacieron después o quedaron
afuera del diagnóstico original:

- `contacto_externo_service.py::listar_contactos_externos` busca
  `ContactoExterno.nombre.ilike(f"%{termino}%")` para `/administracion/
  contactos-externos`. Esta tabla nació DESPUÉS de 0042 (import masivo de
  contactos) y nunca recibió el mismo tratamiento -- ya tiene 1.041 filas
  reales en desarrollo (más que las 31 que tenía `paquetes` cuando 0042
  se diagnosticó), sin ningún índice de búsqueda, solo la PK.
- `customers_manage.py::_buscar_residentes` y `saldo_contra_entrega_
  service.py::listar_movimientos_saldo` buscan `Persona.telefono.
  ilike(f"%{termino}%")` -- 0042 sí indexó `personas.{nombre,email,
  whatsapp_usuario}`, pero dejó `telefono` afuera pese a que ya se
  buscaba parcialmente por ese campo en los mismos 2 lugares.

Mismo criterio que 0042: `CONCURRENTLY` (autocommit_block), no refuerzan
ningún invariante de negocio, así que no hay urgencia que justifique
bloquear escrituras mientras se construyen. `pg_trgm` ya está habilitado
desde 0042 -- `CREATE EXTENSION IF NOT EXISTS` es idempotente, se repite
acá solo por si esta migración corriera sola contra una BD que nunca pasó
por 0042 (no debería darse en la práctica, pero es gratis).

Revision ID: 0053_indices_contactos_telefono
Revises: 0052_configuracion_empresa
Create Date: 2026-09-19
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0053_indices_contactos_telefono"
down_revision = "0052_configuracion_empresa"
branch_labels = None
depends_on = None

_INDICES_TRIGRAMA = [
    ("ix_contactos_externos_nombre_trgm", "contactos_externos", "nombre"),
    ("ix_personas_telefono_trgm", "personas", "telefono"),
]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    with op.get_context().autocommit_block():
        for nombre, tabla, columna in _INDICES_TRIGRAMA:
            op.create_index(
                nombre,
                tabla,
                [columna],
                postgresql_using="gin",
                postgresql_ops={columna: "gin_trgm_ops"},
                postgresql_concurrently=True,
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for nombre, tabla, _columna in reversed(_INDICES_TRIGRAMA):
            op.drop_index(nombre, table_name=tabla, postgresql_concurrently=True)
