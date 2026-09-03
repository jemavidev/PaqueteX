"""motivos_cancelacion -- catálogo editable de motivos de cancelación

DESCENDIENTE de `0038_credencial_historial` (`down_revision`). El árbol
permanece de raíz única (ADR-0002). `.scratch/motivos-cancelacion-catalogo`,
ticket 01: reemplaza el enum Python fijo `MotivoCancelacion` (retirado del
código en el ticket 04 de esa misma rebanada) por una tabla editable desde
`/administracion/notificaciones`.

Siembra las 4 filas que ya existían como enum, con etiquetas legibles
("Anuncio erróneo", en vez del código crudo "ANUNCIO_ERRONEO") y en el mismo
orden que tenía el enum, para que `creado_en` preserve el orden histórico del
picker de cancelación. Cada fila recibe un `creado_en` explícito con un
offset de milisegundos creciente -- `NOW()` de Postgres es estable dentro de
una misma transacción (a diferencia de `clock_timestamp()`), así que 4
`INSERT` sucesivos con el default de columna recibirían el MISMO timestamp y
el orden entre ellos quedaría sin definir.

Además reescribe los valores crudos que ya estaban guardados en
`plantillas_notificacion.motivo` y `paquetes.cancel_reason` (p.ej.
"ANUNCIO_ERRONEO" -> "Anuncio erróneo") para que las plantillas de
notificación que el cliente ya haya personalizado para esos motivos sigan
encontrándose después del cambio -- sin este paso, quedarían huérfanas
(el nuevo código busca por el texto legible, no por el código crudo).
Cualquier `cancel_reason` que no calce con esos 4 valores crudos (texto
libre ya tecleado alguna vez vía "Otro") se deja intacto -- nunca fue un
valor del enum.

Revision ID: 0039_motivos_cancelacion
Revises: 0038_credencial_historial
Create Date: 2026-09-03
"""

import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0039_motivos_cancelacion"
down_revision = "0038_credencial_historial"
branch_labels = None
depends_on = None

# (etiqueta legible, código crudo del enum retirado) -- en el orden original
# del enum `MotivoCancelacion` (`app/domain/paquete.py`).
_MOTIVOS_SEED = [
    ("Anuncio erróneo", "ANUNCIO_ERRONEO"),
    ("Devuelto al transportador", "DEVUELTO_AL_TRANSPORTADOR"),
    ("No reclamado", "NO_RECLAMADO"),
    ("Otro", "OTRO"),
]


def upgrade() -> None:
    op.create_table(
        "motivos_cancelacion",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("etiqueta", sa.String(length=40), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("etiqueta", name="uq_motivos_cancelacion_etiqueta"),
    )

    motivos_cancelacion = sa.table(
        "motivos_cancelacion",
        sa.column("id"),
        sa.column("etiqueta"),
        sa.column("creado_en"),
    )
    ahora = datetime.now(timezone.utc)
    op.bulk_insert(
        motivos_cancelacion,
        [
            {
                "id": uuid.uuid4(),
                "etiqueta": etiqueta,
                "creado_en": ahora + timedelta(milliseconds=indice),
            }
            for indice, (etiqueta, _codigo_crudo) in enumerate(_MOTIVOS_SEED)
        ],
    )

    for etiqueta, codigo_crudo in _MOTIVOS_SEED:
        op.execute(
            sa.text(
                "UPDATE plantillas_notificacion SET motivo = :etiqueta "
                "WHERE motivo = :codigo_crudo"
            ).bindparams(etiqueta=etiqueta, codigo_crudo=codigo_crudo)
        )
        op.execute(
            sa.text(
                "UPDATE paquetes SET cancel_reason = :etiqueta "
                "WHERE cancel_reason = :codigo_crudo"
            ).bindparams(etiqueta=etiqueta, codigo_crudo=codigo_crudo)
        )


def downgrade() -> None:
    # Lossy si ya se crearon/renombraron motivos desde la pantalla de admin
    # (aceptado a propósito, mismo criterio que 0032/0033): solo reescribe de
    # vuelta los 4 valores que esta migración sembró/reescribió; cualquier
    # etiqueta nueva del admin, o texto libre de "Otro", queda tal cual.
    for etiqueta, codigo_crudo in _MOTIVOS_SEED:
        op.execute(
            sa.text(
                "UPDATE plantillas_notificacion SET motivo = :codigo_crudo "
                "WHERE motivo = :etiqueta"
            ).bindparams(etiqueta=etiqueta, codigo_crudo=codigo_crudo)
        )
        op.execute(
            sa.text(
                "UPDATE paquetes SET cancel_reason = :codigo_crudo "
                "WHERE cancel_reason = :etiqueta"
            ).bindparams(etiqueta=etiqueta, codigo_crudo=codigo_crudo)
        )

    op.drop_table("motivos_cancelacion")
