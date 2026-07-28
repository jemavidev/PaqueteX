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


# --------------------------------------------------------------------------- #
# Grupo 19 (Ronda 2) — plantilla Anunciado dividida Cliente/Staff.
# --------------------------------------------------------------------------- #
def test_admin_ve_dos_filas_de_anunciado_cliente_y_staff(client):
    _login_admin(client)
    r = client.get("/administracion/notificaciones")
    assert r.status_code == 200
    assert "ANUNCIADO · Cliente" in r.text
    assert "ANUNCIADO · Staff" in r.text


def test_defaults_de_anunciado_cliente_y_staff_son_distintos(client):
    _login_admin(client)
    r = client.get("/administracion/notificaciones")
    assert "Anunciaste un paquete" in r.text  # default CLIENTE
    assert "Portería anunció un paquete a tu nombre" in r.text  # default STAFF


def test_guardar_anunciado_cliente_no_afecta_anunciado_staff(client):
    _login_admin(client)
    client.post(
        "/administracion/notificaciones",
        data={
            "evento": "ANUNCIADO",
            "motivo": "CLIENTE",
            "texto": "Gracias por anunciar, {recipient_name}.",
        },
    )

    client.db.expire_all()
    from app.domain.notificacion_service import (
        ORIGEN_ANUNCIO_CLIENTE,
        ORIGEN_ANUNCIO_STAFF,
    )

    texto_cliente = obtener_texto_actual(
        client.db, EstadoPaquete.ANUNCIADO, ORIGEN_ANUNCIO_CLIENTE
    )
    texto_staff = obtener_texto_actual(
        client.db, EstadoPaquete.ANUNCIADO, ORIGEN_ANUNCIO_STAFF
    )
    assert "Gracias por anunciar" in texto_cliente
    assert "Gracias por anunciar" not in texto_staff


def test_notificar_anunciado_por_cliente_usa_la_plantilla_de_cliente(client):
    from app.domain.notification_sender import ConsoleNotificationSender
    from app.domain.notificacion_service import notificar_evento
    from app.domain.paquete_service import Destinatario, announce

    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    sender = ConsoleNotificationSender()
    notificar_evento(client.db, p, EstadoPaquete.ANUNCIADO, sender)

    assert len(sender.enviados) == 1
    assert "Anunciaste un paquete" in sender.enviados[0][1]


def test_notificar_anunciado_por_staff_usa_la_plantilla_de_staff(client):
    from app.domain.notification_sender import ConsoleNotificationSender
    from app.domain.notificacion_service import notificar_evento
    from app.domain.paquete_service import Destinatario, announce
    from app.domain.usuario import Usuario

    _login_admin(client)
    admin = client.db.query(Usuario).one()

    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
        staff_actor=admin,
    )
    client.db.commit()

    sender = ConsoleNotificationSender()
    notificar_evento(client.db, p, EstadoPaquete.ANUNCIADO, sender)

    assert len(sender.enviados) == 1
    assert "Portería anunció un paquete a tu nombre" in sender.enviados[0][1]
