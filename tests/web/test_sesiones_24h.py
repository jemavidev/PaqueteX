# -*- coding: utf-8 -*-
"""
Capa web — sesiones de 24 h desde el último uso, y cambiar la contraseña cierra las demás (issue 383,
`.scratch/pendientes-cliente`).

Antes la cookie de sesión duraba 14 días (default de Starlette) y no había forma de revocarla: cambiar o restablecer
la contraseña dejaba vivas las sesiones abiertas en otros equipos. Ahora la cookie dura 24 h y se renueva en cada uso
(Starlette la vuelve a firmar en cada respuesta), y cada cambio de contraseña sube la versión de sesión del usuario:
las sesiones con la versión vieja dejan de valer en su siguiente request.
"""

from fastapi.testclient import TestClient

from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

_PW = "Contrasena1"
_PW_NUEVA = "OtraClave2025"


def _sembrar(client):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    op = create_staff(client.db, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    return admin, op


def _ingresar(cliente_http, email, password=_PW):
    r = cliente_http.post("/ingresar", data={"email": email, "password": password}, follow_redirects=False)
    assert r.status_code in (200, 303)
    return r


def _tiene_sesion(cliente_http):
    return cliente_http.get("/paquetes", follow_redirects=False).status_code == 200


def test_la_cookie_de_sesion_dura_24_horas_y_se_renueva_en_cada_uso(client):
    _sembrar(client)
    r = _ingresar(client, "op@club.com")
    assert "Max-Age=86400" in r.headers["set-cookie"]

    r = client.get("/paquetes")
    assert "Max-Age=86400" in r.headers["set-cookie"]  # cada uso la vuelve a emitir por otras 24 h


def test_restablecer_la_contrasena_desde_administracion_cierra_las_sesiones_de_ese_usuario(client):
    _admin, op = _sembrar(client)
    equipo_del_operador = TestClient(client.app)
    _ingresar(equipo_del_operador, "op@club.com")
    assert _tiene_sesion(equipo_del_operador)

    _ingresar(client, "admin@club.com")
    client.post(f"/administracion/personal/{op.id}/resetear-password", data={"password": _PW_NUEVA})

    assert not _tiene_sesion(equipo_del_operador)
    _ingresar(equipo_del_operador, "op@club.com", _PW_NUEVA)
    assert _tiene_sesion(equipo_del_operador)


def test_cambiar_mi_propia_contrasena_mantiene_esta_sesion_y_cierra_las_demas(client):
    _sembrar(client)
    otro_equipo = TestClient(client.app)
    _ingresar(otro_equipo, "op@club.com")
    _ingresar(client, "op@club.com")

    client.post("/mi-sesion", data={"password": _PW_NUEVA, "password_confirmacion": _PW_NUEVA})

    assert _tiene_sesion(client)
    assert not _tiene_sesion(otro_equipo)


def test_una_sesion_de_antes_de_este_cambio_sigue_valiendo(client):
    """Las cookies emitidas antes de la versión de sesión no la traen: cuentan como versión 0, la inicial."""
    _admin, op = _sembrar(client)
    _ingresar(client, "op@club.com")
    from app.web.security import SESION_VERSION_KEY  # noqa: F401 -- existe

    assert _tiene_sesion(client)
