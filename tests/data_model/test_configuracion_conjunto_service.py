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


def _anunciar_con_apartamento(db_session, torre="Torre 1", numero="101"):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.paquete_service import Destinatario, announce

    apto = resolver_apartamento(db_session, torre, numero)
    return announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
        apartamento=apto,
    )


def test_renombrar_propaga_a_los_paquetes_existentes(db_session):
    """Issue 378 (`.scratch/pendientes-cliente`): antes solo se renombraba `Apartamento.conjunto`, y los paquetes
    anteriores al renombre quedaban con el nombre viejo -- ninguna búsqueda por su terna encontraba la unidad
    ("Este paquete no tiene apartamento resuelto en su snapshot.")."""
    from app.domain.apartamento_service import buscar_apartamento_por_terna

    admin = create_initial_admin(db_session, "admin@club.com", "Admin", _PW)
    p = _anunciar_con_apartamento(db_session)
    assert p.snapshot_conjunto == "EL CLUB"

    renombrar_conjunto(db_session, "El Club Apartamentos", admin)

    db_session.refresh(p)
    assert p.snapshot_conjunto == "EL CLUB APARTAMENTOS"
    assert buscar_apartamento_por_terna(
        db_session, p.snapshot_conjunto, p.snapshot_torre, p.snapshot_apartamento
    ) is not None


def test_renombrar_no_toca_paquetes_sin_apartamento(db_session):
    from app.domain.paquete_service import Destinatario, announce

    admin = create_initial_admin(db_session, "admin@club.com", "Admin", _PW)
    p = announce(
        db_session, anunciante_telefono="3001234567", anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )

    renombrar_conjunto(db_session, "El Club Apartamentos", admin)

    db_session.refresh(p)
    assert p.snapshot_conjunto is None


def test_migracion_realinea_paquetes_desfasados_con_el_conjunto_unico(db_session):
    """Migración de datos del issue 378: los paquetes que ya quedaron con el nombre viejo (renombre hecho antes de
    este arreglo) pasan al nombre del único Conjunto del catálogo."""
    import importlib.util
    from pathlib import Path

    from app.domain.apartamento import Apartamento

    p = _anunciar_con_apartamento(db_session)
    # Estado desfasado: el catálogo ya tiene el nombre nuevo y el paquete no (lo que dejaba el renombre anterior).
    db_session.query(Apartamento).update({"conjunto": "EL CLUB APARTAMENTOS"}, synchronize_session=False)
    db_session.flush()

    ruta = next(Path(__file__).resolve().parents[2].glob("alembic/versions/0057_*.py"))
    spec = importlib.util.spec_from_file_location("migracion_0057", ruta)
    migracion = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migracion)
    migracion.alinear_snapshot_conjunto(db_session.connection())

    db_session.refresh(p)
    assert p.snapshot_conjunto == "EL CLUB APARTAMENTOS"
