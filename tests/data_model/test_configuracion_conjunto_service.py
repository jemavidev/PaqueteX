# -*- coding: utf-8 -*-
"""
Seam A — Configuración global del Conjunto, contra el Postgres efímero
construido con `alembic upgrade head`.

Se prueba comportamiento externo observable (nombre vigente, quién puede
renombrar, propagación a Apartamento), no nombres de columna ni internals de
SQLAlchemy.
"""

import pytest

from app.domain.configuracion_conjunto_service import (
    obtener_nombre_conjunto,
    renombrar_conjunto,
)
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

pytestmark = pytest.mark.integration

_PW = "Contrasena1"


def test_sin_fila_devuelve_el_nombre_por_defecto(db_session):
    # Forma canónica (MAYÚSCULAS), igual que Persona/Apartamento.
    assert obtener_nombre_conjunto(db_session) == "EL CLUB"


def test_admin_renombra_y_la_lectura_posterior_lo_refleja(db_session):
    admin = create_initial_admin(db_session, "admin@club.com", "Admin", _PW)

    resultado = renombrar_conjunto(db_session, "Reserva de Bosques", admin)

    assert resultado == "RESERVA DE BOSQUES"
    assert obtener_nombre_conjunto(db_session) == "RESERVA DE BOSQUES"


def test_operador_no_puede_renombrar(db_session):
    admin = create_initial_admin(db_session, "admin@club.com", "Admin", _PW)
    operador = create_staff(
        db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR
    )

    with pytest.raises(PermissionError):
        renombrar_conjunto(db_session, "Otro Nombre", operador)

    # No se tocó nada -- sigue el default.
    assert obtener_nombre_conjunto(db_session) == "EL CLUB"


def test_renombrar_propaga_a_los_apartamentos_existentes(db_session):
    from app.domain.apartamento import Apartamento
    from app.domain.apartamento_service import resolver_apartamento

    admin = create_initial_admin(db_session, "admin@club.com", "Admin", _PW)
    # Unidad real ya sembrada por la migración de seed (ticket 02) --
    # no hace falta crear ninguna.
    apto = resolver_apartamento(db_session, "Torre 1", "101")

    renombrar_conjunto(db_session, "Reserva de Bosques", admin)

    db_session.refresh(apto)
    assert apto.conjunto == "RESERVA DE BOSQUES"  # normalizado por Apartamento
    assert db_session.query(Apartamento).filter(
        Apartamento.conjunto == "EL CLUB"
    ).count() == 0
