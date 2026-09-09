# -*- coding: utf-8 -*-
"""
Capa web — depósito/recuperación de saldo contra entrega + listado
(`.scratch/dinero-contra-entrega`, ticket 02). Cualquier rol de staff.
"""

from app.domain.paquete_service import Destinatario, announce
from app.domain.persona import Persona
from app.domain.persona_service import get_or_create_persona
from app.domain.saldo_contra_entrega import MovimientoSaldoContraEntrega
from app.domain.saldo_contra_entrega_service import saldo_de_persona
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

_PW = "Contrasena1"


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def test_operador_registra_un_deposito(client):
    _login_operador(client)
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    r = client.post(
        f"/residentes/{p.id}/saldo-contra-entrega/movimiento",
        data={"monto": "5000"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    assert saldo_de_persona(client.db, p.id) == 5000


def test_deposito_con_paquete_id_queda_asociado_al_movimiento(client):
    # .scratch/dinero-contra-entrega, spec.md línea 125-126: la ruta
    # standalone "acepta monto (positivo o negativo) y paquete_id opcional"
    # -- encontrado en code-review sin el segundo (el movimiento quedaba
    # siempre con paquete_id=None, aunque el staff supiera a qué paquete
    # correspondía este depósito puntual).
    _login_operador(client)
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    paquete = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    r = client.post(
        f"/residentes/{p.id}/saldo-contra-entrega/movimiento",
        data={"monto": "5000", "paquete_id": str(paquete.id)},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    movimiento = client.db.query(MovimientoSaldoContraEntrega).filter(
        MovimientoSaldoContraEntrega.persona_id == p.id
    ).one()
    assert movimiento.paquete_id == paquete.id


def test_listado_muestra_solo_residentes_con_saldo_no_cero(client):
    _login_operador(client)
    con_saldo = get_or_create_persona(client.db, "3001111111", "Con Saldo")
    sin_saldo = get_or_create_persona(client.db, "3002222222", "Sin Saldo")
    client.db.commit()
    client.post(
        f"/residentes/{con_saldo.id}/saldo-contra-entrega/movimiento", data={"monto": "3000"}
    )

    r = client.get("/residentes/saldos-contra-entrega")
    assert r.status_code == 200
    assert "CON SALDO" in r.text
    assert "SIN SALDO" not in r.text


def test_sin_sesion_redirige_a_login(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    r = client.post(
        f"/residentes/{p.id}/saldo-contra-entrega/movimiento",
        data={"monto": "1000"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")
