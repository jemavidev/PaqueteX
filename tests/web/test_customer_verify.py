# -*- coding: utf-8 -*-
"""
Capa web — `/mis-datos` (autoedición del cliente, ticket único).

Comportamiento observable por HTTP: exige sesión de cliente; guarda datos
personales de forma PARCIAL; email inválido o Torre/Apartamento incompleto
rechazan TODO el request (rollback); el snapshot de paquetes ya anunciados no
cambia.

Grupo 12 (Ronda 2, `ajustes-post-referencia-funcional/REQUERIMIENTOS.md`): el
cliente ya NO puede fijar ni cambiar el Conjunto — solo el staff lo asigna
(vía `/announce`). Mientras no tenga Conjunto asignado, el cliente no puede
declarar Torre/Apartamento tampoco (no tiene sentido sin saber en cuál
Conjunto). Una vez el staff le asigna uno, el cliente puede actualizar Torre y
Apartamento libremente dentro de ESE Conjunto. `documento`/`tipo_documento`
tampoco se aceptan ya en este formulario (mismo grupo).
"""

from app.domain.apartamento import Apartamento
from app.domain.apartamento_service import declare_unit, get_or_create_apartamento
from app.domain.otp_sender import DevOtpSender
from app.domain.paquete import EstadoPaquete, Paquete
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


def test_con_sesion_sin_apartamento_muestra_el_formulario_sin_campos_de_apartamento(client):
    _login_cliente(client)
    r = client.get("/mis-datos")
    assert r.status_code == 200
    assert 'name="nombre"' in r.text
    assert "todavía no ha sido asignado" in r.text
    assert 'name="torre"' not in r.text  # no tiene sentido sin Conjunto


