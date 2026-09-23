"""catálogo numerado de fuentes de contactos externos

DESCENDIENTE de `0053_indices_contactos_telefono` (`down_revision`). El árbol
permanece de raíz única (ADR-0002).

`.scratch/pendientes-cliente`, issue 362: la columna "Fuentes" de
`/administracion/contactos-externos` pasa a mostrar números ("01 · 02 · 03") con
su equivalencia arriba de la tabla, y el número tiene que decir cuál fuente se
agregó primero, cuál segundo, etc. Ese orden NO se puede deducir de
`contactos_externos` (las fuentes de un mismo contacto comparten su fecha de
creación), así que se guarda: esta migración crea el catálogo
`fuentes_contactos_externos` (`numero` permanente + `nombre`) y lo RELLENA con
las fuentes que ya existen.

Relleno: el cliente confirmó (2026-09-20) el orden real en que se importaron
las 3 fuentes que hoy tiene: 1 Whatsapp, 2 Paquetes, 3 ACTUALIZACION -- se
respeta ese orden (sin importar mayúsculas). Cualquier OTRA fuente ya existente
(ej. las etiquetas `google_contacts`/`produccion_v1` de los primeros scripts de
importación) queda después, por fecha de su primer contacto y luego por nombre.
Las grafías que difieren solo en mayúsculas se juntan en una sola fuente (gana
la más usada) -- el servicio trata "whatsapp" y "Whatsapp" como la misma. Con la
tabla `contactos_externos` vacía (ambiente nuevo) no se inserta nada: la primera
fuente que se importe recibirá el 1.

`contactos_externos.fuentes` sigue guardando el nombre (sin llave foránea): no
cambia ninguna otra tabla.

Revision ID: 0054_fuentes_contactos_externos
Revises: 0053_indices_contactos_telefono
Create Date: 2026-09-20
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0054_fuentes_contactos_externos"
down_revision = "0053_indices_contactos_telefono"
branch_labels = None
depends_on = None

# Orden real confirmado por el cliente (en minúscula, para comparar sin
# importar la grafía guardada).
_ORDEN_CONFIRMADO = ("whatsapp", "paquetes", "actualizacion")


def upgrade() -> None:
    op.create_table(
        "fuentes_contactos_externos",
        sa.Column("numero", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("nombre", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("nombre", name="uq_fuentes_contactos_externos_nombre"),
    )

    conn = op.get_bind()
    filas = conn.execute(
        sa.text(
            "SELECT f AS nombre, COUNT(*) AS contactos, MIN(c.created_at) AS primera "
            "FROM contactos_externos c, unnest(c.fuentes) AS f "
            "GROUP BY f"
        )
    ).all()

    # Agrupar por nombre en minúscula: una fuente por grafía ignorando caso.
    grupos: dict[str, dict] = {}
    for nombre, contactos, primera in filas:
        g = grupos.setdefault(nombre.lower(), {"variantes": [], "primera": primera})
        g["variantes"].append((-contactos, nombre))  # más usada primero
        g["primera"] = min(g["primera"], primera)

    def orden(item):
        clave, g = item
        rango = _ORDEN_CONFIRMADO.index(clave) if clave in _ORDEN_CONFIRMADO else len(_ORDEN_CONFIRMADO)
        return (rango, g["primera"], clave)

    for numero, (_clave, g) in enumerate(sorted(grupos.items(), key=orden), start=1):
        nombre = sorted(g["variantes"])[0][1]
        conn.execute(
            sa.text(
                "INSERT INTO fuentes_contactos_externos (numero, nombre, created_at) "
                "VALUES (:numero, :nombre, now())"
            ),
            {"numero": numero, "nombre": nombre},
        )


def downgrade() -> None:
    # Lossy si la numeración ya se usó (mismo criterio que `0051`): el catálogo
    # se descarta; `contactos_externos.fuentes` conserva los nombres, así que
    # ningún contacto pierde su fuente -- solo se pierde el NÚMERO asignado.
    op.drop_table("fuentes_contactos_externos")
