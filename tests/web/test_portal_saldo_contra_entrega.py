# -*- coding: utf-8 -*-
"""
Capa web — el residente ve su propio saldo/historial contra entrega en
`/mis-datos` (`.scratch/dinero-contra-entrega`, ticket 05).
"""

from app.domain.otp_sender import DevOtpSender
from app.domain.paquete_lifecycle import receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona import Persona
from app.domain.saldo_contra_entrega_service import registrar_movimiento_saldo
from app.domain.telefono import normalizar_telefono
from app.domain.usuario import RolUsuario, Usuario
from app.web.otp import get_otp_sender


def _login_cliente(client, telefono):
    canon = normalizar_telefono(telefono)
    staff = Usuario(nombre="ActorElegibilidad", rol=RolUsuario.OPERADOR)
    client.db.add(staff)
    client.db.flush()
    p = announce(
        client.db,
        anunciante_telefono=telefono,
        anunciante_nombre="Cliente de prueba",
        destinatario=Destinatario.yo_mismo(),
    )
    receive(client.db, p, staff)
    client.db.commit()

    sender = DevOtpSender()
    client.app.dependency_overrides[get_otp_sender] = lambda: sender
    client.post("/otp/solicitar", data={"telefono": telefono})
    codigo = sender.enviados[canon]
    client.post("/otp/verificar", data={"telefono": telefono, "codigo": codigo})
    return client.db.query(Persona).filter(Persona.telefono == canon).one(), staff


def test_residente_ve_su_propio_saldo_e_historial(client):
    persona, staff = _login_cliente(client, "3001234567")
    registrar_movimiento_saldo(client.db, persona.id, 5000, staff)
    client.db.commit()

    r = client.get("/mis-datos")
    assert r.status_code == 200
    assert "Saldo a favor" in r.text
    assert "5,000" in r.text


def test_sin_ningun_movimiento_no_muestra_nada(client):
    _login_cliente(client, "3001234567")

    r = client.get("/mis-datos")
    assert "Saldo a favor" not in r.text


def test_no_ve_el_saldo_de_otro_residente_aunque_comparta_apartamento(client):
    from app.domain.apartamento_service import resolver_apartamento, set_apartamento_actual
    from app.domain.persona_service import get_or_create_persona

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    get_or_create_persona(client.db, "3002222222", "Beto")
    client.db.commit()
    persona_a, staff = _login_cliente(client, "3001111111")
    set_apartamento_actual(client.db, "3001111111", apto)
    set_apartamento_actual(client.db, "3002222222", apto)
    registrar_movimiento_saldo(client.db, persona_a.id, 9000, staff)
    client.db.commit()

    # Logueado como Persona A (con historial) -- ve el suyo.
    r = client.get("/mis-datos")
    assert "9,000" in r.text

    # Ahora se loguea B (sin historial propio, aunque comparte apartamento).
    _login_cliente(client, "3002222222")
    r2 = client.get("/mis-datos")
    assert "Saldo a favor" not in r2.text
    assert "9,000" not in r2.text
