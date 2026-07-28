# -*- coding: utf-8 -*-
"""
Capa web — autenticación de staff (login / sesión / current_staff).

Comportamiento observable por HTTP: login válido abre sesión, inválido no (mensaje
genérico), las rutas con privilegios se abren solo con sesión, logout cierra.

`require_admin` (ADMIN vs OPERADOR) se prueba sobre una ruta real en
`test_admin_staff.py` (`/admin/staff`) — el placeholder `/auth/admin/check` que
antes probaba esto aquí fue retirado (rebanada admin-staff).
"""

from app.domain.staff_service import create_initial_admin

_PW = "Contrasena1"


def _seed_admin(client, email="admin@club.com"):
    create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    return email


def test_get_login_renderiza_el_formulario(client):
    r = client.get("/ingresar")
    assert r.status_code == 200
    assert 'name="email"' in r.text and 'name="password"' in r.text


def test_login_valido_abre_sesion_y_me_muestra_al_staff(client):
    email = _seed_admin(client)
    r = client.post("/ingresar", data={"email": email, "password": _PW})
    assert r.status_code == 200  # siguió el redirect a /mi-sesion
    assert email in r.text  # la página de sesión muestra al staff


def test_login_invalido_no_abre_sesion_y_mensaje_generico(client):
    _seed_admin(client)
    r = client.post(
        "/ingresar", data={"email": "admin@club.com", "password": "mala12345"}
    )
    assert r.status_code == 400
    assert "incorrect" in r.text.lower()  # "Email o contraseña incorrectos."
    # Sin sesión: una ruta con privilegios manda al login.
    r2 = client.get("/mi-sesion", follow_redirects=False)
    assert r2.status_code == 303
    assert r2.headers["location"].endswith("/ingresar")


def test_me_sin_sesion_redirige_a_login(client):
    r = client.get("/mi-sesion", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_logout_cierra_la_sesion(client):
    email = _seed_admin(client)
    client.post("/ingresar", data={"email": email, "password": _PW})
    # con sesión, /mi-sesion responde 200
    assert client.get("/mi-sesion").status_code == 200
    client.post("/salir")
    # tras logout, vuelve a redirigir al login
    r = client.get("/mi-sesion", follow_redirects=False)
    assert r.status_code == 303


# --------------------------------------------------------------------------- #
# /salir-todo (Grupo 10, Ronda 2) — el único botón de logout que el header
# muestra ahora, cierra staff Y cliente a la vez si ambas están abiertas.
# --------------------------------------------------------------------------- #
def test_salir_todo_cierra_la_sesion_de_staff(client):
    email = _seed_admin(client)
    client.post("/ingresar", data={"email": email, "password": _PW})
    assert client.get("/mi-sesion").status_code == 200

    r = client.post("/salir-todo", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/anunciar")

    assert client.get("/mi-sesion", follow_redirects=False).status_code == 303


def test_salir_todo_cierra_tambien_la_sesion_de_cliente_coexistente(client):
    from app.domain.otp_sender import DevOtpSender
    from app.web.otp import get_otp_sender

    email = _seed_admin(client)
    client.post("/ingresar", data={"email": email, "password": _PW})

    sender = DevOtpSender()
    client.app.dependency_overrides[get_otp_sender] = lambda: sender
    client.post("/otp/solicitar", data={"telefono": "3001234567"})
    codigo = sender.enviados["+573001234567"]
    client.post("/otp/verificar", data={"telefono": "3001234567", "codigo": codigo})

    assert client.get("/mi-sesion").status_code == 200
    assert client.get("/otp/perfil").status_code == 200

    client.post("/salir-todo")

    assert client.get("/mi-sesion", follow_redirects=False).status_code == 303
    assert client.get("/otp/perfil", follow_redirects=False).status_code == 303
