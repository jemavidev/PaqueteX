# -*- coding: utf-8 -*-
"""
Capa web — `/announce` completo (Grupo 6 de ajustes-post-referencia-funcional).

Comportamiento observable por HTTP: exige sesión de staff (CUALQUIER rol);
3 bloques (Apartamento, Residentes/Ocupantes, Anunciar), cada uno con sus
propias reglas; Apartamento y Residentes son opcionales EN BLOQUE, Anunciar es
opcional por sí solo. NO se re-testean los invariantes de `agregar_ocupante`
en sí (ya cubiertos en `test_ocupante_service.py`).
"""

from app.domain.apartamento import Apartamento
from app.domain.ocupante import Ocupante
from app.domain.paquete import Paquete
from app.domain.persona import Persona
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

_PW = "Contrasena1"


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _payload(conjunto="", torre="", apartamento="", residentes=None, **anuncio):
    """Payload del POST: campos simples + listas repetidas nombre/teléfono.

    httpx codifica un dict de listas como claves repetidas
    (`nombre=A&nombre=B&telefono=1&telefono=2`) — a diferencia de `requests`,
    NO soporta una lista de tuplas para `data=` (la corrompe silenciosamente).
    """
    residentes = residentes or []
    data = {
        "conjunto": conjunto,
        "torre": torre,
        "apartamento": apartamento,
        "nombre": [n for n, _ in residentes],
        "telefono": [t for _, t in residentes],
    }
    data.update(anuncio)
    return data


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
    assert 'name="anuncio_telefono"' in r.text


# --------------------------------------------------------------------------- #
# Bloque Apartamento + Residentes (Ocupantes)
# --------------------------------------------------------------------------- #
def test_declarar_unidad_con_principal_y_ocupante_sin_telefono(client):
    _login_operador(client)
    data = _payload(
        "Las Flores", "A", "101",
        residentes=[("Papá", "3001234567"), ("Mamá", "")],
    )

    r = client.post("/announce", data=data)
    assert r.status_code == 200

    client.db.expire_all()
    apto = client.db.query(Apartamento).one()
    ocupantes = client.db.query(Ocupante).filter(Ocupante.apartamento_id == apto.id).all()
    assert len(ocupantes) == 2
    papa = next(o for o in ocupantes if o.nombre == "PAPÁ")
    mama = next(o for o in ocupantes if o.nombre == "MAMÁ")
    assert papa.es_principal is True and papa.persona_id is not None
    assert mama.es_principal is False and mama.persona_id is None

    # El residente con teléfono también queda con apartamento_actual sincronizado.
    persona = client.db.query(Persona).filter(Persona.telefono == "+573001234567").one()
    assert persona.apartamento_actual_id == apto.id


def test_primer_residente_de_unidad_nueva_sin_telefono_rechaza(client):
    _login_operador(client)
    data = _payload("Las Flores", "A", "101", residentes=[("SoloNombre", "")])

    r = client.post("/announce", data=data)
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.query(Ocupante).count() == 0


def test_apartamento_existente_se_reutiliza(client):
    _login_operador(client)
    client.post(
        "/announce",
        data=_payload("Las Flores", "A", "101", residentes=[("Beto", "3019999999")]),
    )
    client.post(
        "/announce",
        data=_payload("Las Flores", "A", "101", residentes=[("Ana", "3001234567")]),
    )

    client.db.expire_all()
    assert client.db.query(Apartamento).count() == 1  # no duplicado


def test_reenviar_el_mismo_residente_no_duplica_el_ocupante(client):
    # .scratch/pendientes-cliente/issues/41 -- reenviar el mismo formulario
    # (doble clic, o declarar la misma unidad de nuevo para otro trámite) no
    # debe crear otra fila de Ocupante para quien ya está activo ahí.
    _login_operador(client)
    data = _payload("Las Flores", "A", "101", residentes=[("Beto", "3019999999")])

    client.post("/announce", data=data)
    r = client.post("/announce", data=data)
    r2 = client.post("/announce", data=data)
    assert r.status_code == 200 and r2.status_code == 200

    client.db.expire_all()
    apto = client.db.query(Apartamento).one()
    ocupantes = client.db.query(Ocupante).filter(Ocupante.apartamento_id == apto.id).all()
    assert len(ocupantes) == 1
    assert ocupantes[0].es_principal is True


def test_reenviar_un_residente_sin_telefono_no_duplica_el_ocupante(client):
    _login_operador(client)
    data = _payload(
        "Las Flores", "A", "101",
        residentes=[("Papá", "3001234567"), ("Mamá", "")],
    )
    client.post("/announce", data=data)
    r = client.post("/announce", data=data)
    assert r.status_code == 200

    client.db.expire_all()
    apto = client.db.query(Apartamento).one()
    ocupantes = client.db.query(Ocupante).filter(Ocupante.apartamento_id == apto.id).all()
    assert len(ocupantes) == 2  # Papá + Mamá, ninguno duplicado


def test_apartamento_incompleto_rechaza(client):
    _login_operador(client)
    r = client.post("/announce", data=_payload("Las Flores", "", "101"))
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.query(Persona).count() == 0


def test_residentes_sin_apartamento_rechaza(client):
    _login_operador(client)
    r = client.post("/announce", data=_payload(residentes=[("Ana", "3001234567")]))
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.query(Ocupante).count() == 0


def test_declarar_solo_la_unidad_sin_residentes_rechaza(client):
    _login_operador(client)
    r = client.post("/announce", data=_payload("Las Flores", "A", "101"))
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# Bloque Anunciar (opcional, independiente del Apartamento)
# --------------------------------------------------------------------------- #
def test_anunciar_sin_apartamento(client):
    _login_operador(client)
    data = _payload(anuncio_telefono="3001234567", anuncio_nombre="Ana")

    r = client.post("/announce", data=data)
    assert r.status_code == 200

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "ANA"
    assert p.snapshot_apartamento is None


def test_anunciar_con_apartamento_usa_el_snapshot(client):
    _login_operador(client)
    data = _payload(
        "Las Flores", "A", "101",
        residentes=[("Ana", "3001234567")],
        anuncio_telefono="3001234567", anuncio_nombre="Ana",
    )

    r = client.post("/announce", data=data)
    assert r.status_code == 200

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.snapshot_apartamento == "101"


def test_anunciar_con_telefono_de_notificacion_distinto(client):
    _login_operador(client)
    data = _payload(
        anuncio_telefono="3001234567",
        anuncio_nombre="Ana",
        anuncio_notif_telefono="3029998888",
    )

    r = client.post("/announce", data=data)
    assert r.status_code == 200

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.recipient_phone == "+573029998888"


def test_anuncio_incompleto_rechaza(client):
    _login_operador(client)
    r = client.post("/announce", data=_payload(anuncio_telefono="3001234567"))
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.query(Paquete).count() == 0


def test_formulario_completamente_vacio_no_hace_nada_pero_no_falla(client):
    _login_operador(client)
    r = client.post("/announce", data=_payload())
    assert r.status_code == 200
    client.db.expire_all()
    assert client.db.query(Apartamento).count() == 0
    assert client.db.query(Paquete).count() == 0
