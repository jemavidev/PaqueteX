# -*- coding: utf-8 -*-
"""
Seam A — Credenciales de staff (crear / verificar), contra el Postgres efímero.

Comportamiento observable: solo un ADMIN crea staff; el bootstrap siembra el
primer admin; el email es único; la contraseña se guarda hasheada (no en claro) y
debe ser fuerte; `verify_credentials` acepta la correcta y rechaza mala/inexistente.
"""

import pytest

from app.domain.staff_service import (
    create_initial_admin,
    create_staff,
    verify_credentials,
)
from app.domain.usuario import RolUsuario

pytestmark = pytest.mark.integration

_PW = "Contrasena1"  # >=10, con letra y dígito


def _admin(session, email="admin@club.com"):
    return create_initial_admin(session, email, "Admin", _PW)


def test_bootstrap_crea_el_primer_admin(db_session):
    admin = create_initial_admin(db_session, "admin@club.com", "Admin", _PW)
    assert admin.rol == RolUsuario.ADMIN
    assert admin.email == "admin@club.com"


def test_bootstrap_no_crea_un_segundo_admin(db_session):
    create_initial_admin(db_session, "admin@club.com", "Admin", _PW)
    with pytest.raises(RuntimeError):
        create_initial_admin(db_session, "otro@club.com", "Otro", _PW)


def test_un_admin_crea_staff(db_session):
    admin = _admin(db_session)
    op = create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    assert op.rol == RolUsuario.OPERADOR
    assert op.email == "op@club.com"


def test_un_operador_no_puede_crear_staff(db_session):
    admin = _admin(db_session)
    op = create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    with pytest.raises(PermissionError):
        create_staff(db_session, op, "op2@club.com", "Opb", _PW, RolUsuario.OPERADOR)


def test_email_duplicado_rechazado_normalizando(db_session):
    admin = _admin(db_session)
    create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    with pytest.raises(ValueError):
        # mismo email en otro casing/espacios → normaliza igual → duplicado
        create_staff(db_session, admin, "  OP@Club.com ", "Otro", _PW, RolUsuario.OPERADOR)


def test_password_debil_rechazada(db_session):
    admin = _admin(db_session)
    with pytest.raises(ValueError):
        create_staff(db_session, admin, "a@club.com", "A", "corta1", RolUsuario.OPERADOR)
    with pytest.raises(ValueError):
        create_staff(db_session, admin, "b@club.com", "B", "solo-letras", RolUsuario.OPERADOR)


def test_password_se_guarda_hasheada_no_en_claro(db_session):
    admin = _admin(db_session)
    op = create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    assert op.password_hash and op.password_hash != _PW
    assert _PW not in op.password_hash


def test_verify_acepta_la_correcta_y_normaliza_email(db_session):
    admin = _admin(db_session)
    create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    u = verify_credentials(db_session, "  OP@Club.com ", _PW)
    assert u is not None and u.email == "op@club.com"


def test_verify_rechaza_password_mala(db_session):
    admin = _admin(db_session)
    create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    assert verify_credentials(db_session, "op@club.com", "otra-mala1") is None


def test_verify_rechaza_email_inexistente(db_session):
    assert verify_credentials(db_session, "nadie@club.com", _PW) is None
