# -*- coding: utf-8 -*-
"""
Capa web — recuperación de contraseña de staff (`/staff/olvide-password`,
`/staff/restablecer-password`).

Comportamiento observable por HTTP: la solicitud responde IGUAL exista o no
el email (no revela cuál); el envío real es asíncrono (BackgroundTask) --
`TestClient` lo corre sincrónicamente antes de devolver la respuesta, así
que `sender.enviados` sigue siendo verificable sin cambios. Confirmar con un
token válido cambia la contraseña y redirige a `/ingresar`; inválido/
expirado/reutilizado, o contraseñas que no coinciden, o débiles, no --
mensaje correspondiente, sin sesión abierta.
"""

from app.domain.email_sender import ConsoleEmailSender
from app.domain.password_reset import PasswordReset
from app.domain.staff_service import create_initial_admin
from app.web.password_reset import get_email_sender

_EMAIL = "admin@club.com"
_PW = "Contrasena1"
_PW_NUEVA = "OtraClaveFuerte9"


def _crear_admin(client, email=_EMAIL, password=_PW):
    admin = create_initial_admin(client.db, email, "Admin", password)
    client.db.commit()
    return admin


def _pedir_reset(client, email=_EMAIL):
    sender = ConsoleEmailSender()
    client.app.dependency_overrides[get_email_sender] = lambda: sender
    r = client.post("/staff/olvide-password", data={"email": email})
    assert r.status_code == 200
    return sender


def _token_del_correo(sender, destino=_EMAIL):
    destino_env, asunto, cuerpo = sender.enviados[0]
    assert destino_env == destino
    # El enlace trae el token crudo como query param -- se extrae de la última palabra de la URL.
    linea_enlace = [l for l in cuerpo.splitlines() if "restablecer-password?token=" in l][0]
    return linea_enlace.split("token=")[1]


def test_get_olvide_password_renderiza_el_formulario(client):
    r = client.get("/staff/olvide-password")
    assert r.status_code == 200
    assert 'name="email"' in r.text


def test_solicitar_reset_email_existente_manda_correo_con_enlace(client):
    _crear_admin(client)
    sender = _pedir_reset(client)

    assert len(sender.enviados) == 1
    token = _token_del_correo(sender)
    assert token
    assert db_tiene_un_reset_vigente(client, token)


def db_tiene_un_reset_vigente(client, token):
    return client.db.query(PasswordReset).filter(PasswordReset.usado_en.is_(None)).count() == 1


def test_solicitar_reset_email_inexistente_misma_respuesta_sin_enviar_correo(client):
    sender = ConsoleEmailSender()
    client.app.dependency_overrides[get_email_sender] = lambda: sender

    r = client.post("/staff/olvide-password", data={"email": "nadie@club.com"})

    assert r.status_code == 200
    assert "revisa tu correo" in r.text.lower()
    assert sender.enviados == []
    assert client.db.query(PasswordReset).count() == 0


def test_confirmar_reset_valido_cambia_password_y_redirige_a_ingresar(client):
    _crear_admin(client)
    sender = _pedir_reset(client)
    token = _token_del_correo(sender)

    r = client.post(
        "/staff/restablecer-password",
        data={"token": token, "password": _PW_NUEVA, "password_confirmacion": _PW_NUEVA},
        follow_redirects=False,
    )

    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar?restablecida=1")

    login = client.post("/ingresar", data={"email": _EMAIL, "password": _PW_NUEVA})
    assert login.status_code == 200
    assert client.get("/mi-sesion").status_code == 200


def test_confirmar_reset_token_invalido_mensaje_generico(client):
    r = client.post(
        "/staff/restablecer-password",
        data={"token": "no-existe", "password": _PW_NUEVA, "password_confirmacion": _PW_NUEVA},
    )
    assert r.status_code == 400
    assert "ya no es válido" in r.text.lower()


def test_confirmar_reset_passwords_no_coinciden(client):
    _crear_admin(client)
    sender = _pedir_reset(client)
    token = _token_del_correo(sender)

    r = client.post(
        "/staff/restablecer-password",
        data={"token": token, "password": _PW_NUEVA, "password_confirmacion": "otra-cosa"},
    )
    assert r.status_code == 400
    assert "no coinciden" in r.text.lower()


def test_confirmar_reset_password_debil_mensaje_especifico(client):
    _crear_admin(client)
    sender = _pedir_reset(client)
    token = _token_del_correo(sender)

    r = client.post(
        "/staff/restablecer-password",
        data={"token": token, "password": "corta1", "password_confirmacion": "corta1"},
    )
    assert r.status_code == 400
    assert "al menos" in r.text.lower()


def test_confirmar_reset_token_no_reutilizable(client):
    _crear_admin(client)
    sender = _pedir_reset(client)
    token = _token_del_correo(sender)

    client.post(
        "/staff/restablecer-password",
        data={"token": token, "password": _PW_NUEVA, "password_confirmacion": _PW_NUEVA},
    )
    r = client.post(
        "/staff/restablecer-password",
        data={"token": token, "password": "OtraClaveFuerte2", "password_confirmacion": "OtraClaveFuerte2"},
    )
    assert r.status_code == 400
    assert "ya no es válido" in r.text.lower()


def test_login_muestra_confirmacion_tras_restablecer(client):
    r = client.get("/ingresar?restablecida=1")
    assert r.status_code == 200
    assert "actualizada" in r.text.lower()
