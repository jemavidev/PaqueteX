"""seed_catalogo_apartamentos — 804 unidades reales del conjunto (10 torres)

DESCENDIENTE de `0020_configuracion_conjunto` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). Siembra el catálogo CERRADO de las 804
unidades verificadas y corregidas con el cliente (Torre 3, Piso 3: el `303`
duplicado del listado original se corrigió a `301-308`, 8 unidades como su
torre gemela, Torre 8) --
`.scratch/apartamento-catalogo-confirmacion/spec.md`, sección "Catálogo
completo". Puramente aditiva: en el momento en que esta migración corrió,
`apartamento_service.get_or_create_apartamento` todavía creaba bajo demanda
(el catálogo cerrado -- `resolver_apartamento`, que reemplaza esa función --
lo trae el ticket 03, rebanada aparte y posterior).

Cada piso numera sus apartamentos como `piso*100 + i` para `i` en
`1..cantidad_del_piso`, sin huecos -- así lo confirma cada fila del listado
original (p.ej. Piso 6 con 6 unidades = 601..606, Piso 13 con 4 = 1301..1304),
sin importar el orden en que el cliente las escribió. `conjunto` se lee de
`configuracion_conjunto` (fila si ya existe, si no el default histórico
`EL CLUB` -- mismo fallback que `configuracion_conjunto_service.
obtener_nombre_conjunto`, pero como SQL puro: esta migración no importa
código de la app).

Revision ID: 0021_seed_catalogo_apartamentos
Revises: 0020_configuracion_conjunto
Create Date: 2026-08-04
"""

import uuid

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0021_seed_catalogo_apartamentos"
down_revision = "0020_configuracion_conjunto"
branch_labels = None
depends_on = None

_NOMBRE_CONJUNTO_POR_DEFECTO = "EL CLUB"

# {piso: cantidad_de_unidades} -- cada piso son las unidades piso*100+1..+cantidad.
_TORRE_CHICA = {1: 6, 2: 6, 3: 6, 4: 6, 5: 6, 6: 6, 7: 2}  # Torres 1 y 10 (38)
_TORRE_MEDIANA = {  # Torres 2 y 9 (56)
    1: 6, 2: 6, 3: 6, 4: 6, 5: 6, 6: 6, 7: 6, 8: 6, 9: 6, 10: 2,
}
_TORRE_GRANDE_TOPE_4 = {  # Torres 3 y 8 (100) -- Piso 13 con solo 4 unidades
    1: 8, 2: 8, 3: 8, 4: 8, 5: 8, 6: 8, 7: 8, 8: 8, 9: 8, 10: 8, 11: 8, 12: 8, 13: 4,
}
_TORRE_GRANDE_COMPLETA = {  # Torres 4, 5, 6, 7 (104) -- Piso 13 completo (8)
    1: 8, 2: 8, 3: 8, 4: 8, 5: 8, 6: 8, 7: 8, 8: 8, 9: 8, 10: 8, 11: 8, 12: 8, 13: 8,
}

_TORRES = {
    "TORRE 1": _TORRE_CHICA,
    "TORRE 2": _TORRE_MEDIANA,
    "TORRE 3": _TORRE_GRANDE_TOPE_4,
    "TORRE 4": _TORRE_GRANDE_COMPLETA,
    "TORRE 5": _TORRE_GRANDE_COMPLETA,
    "TORRE 6": _TORRE_GRANDE_COMPLETA,
    "TORRE 7": _TORRE_GRANDE_COMPLETA,
    "TORRE 8": _TORRE_GRANDE_TOPE_4,
    "TORRE 9": _TORRE_MEDIANA,
    "TORRE 10": _TORRE_CHICA,
}


def _ternas_torre_apartamento() -> list[tuple[str, str]]:
    ternas = []
    for torre, pisos in _TORRES.items():
        for piso, cantidad in pisos.items():
            for i in range(1, cantidad + 1):
                ternas.append((torre, str(piso * 100 + i)))
    return ternas


def _filas_catalogo(conjunto: str) -> list[dict]:
    return [
        {"id": uuid.uuid4(), "conjunto": conjunto, "torre": torre, "apartamento": apto}
        for torre, apto in _ternas_torre_apartamento()
    ]


def upgrade() -> None:
    conn = op.get_bind()
    conjunto = conn.execute(
        sa.text("SELECT nombre FROM configuracion_conjunto LIMIT 1")
    ).scalar()
    if not conjunto:
        conjunto = _NOMBRE_CONJUNTO_POR_DEFECTO

    apartamentos = sa.table(
        "apartamentos",
        sa.column("id"),
        sa.column("conjunto"),
        sa.column("torre"),
        sa.column("apartamento"),
    )
    op.bulk_insert(apartamentos, _filas_catalogo(conjunto))


def downgrade() -> None:
    # Borra por (torre, apartamento) -- no por `conjunto`, que pudo haber
    # sido renombrado después del seed -- son exactamente las 804 ternas que
    # esta migración insertó, ni una fila que otra migración/servicio haya
    # creado por su cuenta.
    apartamentos = sa.table(
        "apartamentos", sa.column("torre"), sa.column("apartamento")
    )
    op.execute(
        apartamentos.delete().where(
            sa.tuple_(apartamentos.c.torre, apartamentos.c.apartamento).in_(
                _ternas_torre_apartamento()
            )
        )
    )
