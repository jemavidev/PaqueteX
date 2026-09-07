"""índices para la búsqueda de texto libre de /paquetes (pg_trgm + FK)

DESCENDIENTE de `0041_cancelado_una_plantilla` (`down_revision`). El árbol
permanece de raíz única (ADR-0002).

Diagnóstico de rendimiento (.scratch/pendientes-cliente, 2026-09-06 --
"verifica que todas las vistas carguen en tiempos adecuados, /paquetes ha
estado lenta"): `condiciones_busqueda_paquetes` (búsqueda de texto libre de
`/paquetes`, reusada por `/residentes` vía `contar_paquetes_de_persona` y
por el badge "Mostrar conexiones") arma un `ILIKE '%texto%'` -- comodín al
INICIO -- sobre varias columnas de `paquetes`. Ningún índice B-tree puede
acelerar un comodín al inicio; sin `pg_trgm`, cada búsqueda es un full
table scan.

Medido contra 50.000 paquetes sintéticos (volumen simulado -- la tabla real
solo tenía 31 filas al momento de este diagnóstico, insuficiente para que
el problema doliera todavía, pero el cliente proyecta ~20-30 paquetes
nuevos/día + ~500 cambios de estado/día, así que el crecimiento es real):
~190ms por escaneo completo, hasta 3 escaneos independientes en una sola
petición a `/paquetes?q=...` -- ~540ms totales. En `/residentes`, la
píldora "N paquetes" (issue 321) repite ese mismo escaneo UNA VEZ POR
RESIDENTE de la página (20/página) -- medido en ~3 segundos por carga.

Este índice de trigramas por sí solo NO alcanzaba (verificado con
`EXPLAIN ANALYZE` + `enable_seqscan=off`): la consulta original mezclaba,
en un solo OR, columnas de `paquetes` CON columnas de `personas` después de
un `outerjoin` -- un OR que cruza dos tablas tras un join fuerza a Postgres
a escanear `paquetes` completa sin importar qué índices existan, porque no
puede decidir por-tabla qué filas descartar. La reescritura de
`condiciones_busqueda_paquetes` (`paquete_service.py`, mismo commit)
resuelve la parte de `Persona` en una consulta aparte (tabla chica) y la
traduce a condiciones EXCLUSIVAS de `Paquete` -- de ahí que además de los
índices GIN de trigramas haga falta un índice plano en
`announced_by_persona_id` (hoy sin ninguno pese a ser FK), que es lo que
esa traducción usa para volver a `Paquete` sin el join.

Índices `CONCURRENTLY` (fuera de la transacción de la migración, ver
`autocommit_block()` abajo) -- son puramente de apoyo a consulta (mismo
criterio que 0025), no refuerzan ningún invariante de negocio, así que no
hay urgencia que justifique bloquear escrituras mientras se construyen: en
una tabla ya grande, construirlos sin `CONCURRENTLY` bloquearía
anunciar/recibir en el mostrador durante todo ese tiempo.

Revision ID: 0042_indices_busqueda_paquetes
Revises: 0041_cancelado_una_plantilla
Create Date: 2026-09-06
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0042_indices_busqueda_paquetes"
down_revision = "0041_cancelado_una_plantilla"
branch_labels = None
depends_on = None

# (nombre_indice, tabla, columna, usar_gin_trigram) -- una columna por
# índice, mismo criterio que 0025 ("índices planos... no refuerzan ningún
# invariante"). Los GIN cubren EXACTAMENTE las columnas que
# `condiciones_busqueda_paquetes` compara con `ILIKE '%...%'` tras la
# reescritura (ya no incluye columnas de `personas` -- esas se resuelven
# aparte, y `personas` ya tenía sus propios candidatos de columnas de texto
# libre: `nombre`/`email`/`whatsapp_usuario`, cubiertos igual porque la
# consulta chica que las reemplaza en el join también las busca por ILIKE).
# El último, sobre `announced_by_persona_id`, es un B-tree normal -- lo usa
# esa misma reescritura para reconectar con `Paquete` por igualdad exacta,
# sin ILIKE de por medio.
_INDICES_TRIGRAMA = [
    ("ix_paquetes_access_code_trgm", "paquetes", "access_code"),
    ("ix_paquetes_guide_number_trgm", "paquetes", "guide_number"),
    ("ix_paquetes_recipient_name_trgm", "paquetes", "recipient_name"),
    ("ix_paquetes_snapshot_torre_trgm", "paquetes", "snapshot_torre"),
    ("ix_paquetes_snapshot_apartamento_trgm", "paquetes", "snapshot_apartamento"),
    ("ix_paquetes_recipient_phone_trgm", "paquetes", "recipient_phone"),
    ("ix_paquetes_announced_by_phone_trgm", "paquetes", "announced_by_phone"),
    ("ix_personas_nombre_trgm", "personas", "nombre"),
    ("ix_personas_email_trgm", "personas", "email"),
    ("ix_personas_whatsapp_usuario_trgm", "personas", "whatsapp_usuario"),
]

_INDICE_FK = ("ix_paquetes_announced_by_persona_id", "paquetes", "announced_by_persona_id")


def upgrade() -> None:
    # DDL liviano, no bloquea nada -- corre en la transacción normal de la
    # migración, antes de pasar a modo autocommit para los índices.
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
        nombre, tabla, columna = _INDICE_FK
        op.create_index(nombre, tabla, [columna], postgresql_concurrently=True)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        nombre, tabla, _columna = _INDICE_FK
        op.drop_index(nombre, table_name=tabla, postgresql_concurrently=True)
        for nombre, tabla, _columna in reversed(_INDICES_TRIGRAMA):
            op.drop_index(nombre, table_name=tabla, postgresql_concurrently=True)
    # `pg_trgm` NO se remueve -- quitar una extensión de BD es una decisión
    # de infraestructura, no algo que el downgrade de un feature puntual
    # deba decidir por su cuenta (mismo criterio que 0032/0039 con datos:
    # mejor dejar un rastro inerte que borrar de más).
