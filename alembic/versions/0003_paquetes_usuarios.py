"""paquetes + usuarios — anunciar Paquete con snapshot congelado

DESCENDIENTE de `0002_apartamentos` (`down_revision = "0002_apartamentos"`). El
árbol permanece de raíz única (ADR-0002): esta migración cuelga de la anterior,
NO la edita. Añade:

  - `usuarios` (staff): esqueleto mínimo (rol ADMIN/OPERADOR), target de las
    FK-de-actor del Paquete. Sin columnas de credencial (rebanada de auth).
  - `paquetes`: la foto INMUTABLE del contexto de entrega congelada al anunciar
    (ADR-0001). Anunciante por FK a `personas` + `announced_by_phone` congelado;
    destinatario congelado (`recipient_name`/`recipient_phone`); snapshot del
    apartamento como TEXTO copiado (`snapshot_*`, nunca FK); enum `estado`
    VARCHAR-backed; `guide_number` nullable; llaves de negocio únicas; timestamps
    y FK-actor nullable por transición.

Constraints con nombre explícito e IDÉNTICO al del ORM (`app.domain.usuario`,
`app.domain.paquete`) para que el guard de paridad esquema↔ORM no reporte drift.

Revision ID: 0003_paquetes
Revises: 0002_apartamentos
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0003_paquetes"
down_revision = "0002_apartamentos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- usuarios (staff) --------------------------------------------------- #
    op.create_table(
        "usuarios",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        # Rol VARCHAR-backed (Enum native_enum=False en el ORM).
        sa.Column("rol", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # --- paquetes ----------------------------------------------------------- #
    op.create_table(
        "paquetes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # Llaves de negocio legibles.
        sa.Column("tracking_number", sa.String(length=50), nullable=False),
        sa.Column("access_code", sa.String(length=20), nullable=False),
        sa.Column("guide_number", sa.String(length=50), nullable=True),
        # Anunciante: FK a Persona + teléfono congelado.
        sa.Column("announced_by_persona_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("announced_by_phone", sa.String(length=20), nullable=False),
        # Destinatario: snapshot congelado (nunca FK).
        sa.Column("recipient_name", sa.String(length=120), nullable=False),
        sa.Column("recipient_phone", sa.String(length=20), nullable=True),
        # Snapshot de apartamento: terna copiada como TEXTO (ADR-0001).
        sa.Column("snapshot_conjunto", sa.String(length=120), nullable=True),
        sa.Column("snapshot_torre", sa.String(length=60), nullable=True),
        sa.Column("snapshot_apartamento", sa.String(length=60), nullable=True),
        # Estado del ciclo de vida (VARCHAR-backed).
        sa.Column("estado", sa.String(length=20), nullable=False),
        # Timestamps de transición.
        sa.Column(
            "announced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        # FK-actor nullable por transición hacia usuarios (staff).
        sa.Column("announced_by_usuario_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("received_by_usuario_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("delivered_by_usuario_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cancelled_by_usuario_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Llaves de negocio únicas.
        sa.UniqueConstraint("tracking_number", name="uq_paquetes_tracking_number"),
        sa.UniqueConstraint("access_code", name="uq_paquetes_access_code"),
        # Anunciante: FK viva a la Persona.
        sa.ForeignKeyConstraint(
            ["announced_by_persona_id"], ["personas.id"], name="fk_paquetes_anunciante"
        ),
        # FK-actor por transición hacia usuarios.
        sa.ForeignKeyConstraint(
            ["announced_by_usuario_id"], ["usuarios.id"],
            name="fk_paquetes_announced_by_usuario",
        ),
        sa.ForeignKeyConstraint(
            ["received_by_usuario_id"], ["usuarios.id"],
            name="fk_paquetes_received_by_usuario",
        ),
        sa.ForeignKeyConstraint(
            ["delivered_by_usuario_id"], ["usuarios.id"],
            name="fk_paquetes_delivered_by_usuario",
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_usuario_id"], ["usuarios.id"],
            name="fk_paquetes_cancelled_by_usuario",
        ),
    )


def downgrade() -> None:
    op.drop_table("paquetes")
    op.drop_table("usuarios")
