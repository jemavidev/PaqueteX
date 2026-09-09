# -*- coding: utf-8 -*-
"""
Capa web — flujo de aceptación de términos tras un bloqueo
(`/mis-datos/aceptar-terminos`, `.scratch/bloquear-clientes`, ticket 04).
"""

from app.domain.otp_sender import DevOtpSender
from app.domain.paquete_lifecycle import receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona import Persona
from app.domain.persona_service import autorizar_desbloqueo, bloquear_persona
from app.domain.telefono import normalizar_telefono
from app.domain.usuario import RolUsuario, Usuario
from app.web.otp import get_otp_sender

_CANON = "+573001234567"


def _login_cliente(client, telefono="3001234567"):
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
    return client.db.query(Persona).filter(Persona.telefono == canon).one()


def test_bloqueado_con_desbloqueo_autorizado_es_redirigido_desde_mis_paquetes(client):
    _login_cliente(client)
    persona = client.db.query(Persona).filter(Persona.telefono == _CANON).one()
    bloquear_persona(client.db, persona, "Motivo")
    autorizar_desbloqueo(client.db, persona)
    client.db.commit()

    r = client.get("/mis-paquetes", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/mis-datos/aceptar-terminos"


def test_puede_acceder_a_la_pantalla_de_aceptar_terminos_mientras_bloqueado(client):
    _login_cliente(client)
    persona = client.db.query(Persona).filter(Persona.telefono == _CANON).one()
    bloquear_persona(client.db, persona, "Motivo")
    autorizar_desbloqueo(client.db, persona)
    client.db.commit()

    r = client.get("/mis-datos/aceptar-terminos")
    assert r.status_code == 200


def test_aceptar_sin_marcar_el_checkbox_se_rechaza(client):
    _login_cliente(client)
    persona = client.db.query(Persona).filter(Persona.telefono == _CANON).one()
    bloquear_persona(client.db, persona, "Motivo")
    autorizar_desbloqueo(client.db, persona)
    client.db.commit()

    r = client.post("/mis-datos/aceptar-terminos", data={})
    assert r.status_code == 400

    client.db.expire_all()
    assert client.db.get(Persona, persona.id).bloqueado_en is not None


def test_aceptar_limpia_el_estado_y_permite_volver_al_portal(client):
    _login_cliente(client)
    persona = client.db.query(Persona).filter(Persona.telefono == _CANON).one()
    bloquear_persona(client.db, persona, "Motivo")
    autorizar_desbloqueo(client.db, persona)
    client.db.commit()

    r = client.post(
        "/mis-datos/aceptar-terminos", data={"acepto": "on"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/mis-datos"

    client.db.expire_all()
    persona_actualizada = client.db.get(Persona, persona.id)
    assert persona_actualizada.bloqueado_en is None
    assert persona_actualizada.desbloqueo_autorizado_en is None
    assert persona_actualizada.terminos_aceptados_en is not None

    r2 = client.get("/mis-paquetes")
    assert r2.status_code == 200


def test_bloqueado_con_desbloqueo_autorizado_es_redirigido_desde_post_mis_datos(client):
    # code-review sobre .scratch/bloquear-clientes: `gate_bloqueado` solo
    # estaba cableado en el GET de /mis-datos y en /mis-paquetes -- las
    # rutas POST (editar perfil, ocupantes) no lo llamaban, así que un
    # residente en "desbloqueo autorizado" (OTP habilitado, términos SIN
    # aceptar) podía seguir editando su perfil por POST directo, aunque el
    # spec diga que el portal debe quedar restringido a aceptar términos.
    _login_cliente(client)
    persona = client.db.query(Persona).filter(Persona.telefono == _CANON).one()
    bloquear_persona(client.db, persona, "Motivo")
    autorizar_desbloqueo(client.db, persona)
    client.db.commit()

    r = client.post(
        "/mis-datos", data={"nombre": "Cliente de prueba"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/mis-datos/aceptar-terminos"