def test_con_apartamento_asignado_por_staff_el_conjunto_se_ve_pero_no_se_puede_editar(client):
    apto = get_or_create_apartamento(client.db, "Las Flores", "A", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    r = client.get("/mis-datos")
    assert r.status_code == 200
    assert 'id="conjunto"' in r.text and "disabled" in r.text
    assert "LAS FLORES" in r.text
    assert 'name="torre"' in r.text and 'name="apartamento"' in r.text


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
    assert p.segundo_contacto is None  # no enviado, sigue sin tocar

    # Segunda vez: solo segundo_contacto, sin nombre/email -> éstos NO se pierden.
    client.post("/mis-datos", data={"segundo_contacto": "3009998877"})
    client.db.expire_all()
    p2 = client.db.get(Persona, persona.id)
    assert p2.nombre == "Ana" and p2.email == "ana@example.com"
    assert p2.segundo_contacto == "3009998877"


def test_documento_ya_no_se_acepta_en_este_formulario(client):
    persona = _login_cliente(client)
    client.post("/mis-datos", data={"documento": "123", "tipo_documento": "CC"})
    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.documento is None and p.tipo_documento is None


def test_sin_conjunto_asignado_declarar_torre_y_apartamento_se_rechaza(client):
    persona = _login_cliente(client)
    r = client.post(
        "/mis-datos",
        data={"torre": "A", "apartamento": "101"},
    )
    assert r.status_code == 400
    assert "conjunto" in r.text.lower()

    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.apartamento_actual_id is None


def test_enviar_un_conjunto_en_el_formulario_no_tiene_ningun_efecto(client):
    # Aunque alguien arme el POST a mano con "conjunto", el servidor lo ignora
    # por completo -- nunca lee ese campo del cliente.
    persona = _login_cliente(client)
    r = client.post(
        "/mis-datos",
        data={"conjunto": "Cualquiera", "torre": "A", "apartamento": "101"},
    )
    assert r.status_code == 400  # sigue sin Conjunto asignado -> rechazado igual

    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.apartamento_actual_id is None


def test_con_conjunto_ya_asignado_el_cliente_actualiza_torre_y_apartamento(client):
    apto_inicial = get_or_create_apartamento(client.db, "Las Flores", "A", "101")
    declare_unit(client.db, apto_inicial, [("3001234567", "Ana")])
    client.db.commit()

    persona = _login_cliente(client)
    r = client.post(
        "/mis-datos",
        data={"torre": "B", "apartamento": "202"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    apto = client.db.get(Apartamento, p.apartamento_actual_id)
    # El Conjunto se mantuvo (nunca vino del cliente); Torre/Apto cambiaron.
    assert (apto.conjunto, apto.torre, apto.apartamento) == ("LAS FLORES", "B", "202")


def test_actualizar_torre_reutiliza_apartamento_existente_sin_mutar_a_otros(client):
    # Beto ya está en A/101 del mismo conjunto (declarado por staff).
    apto_destino = get_or_create_apartamento(client.db, "Las Flores", "A", "101")
    declare_unit(client.db, apto_destino, [("3019999999", "Beto")])
    # Ana está en el mismo Conjunto pero en otra Torre/Apto.
    apto_ana = get_or_create_apartamento(client.db, "Las Flores", "B", "202")
    declare_unit(client.db, apto_ana, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    r = client.post(
        "/mis-datos",
        data={"torre": "A", "apartamento": "101"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.query(Apartamento).count() == 2  # reutilizado, no duplicado

    ana = client.db.query(Persona).filter(Persona.telefono == "+573001234567").one()
    beto = client.db.query(Persona).filter(Persona.telefono == "+573019999999").one()
    assert ana.apartamento_actual_id == apto_destino.id
    assert beto.apartamento_actual_id == apto_destino.id  # sigue ahí, sin ser tocado


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


def test_torre_o_apartamento_incompleto_rechaza_todo_el_request(client):
    # Ya tiene Conjunto asignado (por staff) -- torre sin apartamento debe
    # rechazar TODO el request, no solo el apartamento.
    apto = get_or_create_apartamento(client.db, "Las Flores", "A", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    persona = _login_cliente(client)
    client.post("/mis-datos", data={"nombre": "Ana"})

    r = client.post(
        "/mis-datos", data={"nombre": "Cambiado", "torre": "B"}
    )
    assert r.status_code == 400

    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.nombre == "Ana"  # tampoco se guardó el nombre de este request
    apto_sin_cambios = client.db.get(Apartamento, p.apartamento_actual_id)
    assert apto_sin_cambios.torre == "A"  # el cambio parcial no se aplicó


def test_cambiar_de_apartamento_no_reescribe_snapshot_de_paquete_ya_anunciado(client):
    apto_inicial = get_or_create_apartamento(client.db, "Las Flores", "A", "101")
    declare_unit(client.db, apto_inicial, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)

    paquete = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()
    paquete_id = paquete.id

    client.post("/mis-datos", data={"torre": "B", "apartamento": "202"})

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
def test_get_muestra_la_matriz_con_sms_activo_por_default(client):
    _login_cliente(client)
    r = client.get("/mis-datos")
    assert r.status_code == 200
    assert 'name="pref_SMS_ANUNCIADO"' in r.text
    # SMS por default viene marcado (checked) para los 4 eventos.
    idx = r.text.index('name="pref_SMS_ANUNCIADO"')
    assert "checked" in r.text[idx : idx + 60]


def test_marcar_un_canal_lo_activa_para_ese_evento(client):
    from app.domain.preferencia_notificacion import CanalNotificacion
    from app.domain.preferencia_notificacion_service import preferencia_activa

    persona = _login_cliente(client)
    client.post("/mis-datos", data={"pref_WHATSAPP_RECIBIDO": "on"})
    client.db.expire_all()

    assert preferencia_activa(
        client.db, persona.id, CanalNotificacion.WHATSAPP, EstadoPaquete.RECIBIDO
    ) is True


def test_no_marcar_sms_lo_desactiva_para_ese_evento(client):
    from app.domain.preferencia_notificacion import CanalNotificacion
    from app.domain.preferencia_notificacion_service import preferencia_activa

    persona = _login_cliente(client)
    # Ningún pref_* en el POST: toda la matriz queda desmarcada (mismo
    # comportamiento que cualquier checkbox HTML ausente).
    client.post("/mis-datos", data={})
    client.db.expire_all()

    assert preferencia_activa(
        client.db, persona.id, CanalNotificacion.SMS, EstadoPaquete.ANUNCIADO
    ) is False


def test_reenviar_la_matriz_marcada_restaura_la_preferencia(client):
    from app.domain.preferencia_notificacion import CanalNotificacion
    from app.domain.preferencia_notificacion_service import preferencia_activa

    persona = _login_cliente(client)
    client.post("/mis-datos", data={})  # desactiva todo
    client.post("/mis-datos", data={"pref_SMS_ANUNCIADO": "on"})  # reactiva solo este

    client.db.expire_all()
    assert preferencia_activa(
        client.db, persona.id, CanalNotificacion.SMS, EstadoPaquete.ANUNCIADO
    ) is True


def test_matriz_vacia_no_rompe_el_resto_del_guardado(client):
    persona = _login_cliente(client)
    client.post("/mis-datos", data={"nombre": "Ana Actualizada"})
    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.nombre == "Ana Actualizada"  # el resto del formulario se guardó igual


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
