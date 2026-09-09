# -*- coding: utf-8 -*-
"""
Capa web — `/administracion/motivos-bloqueo` (`.scratch/bloquear-clientes`,
ticket 03). Mismo molde que motivos de anulación de cobro / cancelación.
"""

from app.domain.motivo_bloqueo import MotivoBloqueo
from app.domain.motivo_bloqueo_service import listar_motivos_bloqueo
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
    r = client.get("/administracion/motivos-bloqueo", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_operador_es_rechazado_403(client):
    _login_operador(client)
    r = client.get("/administracion/motivos-bloqueo")
    assert r.status_code == 403


def test_admin_crea_un_motivo(client):
    _login_admin(client)
    r = client.post("/administracion/motivos-bloqueo", data={"etiqueta": "Incumplimiento"})
    assert r.status_code == 200
    assert (
        client.db.query(MotivoBloqueo).filter(MotivoBloqueo.etiqueta == "Incumplimiento").one_or_none()
        is not None
    )


def test_crear_con_etiqueta_vacia_se_rechaza(client):
    _login_admin(client)
    r = client.post("/administracion/motivos-bloqueo", data={"etiqueta": ""})
    assert r.status_code == 400


def test_admin_elimina_un_motivo(client):
    _login_admin(client)
    client.post("/administracion/motivos-bloqueo", data={"etiqueta": "Incumplimiento"})
    motivo = (
        client.db.query(MotivoBloqueo).filter(MotivoBloqueo.etiqueta == "Incumplimiento").one()
    )

    r = client.post(f"/administracion/motivos-bloqueo/{motivo.id}/eliminar")
    assert r.status_code == 200

    client.db.expire_all()
    assert "Incumplimiento" not in {m.etiqueta for m in listar_motivos_bloqueo(client.db)}


def test_operador_no_puede_crear(client):
    _login_operador(client)
    r = client.post("/administracion/motivos-bloqueo", data={"etiqueta": "Incumplimiento"})
    assert r.status_code == 403
