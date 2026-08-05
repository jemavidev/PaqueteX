# -*- coding: utf-8 -*-
"""
Capa web — `/administracion/conjunto` (`.scratch/apartamento-catalogo-
confirmacion`, ticket 01).

Comportamiento observable por HTTP: gate require_admin (mismo patrón que
`/administracion/personal` y `/administracion/notificaciones`); sin fila
previa, el campo muestra el nombre por defecto; guardar persiste el nombre y
lo refleja en la respuesta.
"""

from app.domain.configuracion_conjunto_service import obtener_nombre_conjunto
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

_PW = "Contrasena1"


def _login_admin(client, email="admin@club.com"):
    create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def test_sin_sesion_redirige_a_login(client):
    r = client.get("/administracion/conjunto", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_operador_es_rechazado_403(client):
    _login_operador(client)
    r = client.get("/administracion/conjunto")
    assert r.status_code == 403


def test_admin_ve_el_nombre_vigente_por_defecto(client):
    _login_admin(client)
    r = client.get("/administracion/conjunto")
    assert r.status_code == 200
    assert "EL CLUB" in r.text


def test_admin_renombra_y_persiste(client):
    _login_admin(client)
    r = client.post("/administracion/conjunto", data={"nombre": "Reserva de Bosques"})
    assert r.status_code == 200
    assert "RESERVA DE BOSQUES" in r.text

    client.db.expire_all()
    assert obtener_nombre_conjunto(client.db) == "RESERVA DE BOSQUES"


def test_operador_no_puede_renombrar_por_post(client):
    _login_operador(client)
    r = client.post("/administracion/conjunto", data={"nombre": "Otro Nombre"})
    assert r.status_code == 403

    client.db.expire_all()
    assert obtener_nombre_conjunto(client.db) == "EL CLUB"


def test_nombre_vacio_no_se_guarda(client):
    _login_admin(client)
    r = client.post("/administracion/conjunto", data={"nombre": "   "})
    assert r.status_code == 400

    client.db.expire_all()
    assert obtener_nombre_conjunto(client.db) == "EL CLUB"
