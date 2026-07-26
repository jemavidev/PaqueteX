# -*- coding: utf-8 -*-
"""
Capa web — `/administracion/notificaciones` (Grupo 8, ticket 02).

Comportamiento observable por HTTP: gate require_admin (mismo patrón que
`/administracion/personal`); sin plantilla previa, el campo muestra el texto
por defecto; guardar persiste la plantilla personalizada.
"""

from app.domain.notificacion_service import obtener_texto_actual
from app.domain.paquete import EstadoPaquete
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
    r = client.get("/administracion/notificaciones", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_operador_es_rechazado_403(client):
    _login_operador(client)
    r = client.get("/administracion/notificaciones")
    assert r.status_code == 403


def test_admin_ve_las_plantillas_con_el_texto_por_defecto(client):
    _login_admin(client)
    r = client.get("/administracion/notificaciones")
    assert r.status_code == 200
    assert "ya está en portería" in r.text  # default de RECIBIDO, sin override


def test_guardar_persiste_la_plantilla_personalizada(client):
    _login_admin(client)
    r = client.post(
        "/administracion/notificaciones",
        data={
            "evento": "RECIBIDO",
            "motivo": "",
            "texto": "Hola {recipient_name}, ya llegó tu encomienda.",
        },
    )
    assert r.status_code == 200
    assert "ya llegó tu encomienda" in r.text

    client.db.expire_all()
    texto = obtener_texto_actual(client.db, EstadoPaquete.RECIBIDO)
    assert texto == "Hola {recipient_name}, ya llegó tu encomienda."


def test_guardar_con_motivo_solo_afecta_ese_motivo(client):
    _login_admin(client)
    client.post(
        "/administracion/notificaciones",
        data={
            "evento": "CANCELADO",
            "motivo": "NO_RECLAMADO",
            "texto": "Tu paquete {recipient_name} no fue reclamado a tiempo.",
        },
    )

    client.db.expire_all()
    texto_no_reclamado = obtener_texto_actual(
        client.db, EstadoPaquete.CANCELADO, "NO_RECLAMADO"
    )
    texto_otro = obtener_texto_actual(client.db, EstadoPaquete.CANCELADO, "OTRO")

    assert "no fue reclamado a tiempo" in texto_no_reclamado
    assert "no fue reclamado a tiempo" not in texto_otro


def test_texto_vacio_rechaza(client):
    _login_admin(client)
    r = client.post(
        "/administracion/notificaciones",
        data={"evento": "RECIBIDO", "motivo": "", "texto": "   "},
    )
    assert r.status_code == 400
