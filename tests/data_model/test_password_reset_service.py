# -*- coding: utf-8 -*-
"""
Seam A — recuperación de contraseña de staff (solicitar / confirmar), contra
el Postgres efímero.

Comportamiento observable: `solicitar_reset` SOLO genera un registro si el
email corresponde a una cuenta ACTIVA -- resuelve el token y lo persiste,
HASHEADO (SHA-256, ver docstring del módulo), sin enviarlo (el envío es
responsabilidad de quien llama, vía BackgroundTask). Confirmar correcto
cambia la contraseña y consume el token; inválido, expirado o reutilizado se
rechaza con el mismo mensaje genérico -- contraseña débil se rechaza con un
mensaje específico (no es un riesgo de enumeración).
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.password_reset import PasswordReset
from app.domain.password_reset_service import confirmar_reset, solicitar_reset
from app.domain.staff_service import create_initial_admin, verify_credentials

pytestmark = pytest.mark.integration

_EMAIL = "admin@club.com"
_PW = "Contrasena1"
_PW_NUEVA = "OtraClaveFuerte9"


def _crear_admin(session, email=_EMAIL, password=_PW):
    return create_initial_admin(session, email, "Admin", password)


def _pedir(session, email=_EMAIL):
    resultado = solicitar_reset(session, email)
    assert resultado is not None, "el email debía corresponder a una cuenta activa"
    _, token = resultado
    return token


def test_solicitar_reset_email_inexistente_devuelve_none_sin_crear_registro(db_session):
    resultado = solicitar_reset(db_session, "nadie@club.com")
    assert resultado is None
    assert db_session.query(PasswordReset).count() == 0


def test_solicitar_reset_cuenta_desactivada_devuelve_none(db_session):
    admin = _crear_admin(db_session)
    # Se desactiva directo en el modelo -- las reglas de "un ADMIN no puede
    # desactivarse a sí mismo" son de `staff_service.set_activo_staff`
    # (ya cubiertas en `test_staff_service.py`), ortogonales a lo que se
    # prueba acá (que `solicitar_reset` respeta `activo`).
    admin.activo = False
    db_session.flush()

    resultado = solicitar_reset(db_session, _EMAIL)
    assert resultado is None
    assert db_session.query(PasswordReset).count() == 0


def test_solicitar_reset_cuenta_activa_genera_registro_con_token_hasheado(db_session):
    _crear_admin(db_session)
    token = _pedir(db_session)

    reset = db_session.query(PasswordReset).one()
    assert reset.token_hash != token
    assert token not in reset.token_hash
    assert reset.usado_en is None


def test_confirmar_reset_correcto_cambia_password_y_consume_el_token(db_session):
    _crear_admin(db_session)
    token = _pedir(db_session)

    confirmar_reset(db_session, token, _PW_NUEVA)

    reset = db_session.query(PasswordReset).one()
    assert reset.usado_en is not None
    assert verify_credentials(db_session, _EMAIL, _PW_NUEVA) is not None
    assert verify_credentials(db_session, _EMAIL, _PW) is None


def test_confirmar_reset_token_no_es_reutilizable(db_session):
    _crear_admin(db_session)
    token = _pedir(db_session)
    confirmar_reset(db_session, token, _PW_NUEVA)

    with pytest.raises(ValueError):
        confirmar_reset(db_session, token, "OtraClaveFuerte2")


def test_confirmar_reset_token_expirado_se_rechaza(db_session):
    _crear_admin(db_session)
    token = _pedir(db_session)

    reset = db_session.query(PasswordReset).one()
    reset.expira_en = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.flush()

    with pytest.raises(ValueError):
        confirmar_reset(db_session, token, _PW_NUEVA)


def test_confirmar_reset_token_invalido_lanza_valueerror_generico(db_session):
    _crear_admin(db_session)
    _pedir(db_session)

    with pytest.raises(ValueError):
        confirmar_reset(db_session, "token-que-no-existe", _PW_NUEVA)


def test_confirmar_reset_password_debil_lanza_valueerror_especifico(db_session):
    _crear_admin(db_session)
    token = _pedir(db_session)

    with pytest.raises(ValueError, match="al menos"):
        confirmar_reset(db_session, token, "corta1")

    # El token sigue vigente -- una contraseña débil no lo consume.
    reset = db_session.query(PasswordReset).one()
    assert reset.usado_en is None
