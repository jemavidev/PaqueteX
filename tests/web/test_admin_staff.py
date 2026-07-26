# -*- coding: utf-8 -*-
"""
Capa web — `/administracion/personal` (alta de cuentas de staff, ticket único).

Comportamiento observable por HTTP: gate require_admin (sin sesión redirige,
operador rechazado 403, admin ve el form); un alta válida crea el Usuario; los
rechazos de dominio (duplicado, contraseña débil) no crean nada. NO se re-testea
la regla de negocio de create_staff (ya cubierta en test_staff_service.py).
"""

from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario, Usuario

_PW = "Contrasena1"


def _login_admin(client, email="admin@club.com"):
    create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})
    return email


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def test_sin_sesion_redirige_a_login(client):
    r = client.get("/administracion/personal", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_operador_es_rechazado_403(client):
    _login_operador(client)
    r = client.get("/administracion/personal")
    assert r.status_code == 403


def test_admin_ve_el_formulario(client):
    _login_admin(client)
    r = client.get("/administracion/personal")
    assert r.status_code == 200
    assert 'name="email"' in r.text and 'name="rol"' in r.text


def test_alta_valida_crea_la_cuenta(client):
    _login_admin(client)

    r = client.post(
        "/administracion/personal",
        data={
            "email": "nuevo@club.com",
            "nombre": "Nuevo Operador",
            "password": _PW,
            "rol": "OPERADOR",
        },
    )
    assert r.status_code == 200
    assert "nuevo@club.com" in r.text

    client.db.expire_all()
    creado = (
        client.db.query(Usuario).filter(Usuario.email == "nuevo@club.com").one()
    )
    assert creado.rol == RolUsuario.OPERADOR
    assert creado.password_hash != _PW  # nunca en claro


def test_email_duplicado_no_crea_segunda_cuenta(client):
    _login_admin(client)
    client.post(
        "/administracion/personal",
        data={
            "email": "dup@club.com",
            "nombre": "Uno",
            "password": _PW,
            "rol": "OPERADOR",
        },
    )

    r = client.post(
        "/administracion/personal",
        data={
            "email": "dup@club.com",
            "nombre": "Dos",
            "password": _PW,
            "rol": "OPERADOR",
        },
    )
    assert r.status_code == 400

    client.db.expire_all()
    assert client.db.query(Usuario).filter(Usuario.email == "dup@club.com").count() == 1


def test_password_debil_no_crea_cuenta(client):
    _login_admin(client)
    r = client.post(
        "/administracion/personal",
        data={
            "email": "debil@club.com",
            "nombre": "Debil",
            "password": "corta",
            "rol": "OPERADOR",
        },
    )
    assert r.status_code == 400

    client.db.expire_all()
    assert (
        client.db.query(Usuario).filter(Usuario.email == "debil@club.com").count() == 0
    )


def test_campos_vacios_rechaza_antes_de_llamar_a_dominio(client):
    _login_admin(client)
    r = client.post(
        "/administracion/personal", data={"email": "", "nombre": "", "password": "", "rol": "OPERADOR"}
    )
    assert r.status_code == 400
