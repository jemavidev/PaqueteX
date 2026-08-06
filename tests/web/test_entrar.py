# -*- coding: utf-8 -*-
"""
Capa web — `/entrar` (login unificado, Grupo 10 de la Ronda 2).

Vista pública, sin sesión. Envuelve visualmente a `/otp` y `/ingresar` sin
reemplazarlos: mismos nombres de campo, mismos `action` de POST -- esta
pantalla es solo el selector Cliente/Staff.

Pedido del cliente (versión móvil, `.scratch/pendientes-cliente`): con
sesión YA activa, `/entrar` no debe mostrar el formulario de nuevo --
redirige directo al área por defecto de esa sesión (mismo destino que
`destino_marca` en `base.html`: staff a `/paquetes`, cliente a
`/mis-datos`, staff gana si coexisten ambas).
"""

from app.domain.otp_sender import DevOtpSender
from app.domain.paquete_lifecycle import receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin
from app.web.otp import get_otp_sender

_PW = "Contrasena1"


def _login_cliente(client, telefono="3001234567"):
    staff = create_initial_admin(client.db, "admin-seed@club.com", "AdminSeed", _PW)
    p = announce(
        client.db,
        anunciante_telefono=telefono,
        anunciante_nombre="Cliente de prueba",
        destinatario=Destinatario.yo_mismo(),
    )
    receive(client.db, p, staff)
    client.db.commit()

    sender = DevOtpSender()
    client.app.dependency_overrides[get_otp_sender] = lambda: sender
    client.post("/otp/solicitar", data={"telefono": telefono})
    codigo = sender.enviados["+573001234567"]
    client.post("/otp/verificar", data={"telefono": telefono, "codigo": codigo})


def _login_staff(client, email="admin@club.com"):
    create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


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


def test_entrar_redirige_a_mis_datos_si_ya_hay_sesion_de_cliente(client):
    _login_cliente(client)
    r = client.get("/entrar", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/mis-datos"


def test_entrar_redirige_a_paquetes_si_ya_hay_sesion_de_staff(client):
    _login_staff(client)
    r = client.get("/entrar", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/paquetes"


def test_entrar_prioriza_staff_si_coexisten_ambas_sesiones(client):
    # Mismo criterio de prioridad que `destino_marca` en base.html.
    # `_login_cliente` ya crea un ADMIN internamente (actor de elegibilidad)
    # -- se reutiliza ESE mismo admin para la sesión de staff, en vez de
    # intentar un segundo bootstrap (`create_initial_admin` solo permite uno).
    _login_cliente(client)
    client.post(
        "/ingresar", data={"email": "admin-seed@club.com", "password": _PW}
    )
    r = client.get("/entrar", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/paquetes"
