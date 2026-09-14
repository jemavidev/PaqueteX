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


def test_ficha_muestra_el_historial_de_movimientos_de_la_persona(client):
    # .scratch/dinero-contra-entrega-control, ticket 01: el modal "Saldo" de
    # la ficha, además de "Saldo actual: $X" y el formulario de "Actualizar"
    # ya existentes, ahora lista el historial completo de movimientos de esa
    # Persona -- monto, quién de staff lo registró, y a qué paquete se
    # aplicó (o "movimiento manual" si no tiene ninguno asociado).
    _login_operador(client)
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    paquete = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    client.post(
        f"/residentes/{p.id}/saldo-contra-entrega/movimiento",
        data={"monto": "5000", "paquete_id": str(paquete.id)},
    )
    client.post(f"/residentes/{p.id}/saldo-contra-entrega/movimiento", data={"monto": "-2000"})

    r = client.get(f"/residentes/{p.id}")
    assert r.status_code == 200
    assert "5,000" in r.text
    assert "-2,000" in r.text or "2,000" in r.text
    assert "OPA" in r.text  # quién de staff lo registró (nombre en mayúsculas, ver _login_operador)
    assert paquete.access_code in r.text
    assert "movimiento manual" in r.text.lower()


def test_ficha_de_persona_sin_movimientos_muestra_mensaje_de_lista_vacia(client):
    _login_operador(client)
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    r = client.get(f"/residentes/{p.id}")
    assert r.status_code == 200
    # No debe reventar ni mostrar una sección rota -- algún mensaje de "sin
    # movimientos todavía" (texto exacto lo decide la implementación).
    assert "sin movimientos" in r.text.lower() or "ningún movimiento" in r.text.lower()


def test_ledger_global_lista_movimientos_de_varios_residentes(client):
    # .scratch/dinero-contra-entrega-control, ticket 02: a diferencia del
    # resumen (`/residentes/saldos-contra-entrega`, solo saldo actual), este
    # ledger lista cada MOVIMIENTO individual, de cualquier residente.
    _login_operador(client)
    ana = get_or_create_persona(client.db, "3001111111", "Ana")
    beto = get_or_create_persona(client.db, "3002222222", "Beto")
    client.db.commit()
    client.post(f"/residentes/{ana.id}/saldo-contra-entrega/movimiento", data={"monto": "3000"})
    client.post(f"/residentes/{beto.id}/saldo-contra-entrega/movimiento", data={"monto": "-1500"})

    r = client.get("/residentes/movimientos-saldo-contra-entrega")
    assert r.status_code == 200
    assert "ANA" in r.text
    assert "BETO" in r.text
    assert "3,000" in r.text
    assert "1,500" in r.text
    assert "OPA" in r.text


def test_ledger_global_filtra_por_residente(client):
    _login_operador(client)
    ana = get_or_create_persona(client.db, "3001111111", "Ana")
    beto = get_or_create_persona(client.db, "3002222222", "Beto")
    client.db.commit()
    client.post(f"/residentes/{ana.id}/saldo-contra-entrega/movimiento", data={"monto": "3000"})
    client.post(f"/residentes/{beto.id}/saldo-contra-entrega/movimiento", data={"monto": "1500"})

    r = client.get("/residentes/movimientos-saldo-contra-entrega", params={"q": "Ana"})
    assert r.status_code == 200
    assert "ANA" in r.text
    assert "BETO" not in r.text


def test_ledger_global_filtra_ingreso_vs_egreso(client):
    _login_operador(client)
    ana = get_or_create_persona(client.db, "3001111111", "Ana")
    client.db.commit()
    client.post(f"/residentes/{ana.id}/saldo-contra-entrega/movimiento", data={"monto": "3000"})
    client.post(f"/residentes/{ana.id}/saldo-contra-entrega/movimiento", data={"monto": "-700"})

    r = client.get("/residentes/movimientos-saldo-contra-entrega", params={"tipo": "ingreso"})
    assert "$3,000" in r.text
    assert "-$700" not in r.text

    r = client.get("/residentes/movimientos-saldo-contra-entrega", params={"tipo": "egreso"})
    assert "-$700" in r.text
    assert "$3,000" not in r.text


def test_resumen_de_saldos_enlaza_al_ledger_global(client):
    _login_operador(client)
    r = client.get("/residentes/saldos-contra-entrega")
    assert r.status_code == 200
    assert 'href="/residentes/movimientos-saldo-contra-entrega"' in r.text


def test_ledger_global_sin_sesion_redirige_a_login(client):
    r = client.get("/residentes/movimientos-saldo-contra-entrega", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


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
