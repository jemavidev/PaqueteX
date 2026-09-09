# -*- coding: utf-8 -*-
"""
Capa web — bloquear/autorizar desbloqueo de un residente (`/residentes/{id}/
bloquear`, `/residentes/{id}/autorizar-desbloqueo`), `.scratch/bloquear-
clientes`, ticket 01. Cualquier rol de staff (no exclusivo de admin, mismo
patrón que baja administrativa).
"""

from app.domain.motivo_bloqueo import MotivoBloqueo
from app.domain.persona import Persona
from app.domain.persona_service import get_or_create_persona
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

_PW = "Contrasena1"


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def test_bloquear_exige_motivo(client):
    _login_operador(client)
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    r = client.post(f"/residentes/{p.id}/bloquear", data={"motivo_bloqueo": ""})
    assert r.status_code == 400

    client.db.expire_all()
    assert client.db.get(Persona, p.id).bloqueado_en is None


def test_cualquier_rol_de_staff_puede_bloquear(client):
    _login_operador(client)
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    r = client.post(
        f"/residentes/{p.id}/bloquear",
        data={"motivo_bloqueo": "Incumplimiento de términos"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    persona = client.db.get(Persona, p.id)
    assert persona.bloqueado_en is not None
    assert persona.motivo_bloqueo == "Incumplimiento de términos"


def test_autorizar_desbloqueo_habilita_el_estado(client):
    _login_operador(client)
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    client.post(f"/residentes/{p.id}/bloquear", data={"motivo_bloqueo": "Motivo"})

    r = client.post(f"/residentes/{p.id}/autorizar-desbloqueo", follow_redirects=False)
    assert r.status_code == 303

    client.db.expire_all()
    persona = client.db.get(Persona, p.id)
    assert persona.desbloqueo_autorizado_en is not None
    assert persona.bloqueado_en is not None


def test_autorizar_desbloqueo_sin_estar_bloqueada_se_rechaza(client):
    _login_operador(client)
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    r = client.post(f"/residentes/{p.id}/autorizar-desbloqueo")
    assert r.status_code == 400


def test_bloquear_no_afecta_ningun_paquete_ya_existente(client):
    from app.domain.paquete import Paquete
    from app.domain.paquete_service import Destinatario, announce

    _login_operador(client)
    p_paquete = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()
    persona = get_or_create_persona(client.db, "3001234567", "Ana")

    client.post(
        f"/residentes/{persona.id}/bloquear", data={"motivo_bloqueo": "Motivo"}
    )

    client.db.expire_all()
    assert client.db.get(Paquete, p_paquete.id).estado.value == "ANUNCIADO"


def test_bloquear_id_inexistente_da_404(client):
    _login_operador(client)
    r = client.post(
        "/residentes/00000000-0000-0000-0000-000000000000/bloquear",
        data={"motivo_bloqueo": "Motivo"},
    )
    assert r.status_code == 404


def test_sin_sesion_redirige_a_login(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    r = client.post(f"/residentes/{p.id}/bloquear", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_badge_bloqueado_aparece_en_la_ficha(client):
    _login_operador(client)
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    client.post(f"/residentes/{p.id}/bloquear", data={"motivo_bloqueo": "Motivo"})

    r = client.get(f"/residentes/{p.id}")
    assert "Bloqueado" in r.text


def test_badge_bloqueado_aparece_en_el_listado(client):
    _login_operador(client)
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    client.post(f"/residentes/{p.id}/bloquear", data={"motivo_bloqueo": "Motivo"})

    r = client.get("/residentes", params={"q": "3001234567"})
    assert "Bloqueado" in r.text


def test_sin_bloquear_no_aparece_el_badge(client):
    _login_operador(client)
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    r = client.get(f"/residentes/{p.id}")
    assert "Bloqueado" not in r.text
