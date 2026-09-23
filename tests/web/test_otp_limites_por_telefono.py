# -*- coding: utf-8 -*-
"""
OTP de clientes: 6 dígitos sin "666", 3 intentos por código y topes por teléfono (issue 384, `.scratch/pendientes-cliente`).

Antes el código era de 2 dígitos (100 combinaciones) y cada solicitud daba 5 intentos nuevos sin tope por teléfono:
en la auditoría del 2026-09-23 un atacante que nunca veía el código entró en 3-27 solicitudes (~3 minutos).
"""

import pytest

from app.domain.otp_cliente import OtpCliente
from app.domain.otp_sender import DevOtpSender
from app.domain.otp_service import _generar_codigo
from app.domain.paquete_lifecycle import receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin
from app.web.otp import get_otp_sender
from app.web.rate_limit import InMemoryRateLimiter

_CANON = "+573001234567"
_MENSAJE_TOPE = "Ya pediste varios códigos"


def _hacer_elegible(client, telefono="3001234567"):
    staff = create_initial_admin(client.db, "admin@club.com", "Admin", "Contrasena1")
    p = announce(client.db, anunciante_telefono=telefono, anunciante_nombre="Ana", destinatario=Destinatario.yo_mismo())
    receive(client.db, p, staff)
    client.db.commit()


def _sender(client):
    sender = DevOtpSender()
    client.app.dependency_overrides[get_otp_sender] = lambda: sender
    return sender


def _pedir(client, telefono="3001234567"):
    # Un minuto "nuevo" en cada pedido: el límite por IP (5/min) es otro, ya probado en test_rate_limit.py.
    client.app.state.rate_limiter._contadores = {
        k: v for k, v in client.app.state.rate_limiter._contadores.items() if not k.startswith("customer_request_otp:")
    }
    return client.post("/otp/solicitar", data={"telefono": telefono})


def test_el_codigo_tiene_6_digitos_y_nunca_contiene_666():
    codigos = [_generar_codigo() for _ in range(20000)]
    assert all(len(c) == 6 and c.isdigit() for c in codigos)
    assert not any("666" in c for c in codigos)


def test_el_sms_lleva_un_codigo_de_6_digitos(client):
    _hacer_elegible(client)
    sender = _sender(client)

    _pedir(client)

    assert len(sender.enviados[_CANON]) == 6


def test_tres_intentos_fallidos_invalidan_el_codigo(client):
    _hacer_elegible(client)
    sender = _sender(client)
    _pedir(client)
    codigo = sender.enviados[_CANON]
    malo = "000000" if codigo != "000000" else "111111"

    for _ in range(3):
        client.post("/otp/verificar", data={"telefono": "3001234567", "codigo": malo})
    r = client.post("/otp/verificar", data={"telefono": "3001234567", "codigo": codigo}, follow_redirects=False)

    assert r.status_code != 303  # agotado: ni el correcto entra
    assert client.db.query(OtpCliente).one().max_intentos == 3


def test_el_cuarto_codigo_en_una_hora_se_niega_con_un_mensaje_amigable(client):
    _hacer_elegible(client)
    sender = _sender(client)
    for _ in range(3):
        assert _pedir(client).status_code == 200

    r = _pedir(client)

    assert r.status_code == 429
    assert _MENSAJE_TOPE in r.text
    assert client.db.query(OtpCliente).count() == 3  # el cuarto no se generó ni se envió
    assert len(sender.enviados) == 1


def test_el_tope_por_telefono_no_revela_si_el_telefono_es_cliente(client):
    """Un teléfono que no es cliente recibe exactamente la misma respuesta al llegar al tope."""
    _sender(client)
    for _ in range(3):
        assert _pedir(client, "3009998888").status_code == 200

    r = _pedir(client, "3009998888")

    assert r.status_code == 429
    assert _MENSAJE_TOPE in r.text


def test_el_tope_diario_es_de_6_codigos(client):
    _hacer_elegible(client)
    _sender(client)
    limitador = client.app.state.rate_limiter
    for _ in range(6):
        assert _pedir(client).status_code == 200
        # "pasa una hora": se olvida solo la ventana horaria de este teléfono
        limitador._contadores.pop(f"otp_telefono_hora:{_CANON}", None)

    r = _pedir(client)

    assert r.status_code == 429
    assert _MENSAJE_TOPE in r.text


def test_la_pantalla_del_codigo_tiene_6_casillas_y_autocompletado_desde_el_sms(client):
    r = client.get("/otp/verificar", params={"telefono": _CANON})

    assert r.text.count("data-otp-digito=") == 6
    assert 'autocomplete="one-time-code"' in r.text
    assert "código de 6 dígitos" in r.text
