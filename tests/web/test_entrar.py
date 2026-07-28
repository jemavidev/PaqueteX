# -*- coding: utf-8 -*-
"""
Capa web — `/entrar` (login unificado, Grupo 10 de la Ronda 2).

Vista pública, sin sesión. Envuelve visualmente a `/otp` y `/ingresar` sin
reemplazarlos: mismos nombres de campo, mismos `action` de POST -- esta
pantalla es solo el selector Cliente/Staff.
"""


def test_get_entrar_renderiza_ambos_formularios(client):
    r = client.get("/entrar")
    assert r.status_code == 200
    assert 'action="/otp/solicitar"' in r.text
    assert 'action="/ingresar"' in r.text
    assert 'name="telefono"' in r.text
    assert 'name="email"' in r.text and 'name="password"' in r.text


def test_entrar_no_requiere_sesion(client):
    r = client.get("/entrar", follow_redirects=False)
    assert r.status_code == 200


def test_el_formulario_de_cliente_en_entrar_funciona_igual_que_otp(client):
    from app.domain.otp_sender import DevOtpSender
    from app.web.otp import get_otp_sender

    sender = DevOtpSender()
    client.app.dependency_overrides[get_otp_sender] = lambda: sender

    r = client.post("/otp/solicitar", data={"telefono": "3001234567"})
    assert r.status_code == 200
    assert 'name="codigo"' in r.text
