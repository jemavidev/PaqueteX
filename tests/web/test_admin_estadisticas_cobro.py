# -*- coding: utf-8 -*-
"""
Capa web — `/administracion/estadisticas-cobro` (`.scratch/cobro-bodegaje`,
ticket 06). Solo lectura, exclusiva de admin, agregados por rango de fechas.
"""

from app.domain.cobro import Cobro
from app.domain.paquete_lifecycle import receive as dom_receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario, Usuario

_PW = "Contrasena1"


def _login_admin(client, email="admin@club.com"):
    admin = create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})
    return admin


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def test_sin_sesion_redirige_a_login(client):
    r = client.get("/administracion/estadisticas-cobro", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_operador_es_rechazado_403(client):
    _login_operador(client)
    r = client.get("/administracion/estadisticas-cobro")
    assert r.status_code == 403


def test_admin_ve_el_total_del_dia_de_hoy_por_defecto(client):
    admin = _login_admin(client)
    staff = client.db.query(Usuario).filter(Usuario.email == "admin@club.com").one()
    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    dom_receive(client.db, p, staff)
    client.db.commit()
    client.post(f"/paquetes/{p.id}/entregar")  # crea el Cobro real

    r = client.get("/administracion/estadisticas-cobro")
    assert r.status_code == 200
    cobro = client.db.query(Cobro).filter(Cobro.paquete_id == p.id).one()
    assert str(cobro.monto_total) in r.text or f"{cobro.monto_total:,}" in r.text
