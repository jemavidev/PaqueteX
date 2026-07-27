# -*- coding: utf-8 -*-
"""
Seam A — OTP de cliente (pedir / verificar), contra el Postgres efímero.

Comportamiento observable: pedir genera un registro con el código HASHEADO (no en
claro); verificar correcto crea/reutiliza la Persona y consume el OTP; incorrecto,
expirado, agotado o reutilizado se rechazan con el mismo mensaje genérico.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.otp_cliente import OtpCliente
from app.domain.otp_sender import DevOtpSender
from app.domain.otp_service import OtpEnvioFallido, request_otp, verify_otp
from app.domain.persona import Persona

pytestmark = pytest.mark.integration

CANON = "+573001234567"


def _pedir(session, sender, tel="3001234567"):
    request_otp(session, tel, sender)
    return sender.enviados[CANON]


def test_pedir_otp_genera_registro_con_codigo_hasheado(db_session):
    sender = DevOtpSender()
    codigo = _pedir(db_session, sender)

    otp = db_session.query(OtpCliente).filter(OtpCliente.telefono == CANON).one()
    assert otp.codigo_hash != codigo
    assert codigo not in otp.codigo_hash
    assert len(codigo) == 2 and codigo.isdigit()


def test_verificar_codigo_correcto_crea_persona_y_consume_el_otp(db_session):
    sender = DevOtpSender()
    codigo = _pedir(db_session, sender)

    persona = verify_otp(db_session, "3001234567", codigo)
    assert persona.telefono == CANON

    otp = db_session.query(OtpCliente).filter(OtpCliente.telefono == CANON).one()
    assert otp.verificado_en is not None


def test_verificar_reutiliza_persona_existente(db_session):
    sender = DevOtpSender()
    codigo1 = _pedir(db_session, sender)
    p1 = verify_otp(db_session, "3001234567", codigo1)

    codigo2 = _pedir(db_session, sender)
    p2 = verify_otp(db_session, "3001234567", codigo2)

    assert p1.id == p2.id
    assert db_session.query(Persona).count() == 1


def test_codigo_incorrecto_lanza_valueerror_generico(db_session):
    sender = DevOtpSender()
    codigo = _pedir(db_session, sender)
    # Con solo 100 códigos posibles, un valor fijo podría coincidir por azar con
    # el generado — se calcula uno garantizado distinto.
    codigo_incorrecto = "00" if codigo != "00" else "01"

    with pytest.raises(ValueError):
        verify_otp(db_session, "3001234567", codigo_incorrecto)


def test_otp_expirado_se_rechaza(db_session):
    sender = DevOtpSender()
    codigo = _pedir(db_session, sender)

    otp = db_session.query(OtpCliente).filter(OtpCliente.telefono == CANON).one()
    otp.expira_en = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.flush()

    with pytest.raises(ValueError):
        verify_otp(db_session, "3001234567", codigo)


def test_intentos_agotados_rechaza_aunque_el_codigo_sea_correcto(db_session):
    sender = DevOtpSender()
    codigo = _pedir(db_session, sender)

    otp = db_session.query(OtpCliente).filter(OtpCliente.telefono == CANON).one()
    otp.intentos = otp.max_intentos
    db_session.flush()

    with pytest.raises(ValueError):
        verify_otp(db_session, "3001234567", codigo)


def test_codigo_no_es_reutilizable(db_session):
    sender = DevOtpSender()
    codigo = _pedir(db_session, sender)
    verify_otp(db_session, "3001234567", codigo)

    with pytest.raises(ValueError):
        verify_otp(db_session, "3001234567", codigo)


class _SenderQueFalla:
    """Simula un proveedor SMS inalcanzable (p.ej. LIWA sin whitelist de IP)."""

    def enviar(self, telefono, codigo):
        raise ConnectionError("timeout de red simulado")


def test_fallo_de_envio_lanza_otpenviofallido_no_el_error_crudo(db_session):
    with pytest.raises(OtpEnvioFallido):
        request_otp(db_session, "3001234567", _SenderQueFalla())
