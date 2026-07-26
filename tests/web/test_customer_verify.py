# -*- coding: utf-8 -*-
"""
Capa web — `/mis-datos` (autoedición del cliente, ticket único).

Comportamiento observable por HTTP: exige sesión de cliente; guarda datos
personales de forma PARCIAL; declarar Apartamento crea/reutiliza sin mutar a
otras Personas ya asignadas; email inválido o Apartamento incompleto rechazan
TODO el request (rollback); el snapshot de paquetes ya anunciados no cambia.
"""

from app.domain.apartamento import Apartamento
from app.domain.apartamento_service import declare_unit, get_or_create_apartamento
from app.domain.otp_sender import DevOtpSender
from app.domain.paquete import Paquete
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona import Persona
from app.web.otp import get_otp_sender

_CANON = "+573001234567"


def _login_cliente(client, telefono="3001234567"):
    sender = DevOtpSender()
    client.app.dependency_overrides[get_otp_sender] = lambda: sender
    client.post("/otp/solicitar", data={"telefono": telefono})
    codigo = sender.enviados[_CANON]
    client.post(
        "/otp/verificar", data={"telefono": telefono, "codigo": codigo}
    )
    return client.db.query(Persona).filter(Persona.telefono == _CANON).one()


def test_sin_sesion_redirige_a_login_de_cliente(client):
    r = client.get("/mis-datos", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/otp")


def test_con_sesion_muestra_el_formulario(client):
    _login_cliente(client)
    r = client.get("/mis-datos")
    assert r.status_code == 200
    assert 'name="nombre"' in r.text and 'name="conjunto"' in r.text


def test_guardar_datos_personales_es_parcial(client):
    persona = _login_cliente(client)

    r = client.post(
        "/mis-datos",
        data={"nombre": "Ana", "email": "ana@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.nombre == "Ana" and p.email == "ana@example.com"
    assert p.documento is None  # no enviado, sigue sin tocar

    # Segunda vez: solo documento/tipo, sin nombre/email -> éstos NO se pierden.
    client.post(
        "/mis-datos", data={"documento": "123", "tipo_documento": "CC"}
    )
    client.db.expire_all()
    p2 = client.db.get(Persona, persona.id)
    assert p2.nombre == "Ana" and p2.email == "ana@example.com"
    assert p2.documento == "123" and p2.tipo_documento == "CC"


def test_declarar_apartamento_nuevo_lo_crea_y_asigna(client):
    persona = _login_cliente(client)

    r = client.post(
        "/mis-datos",
        data={"conjunto": "Las Flores", "torre": "A", "apartamento": "101"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.apartamento_actual_id is not None
    apto = client.db.get(Apartamento, p.apartamento_actual_id)
    assert (apto.conjunto, apto.torre, apto.apartamento) == ("LAS FLORES", "A", "101")


def test_declarar_apartamento_existente_lo_reutiliza_sin_mutar_a_otros(client):
    # Beto ya está en el apartamento (declarado por otra vía, p.ej. staff).
    apto = get_or_create_apartamento(client.db, "Las Flores", "A", "101")
    declare_unit(client.db, apto, [("3019999999", "Beto")])
    client.db.commit()

    persona = _login_cliente(client)
    r = client.post(
        "/mis-datos",
        data={"conjunto": "Las Flores", "torre": "A", "apartamento": "101"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.query(Apartamento).count() == 1  # reutilizado, no duplicado

    ana = client.db.get(Persona, persona.id)
    beto = client.db.query(Persona).filter(Persona.telefono == "+573019999999").one()
    assert ana.apartamento_actual_id == apto.id
    assert beto.apartamento_actual_id == apto.id  # sigue ahí, sin ser tocado/movido


def test_email_invalido_rechaza_todo_el_request_sin_persistir_nada(client):
    persona = _login_cliente(client)
    client.post("/mis-datos", data={"nombre": "Ana"})  # estado base conocido

    r = client.post(
        "/mis-datos", data={"nombre": "Otro Nombre", "email": "no-es-un-email"}
    )
    assert r.status_code == 400

    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.nombre == "Ana"  # el cambio de ESTE request no se aplicó (todo o nada)


def test_apartamento_incompleto_rechaza_todo_el_request(client):
    persona = _login_cliente(client)
    client.post("/mis-datos", data={"nombre": "Ana"})

    r = client.post(
        "/mis-datos", data={"nombre": "Cambiado", "conjunto": "Las Flores"}
    )
    assert r.status_code == 400

    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.nombre == "Ana"  # tampoco se guardó el nombre de este request
    assert p.apartamento_actual_id is None


def test_cambiar_de_apartamento_no_reescribe_snapshot_de_paquete_ya_anunciado(client):
    _login_cliente(client)
    client.post(
        "/mis-datos",
        data={"conjunto": "Las Flores", "torre": "A", "apartamento": "101"},
    )

    paquete = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()
    paquete_id = paquete.id

    client.post(
        "/mis-datos",
        data={"conjunto": "Las Flores", "torre": "B", "apartamento": "202"},
    )

    client.db.expire_all()
    p = client.db.get(Paquete, paquete_id)
    assert (p.snapshot_conjunto, p.snapshot_torre, p.snapshot_apartamento) == (
        "LAS FLORES",
        "A",
        "101",
    )


# --------------------------------------------------------------------------- #
# Preferencia de notificaciones (ticket 02 de notification-preferences)
# --------------------------------------------------------------------------- #
def test_checkbox_marcado_activa_notificaciones(client):
    persona = _login_cliente(client)
    client.post("/mis-datos", data={"notificaciones_activas": "on"})
    client.db.expire_all()
    assert client.db.get(Persona, persona.id).notificaciones_activas is True


def test_checkbox_ausente_desactiva_notificaciones(client):
    persona = _login_cliente(client)
    client.post("/mis-datos", data={})  # sin el campo: desmarcado
    client.db.expire_all()
    assert client.db.get(Persona, persona.id).notificaciones_activas is False


def test_reactivar_restaura_la_preferencia(client):
    persona = _login_cliente(client)
    client.post("/mis-datos", data={})  # desactiva
    client.post("/mis-datos", data={"notificaciones_activas": "on"})  # reactiva
    client.db.expire_all()
    assert client.db.get(Persona, persona.id).notificaciones_activas is True


def test_checkbox_desmarcado_no_rompe_el_resto_del_guardado(client):
    persona = _login_cliente(client)
    client.post("/mis-datos", data={"nombre": "Ana Actualizada"})
    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.nombre == "Ana Actualizada"
    assert p.notificaciones_activas is False  # checkbox ausente = False


def test_desactivar_detiene_una_notificacion_posterior(client):
    from app.domain.staff_service import create_initial_admin
    from app.domain.paquete_lifecycle import receive
    from app.web.notifications import get_notification_sender

    _login_cliente(client)  # Ana, +573001234567, activa por defecto

    class _SenderEspia:
        def __init__(self):
            self.enviados = []

        def enviar(self, destino, mensaje):
            self.enviados.append((destino, mensaje))

    espia = _SenderEspia()
    client.app.dependency_overrides[get_notification_sender] = lambda: espia

    # Desactivar SIN cerrar la sesión de cliente (coexiste con la de staff).
    client.post("/mis-datos", data={})

    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    admin = create_initial_admin(client.db, "admin@club.com", "Admin", "Contrasena1")
    client.db.commit()
    client.post("/ingresar", data={"email": "admin@club.com", "password": "Contrasena1"})

    client.post(f"/paquetes/{p.id}/recibir", data={})

    assert espia.enviados == []
