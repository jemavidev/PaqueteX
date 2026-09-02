"""proveedores_credenciales_historial -- auditoría de campo cambiado (sin valor)

DESCENDIENTE de `0037_proveedor_config` (`down_revision`). El árbol permanece
de raíz única (ADR-0002). `.scratch/administracion-proveedores/spec.md`,
issue 05: cada credencial real que se aplica vía `app/infra/deploy_ssh.py`
(issue 04) deja un registro acá -- SOLO el nombre del campo (`campo`), quién
y cuándo, NUNCA el valor. Separada de `proveedores_notificacion_config_
historial` (habilitado/orden, issue 01 -- ese sí guarda el valor completo,
nunca es secreto) porque son dos eventos de forma distinta.

Revision ID: 0038_credencial_historial
Revises: 0037_proveedor_config
Create Date: 2026-09-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0038_credencial_historial"
down_revision = "0037_proveedor_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proveedores_credenciales_historial",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("canal", sa.String(length=20), nullable=False),
        sa.Column("proveedor", sa.String(length=30), nullable=False),
        sa.Column("campo", sa.String(length=60), nullable=False),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["usuario_id"],
            ["usuarios.id"],
            name="fk_proveedores_credenciales_historial_usuario",
        ),
    )


def downgrade() -> None:
    op.drop_table("proveedores_credenciales_historial")
