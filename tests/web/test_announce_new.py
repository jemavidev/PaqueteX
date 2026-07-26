# -*- coding: utf-8 -*-
"""
Capa web — `/announce-new` (declarar unidad en lote, staff, ticket único).

Comportamiento observable por HTTP: exige sesión de staff (CUALQUIER rol, a
diferencia de `/admin/staff`); declarar une a TODOS los miembros a la vez;
reutiliza Apartamento/Persona existentes sin duplicar; validación todo-o-nada.
NO se re-testean los invariantes de `declare_unit` en sí (ya cubiertos en
`test_declarar_unidad.py`).
"""

from app.domain.apartamento import Apartamento
from app.domain.apartamento_service import declare_unit, get_or_create_apartamento
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


def _payload(conjunto, torre, apartamento, *pares_nombre_telefono):
    """Payload del POST: campos simples + listas repetidas nombre/teléfono.

    httpx codifica un dict de listas como claves repetidas
    (`nombre=A&nombre=B&telefono=1&telefono=2`) — a diferencia de `requests`,
    NO soporta una lista de tuplas para `data=` (la corrompe silenciosamente).
    """
    nombres = [n for n, _ in pares_nombre_telefono]
    telefonos = [t for _, t in pares_nombre_telefono]
    return {
        "conjunto": conjunto,
        "torre": torre,
        "apartamento": apartamento,
        "nombre": nombres,
        "telefono": telefonos,
    }


def test_sin_sesion_redirige_a_login(client):
    r = client.get("/announce", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_operador_ve_el_formulario(client):
    # A diferencia de /admin/staff, CUALQUIER rol de staff entra aquí.
    _login_operador(client)
    r = client.get("/announce")
    assert r.status_code == 200
    assert 'name="conjunto"' in r.text and 'name="nombre"' in r.text


def test_declarar_unidad_une_a_todos_los_miembros_a_la_vez(client):
    _login_operador(client)

    data = _payload(
        "Las Flores", "A", "101",
        ("Ana", "3001234567"), ("Beto", "3019999999"), ("Cira", "3025555555"),
    )

    r = client.post("/announce", data=data)
    assert r.status_code == 200

    client.db.expire_all()
    apto = client.db.query(Apartamento).one()
    personas = client.db.query(Persona).all()
    assert len(personas) == 3
    assert all(p.apartamento_actual_id == apto.id for p in personas)


def test_apartamento_existente_se_reutiliza(client):
    apto_previo = get_or_create_apartamento(client.db, "Las Flores", "A", "101")
    declare_unit(client.db, apto_previo, [("3019999999", "Beto")])
    client.db.commit()

    _login_operador(client)
    data = _payload("Las Flores", "A", "101", ("Ana", "3001234567"))
    client.post("/announce", data=data)

    client.db.expire_all()
    assert client.db.query(Apartamento).count() == 1  # no duplicado


def test_telefono_existente_reutiliza_la_persona(client):
    get_or_create_persona(client.db, "3001234567", "Ana Vieja")
    client.db.commit()

    _login_operador(client)
    data = _payload("Las Flores", "A", "101", ("Ana", "3001234567"))
    client.post("/announce", data=data)

    client.db.expire_all()
    assert (
        client.db.query(Persona).filter(Persona.telefono == "+573001234567").count()
        == 1
    )


def test_fila_con_nombre_sin_telefono_rechaza_todo_sin_persistir(client):
    _login_operador(client)
    data = _payload(
        "Las Flores", "A", "101", ("Ana", "3001234567"), ("SoloNombre", "")
    )

    r = client.post("/announce", data=data)
    assert r.status_code == 400

    client.db.expire_all()
    assert client.db.query(Persona).count() == 0  # nada se persistió


def test_apartamento_incompleto_rechaza(client):
    _login_operador(client)
    data = _payload("Las Flores", "", "101", ("Ana", "3001234567"))

    r = client.post("/announce", data=data)
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.query(Persona).count() == 0


def test_cero_miembros_rechaza(client):
    _login_operador(client)
    # filas en blanco, como llegan del form cuando no se completan.
    data = _payload("Las Flores", "A", "101", ("", ""), ("", ""))

    r = client.post("/announce", data=data)
    assert r.status_code == 400
