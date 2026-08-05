# -*- coding: utf-8 -*-
"""
Seam A — OTP de cliente (pedir / verificar), contra el Postgres efímero.

Comportamiento observable: `preparar_otp` (corrección en vivo 2026-08-02,
reemplaza el antiguo `request_otp` síncrono) SOLO genera un registro si el
teléfono es elegible (existe con al menos un Paquete Recibido) -- resuelve
el código y lo persiste, HASHEADO, sin enviarlo (el envío es responsabilidad
de quien llama, vía BackgroundTask). Verificar correcto crea/reutiliza la
Persona y consume el OTP; incorrecto, expirado, agotado o reutilizado se
rechazan con el mismo mensaje genérico.
"""

from datetime import datetime, timedelta, timezone

import bcrypt
import pytest

from app.domain.otp_cliente import OtpCliente
from app.domain.otp_service import elegible_para_otp, preparar_otp, verify_otp
from app.domain.paquete_lifecycle import receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona import Persona
from app.domain.usuario import RolUsuario, Usuario

pytestmark = pytest.mark.integration

CANON = "+573001234567"


def _usuario(session, nombre="Operador", rol=RolUsuario.OPERADOR) -> Usuario:
    u = Usuario(nombre=nombre, rol=rol)
    session.add(u)
    session.flush()
    return u


def _hacer_elegible(session, tel="3001234567", nombre="Ana"):
    op = _usuario(session)
    p = announce(
        session,
        anunciante_telefono=tel,
        anunciante_nombre=nombre,
        destinatario=Destinatario.yo_mismo(),
    )
    receive(session, p, op)
    session.flush()
    return p


def _pedir(session, tel="3001234567"):
    resultado = preparar_otp(session, tel)
    assert resultado is not None, "el teléfono debía ser elegible para este test"
    _, codigo = resultado
    return codigo


def test_elegible_requiere_paquete_recibido(db_session):
    assert elegible_para_otp(db_session, CANON) is False

    _hacer_elegible(db_session)
    assert elegible_para_otp(db_session, CANON) is True


def test_ocupante_activo_es_elegible_sin_ningun_paquete_propio(db_session):
    # Ticket 05/11 (.scratch/mis-datos): un segundo contacto recién agregado
    # por el principal debe poder pedir su OTP aunque nunca le hayan recibido
    # un paquete a su propio nombre -- ni él ni el principal tienen ningún
    # Paquete en este test, y aun así ambos son elegibles.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    assert elegible_para_otp(db_session, "+573001234567") is True  # el principal
    assert elegible_para_otp(db_session, "+573021112233") is True  # el segundo contacto


def test_ocupante_dado_de_baja_ya_no_es_elegible_por_esta_via(db_session):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, dar_de_baja_ocupante

    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    dar_de_baja_ocupante(db_session, hija)
    assert elegible_para_otp(db_session, "+573021112233") is False


def test_preparar_otp_no_elegible_devuelve_none_sin_crear_registro(db_session):
    resultado = preparar_otp(db_session, "3009998877")
    assert resultado is None
    assert (
        db_session.query(OtpCliente)
        .filter(OtpCliente.telefono == "+573009998877")
        .count()
        == 0
    )


def test_preparar_otp_elegible_genera_registro_con_codigo_hasheado(db_session):
    _hacer_elegible(db_session)
    codigo = _pedir(db_session)

    otp = db_session.query(OtpCliente).filter(OtpCliente.telefono == CANON).one()
    assert otp.codigo_hash != codigo
    # `codigo not in otp.codigo_hash` era la aserción original -- flaky: un
    # hash bcrypt (`$2b$12$...`) SIEMPRE contiene "12" (factor de costo), y su
    # salt/digest es texto pseudoaleatorio donde cualquier substring de 2
    # dígitos puede aparecer por azar. La verificación real es bcrypt propio.
    assert otp.codigo_hash.startswith("$2b$")
    assert bcrypt.checkpw(codigo.encode("utf-8"), otp.codigo_hash.encode("utf-8"))
    assert len(codigo) == 2 and codigo.isdigit()


def test_verificar_codigo_correcto_crea_persona_y_consume_el_otp(db_session):
    _hacer_elegible(db_session)
    codigo = _pedir(db_session)

    persona = verify_otp(db_session, "3001234567", codigo)
    assert persona.telefono == CANON

    otp = db_session.query(OtpCliente).filter(OtpCliente.telefono == CANON).one()
    assert otp.verificado_en is not None


def test_verificar_reutiliza_persona_existente(db_session):
    _hacer_elegible(db_session)
    codigo1 = _pedir(db_session)
    p1 = verify_otp(db_session, "3001234567", codigo1)

    codigo2 = _pedir(db_session)
    p2 = verify_otp(db_session, "3001234567", codigo2)

    assert p1.id == p2.id
    assert db_session.query(Persona).count() == 1


def test_codigo_incorrecto_lanza_valueerror_generico(db_session):
    _hacer_elegible(db_session)
    codigo = _pedir(db_session)
    # Con solo 100 códigos posibles, un valor fijo podría coincidir por azar con
    # el generado — se calcula uno garantizado distinto.
    codigo_incorrecto = "00" if codigo != "00" else "01"

    with pytest.raises(ValueError):
        verify_otp(db_session, "3001234567", codigo_incorrecto)


def test_otp_expirado_se_rechaza(db_session):
    _hacer_elegible(db_session)
    codigo = _pedir(db_session)

    otp = db_session.query(OtpCliente).filter(OtpCliente.telefono == CANON).one()
    otp.expira_en = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.flush()

    with pytest.raises(ValueError):
        verify_otp(db_session, "3001234567", codigo)


def test_intentos_agotados_rechaza_aunque_el_codigo_sea_correcto(db_session):
    _hacer_elegible(db_session)
    codigo = _pedir(db_session)

    otp = db_session.query(OtpCliente).filter(OtpCliente.telefono == CANON).one()
    otp.intentos = otp.max_intentos
    db_session.flush()

    with pytest.raises(ValueError):
        verify_otp(db_session, "3001234567", codigo)


def test_codigo_no_es_reutilizable(db_session):
    _hacer_elegible(db_session)
    codigo = _pedir(db_session)
    verify_otp(db_session, "3001234567", codigo)

    with pytest.raises(ValueError):
        verify_otp(db_session, "3001234567", codigo)
