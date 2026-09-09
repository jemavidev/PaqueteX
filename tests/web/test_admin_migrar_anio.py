# -*- coding: utf-8 -*-
"""
Capa web — `/administracion/migrar-anio` (`.scratch/migracion-por-anio`,
ticket 01). Solo admin, GET cuenta elegibles sin ejecutar, POST ejecuta.
"""

from datetime import datetime, timezone

from app.domain.paquete import Paquete
from app.domain.paquete_lifecycle import deliver as dom_deliver
from app.domain.paquete_lifecycle import receive as dom_receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario, Usuario

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


def _entregado_del_anio_anterior(client):
    staff = client.db.query(Usuario).filter(Usuario.email == "admin@club.com").one()
    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    dom_receive(client.db, p, staff)
    dom_deliver(client.db, p, staff)
    anio_anterior = datetime.now(timezone.utc).year - 1
    p.delivered_at = datetime(anio_anterior, 6, 15, tzinfo=timezone.utc)
    client.db.commit()
    return p


def test_sin_sesion_redirige_a_login(client):
    r = client.get("/administracion/migrar-anio", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_operador_es_rechazado_403(client):
    _login_operador(client)
    r = client.get("/administracion/migrar-anio")
    assert r.status_code == 403


def test_get_muestra_el_conteo_sin_ejecutar(client):
    _login_admin(client)
    p = _entregado_del_anio_anterior(client)
    codigo_original = p.access_code

    r = client.get("/administracion/migrar-anio")
    assert r.status_code == 200
    assert "1" in r.text

    client.db.expire_all()
    assert client.db.get(Paquete, p.id).access_code == codigo_original


def test_post_ejecuta_y_confirma_cuantos_se_migraron(client):
    _login_admin(client)
    p = _entregado_del_anio_anterior(client)
    codigo_original = p.access_code

    r = client.post("/administracion/migrar-anio")
    assert r.status_code == 200

    client.db.expire_all()
    migrado = client.db.get(Paquete, p.id)
    assert migrado.access_code != codigo_original
    assert len(migrado.access_code) == 6


def test_post_actualiza_el_conteo_de_elegibles_tras_migrar(client):
    # Encontrado en pruebas manuales en navegador: la respuesta del POST
    # reusaba `resumen.total` (cuántos se ACABAN de migrar) también para
    # "N paquetes elegibles" -- justo debajo del toast de éxito, la misma
    # pantalla decía "Migración completada: 1 paquete(s)" Y "1 paquete
    # elegible", como si el que se acababa de migrar siguiera pendiente.
    _login_admin(client)
    _entregado_del_anio_anterior(client)

    r = client.post("/administracion/migrar-anio")
    assert r.status_code == 200
    assert "0 paquetes elegibles" in r.text
