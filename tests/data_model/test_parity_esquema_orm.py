# -*- coding: utf-8 -*-
"""
Guard de paridad esquema ↔ ORM.

La forma de cada tabla del esquema nuevo se declara en DOS lugares: el modelo ORM
(`app.domain.*`) y su migración. Este test evita que diverjan: tras construir la
BD con `alembic upgrade head`, `compare_metadata` no debe encontrar NINGUNA
diferencia contra `Base.metadata`. Si alguien cambia el modelo sin escribir la
migración (o al revés), este test lo atrapa antes de que aterricen más rebanadas
sobre el mismo árbol.

Cada rebanada nueva importa aquí su modelo para que quede registrado en
`Base.metadata` y el guard cubra su tabla (hoy: `personas`, `apartamentos`,
`usuarios`, `paquetes`, `otps_cliente`, `password_resets`,
`configuracion_conjunto`, `configuracion_empresa`, `ocupantes`,
`paquete_fotos`, `cobros`, `tarifas_cobro`, `motivos_anulacion_cobro`,
`contactos_externos`, `contactos_externos_telefonos`, `motivos_bloqueo`,
`movimientos_saldo_contra_entrega`, `motivos_cancelacion`,
`plantillas_notificacion`, `plantillas_notificacion_historial`,
`persona_preferencia_notificacion`, `proveedores_notificacion_config`,
`proveedores_notificacion_config_historial`,
`proveedores_credenciales_historial`).

Auditoría 2026-09-19: este guard SOLO cubre lo que quede explícitamente
importado acá -- 7 tablas reales (`motivos_cancelacion`,
`plantillas_notificacion(_historial)`, `persona_preferencia_notificacion`,
`proveedores_*`) llevaban sin registrarse desde siempre, así que el guard
pasaba o fallaba dependiendo de si algo MÁS, ajeno a este archivo (ej.
`app.web.app` vía `tests/web/conftest.py`), ya las había importado antes
como efecto secundario -- pasaba en la suite completa, fallaba corriendo
este archivo solo. Con las 7 explícitas, el resultado ya no depende de qué
más se haya importado antes.
"""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from app.domain.base import Base
from app.domain import persona  # noqa: F401  (registra 'personas' en Base.metadata)
from app.domain import apartamento  # noqa: F401  (registra 'apartamentos' en Base.metadata)
from app.domain import usuario  # noqa: F401  (registra 'usuarios' en Base.metadata)
from app.domain import paquete  # noqa: F401  (registra 'paquetes' en Base.metadata)
from app.domain import otp_cliente  # noqa: F401  (registra 'otps_cliente' en Base.metadata)
from app.domain import password_reset  # noqa: F401  (registra 'password_resets' en Base.metadata)
from app.domain import configuracion_conjunto  # noqa: F401  (registra 'configuracion_conjunto' en Base.metadata)
from app.domain import configuracion_empresa  # noqa: F401  (registra 'configuracion_empresa' en Base.metadata)
from app.domain import ocupante  # noqa: F401  (registra 'ocupantes' en Base.metadata)
from app.domain import paquete_foto  # noqa: F401  (registra 'paquete_fotos' en Base.metadata)
from app.domain import cobro  # noqa: F401  (registra 'cobros' en Base.metadata)
from app.domain import tarifa_cobro  # noqa: F401  (registra 'tarifas_cobro' en Base.metadata)
from app.domain import motivo_anulacion_cobro  # noqa: F401  (registra 'motivos_anulacion_cobro' en Base.metadata)
from app.domain import contacto_externo  # noqa: F401  (registra 'contactos_externos'/'contactos_externos_telefonos' en Base.metadata)
from app.domain import motivo_bloqueo  # noqa: F401  (registra 'motivos_bloqueo' en Base.metadata)
from app.domain import saldo_contra_entrega  # noqa: F401  (registra 'movimientos_saldo_contra_entrega' en Base.metadata)
from app.domain import motivo_cancelacion  # noqa: F401  (registra 'motivos_cancelacion' en Base.metadata)
from app.domain import plantilla_notificacion  # noqa: F401  (registra 'plantillas_notificacion' en Base.metadata)
from app.domain import plantilla_notificacion_historial  # noqa: F401  (registra 'plantillas_notificacion_historial' en Base.metadata)
from app.domain import preferencia_notificacion  # noqa: F401  (registra 'persona_preferencia_notificacion' en Base.metadata)
from app.domain import proveedor_config  # noqa: F401  (registra 'proveedores_notificacion_config' en Base.metadata)
from app.domain import proveedor_config_historial  # noqa: F401  (registra 'proveedores_notificacion_config_historial' en Base.metadata)
from app.domain import proveedor_credencial_historial  # noqa: F401  (registra 'proveedores_credenciales_historial' en Base.metadata)

pytestmark = pytest.mark.integration


def test_migracion_y_orm_no_divergen(migrated_db_url):
    engine = create_engine(migrated_db_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            diffs = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert diffs == [], (
        "El esquema migrado y el modelo ORM divergen (drift de autogenerate). "
        f"Diferencias detectadas: {diffs}"
    )
