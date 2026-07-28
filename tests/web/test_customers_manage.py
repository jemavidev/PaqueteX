# -*- coding: utf-8 -*-
"""
Capa web — `/residentes` (buscar + ver/editar cliente, ticket 02).

Comportamiento observable por HTTP: exige sesión de staff (CUALQUIER rol);
buscar por teléfono o nombre encuentra al cliente correcto; editar es parcial y
opera sobre la Persona de OTRO (no la propia sesión); email inválido rechaza
sin persistir; id inexistente -> 404.
"""

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


def test_sin_sesion_redirige_al_login_de_staff_no_al_de_cliente(client):
    # Confirma el gate correcto: /residentes es STAFF, no cliente, pese a
    # empezar con "/customer" como substring de "/customers".
    r = client.get("/residentes", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")
    assert "customer/login" not in r.headers["location"]


def test_buscar_por_telefono_encuentra_al_cliente(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes", params={"q": "3001234567"})
    assert r.status_code == 200
    assert "Ana" in r.text
    assert str(p.id) in r.text


def test_buscar_por_nombre_encuentra_al_cliente(client):
    get_or_create_persona(client.db, "3001234567", "Ana Gómez")
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes", params={"q": "gómez"})
    assert r.status_code == 200
    assert "Ana Gómez" in r.text


# --------------------------------------------------------------------------- #
# Grupo 17 (Ronda 2) — búsqueda extendida.
# --------------------------------------------------------------------------- #
def test_buscar_por_torre_encuentra_a_los_residentes_de_esa_torre(client):
    from app.domain.apartamento_service import declare_unit, get_or_create_apartamento

    apto = get_or_create_apartamento(client.db, "Las Flores", "A", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes", params={"q": "A"})
    assert r.status_code == 200
    assert "Ana" in r.text


def test_buscar_por_apartamento_encuentra_al_residente(client):
    from app.domain.apartamento_service import declare_unit, get_or_create_apartamento

    apto = get_or_create_apartamento(client.db, "Las Flores", "B", "202")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes", params={"q": "202"})
    assert r.status_code == 200
    assert "Ana" in r.text


def test_buscar_por_nombre_de_segundo_contacto(client):
    from app.domain.persona_service import update_datos_personales

    p = get_or_create_persona(client.db, "3001234567", "Ana")
    update_datos_personales(client.db, p, segundo_contacto="Carlos Gómez")
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes", params={"q": "Carlos Gómez"})
    assert r.status_code == 200
    assert "Ana" in r.text


def test_buscar_por_nombre_de_ocupante_sin_telefono_encuentra_al_principal(client):
    from app.domain.apartamento_service import get_or_create_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    apto = get_or_create_apartamento(client.db, "Las Flores", "A", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")  # principal
    agregar_ocupante(client.db, apto, "Hijo Menor")  # sin teléfono
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes", params={"q": "Hijo Menor"})
    assert r.status_code == 200
    assert "Ana" in r.text  # resuelve a la Persona principal de esa unidad


def test_buscar_por_telefono_de_ocupante_no_principal(client):
    from app.domain.apartamento_service import get_or_create_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    apto = get_or_create_apartamento(client.db, "Las Flores", "A", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")  # principal
    agregar_ocupante(client.db, apto, "Hija", "3019999999")  # con teléfono propio

    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes", params={"q": "3019999999"})
    assert r.status_code == 200
    assert "Hija" in r.text  # tiene su propia Persona/ficha (tiene teléfono)


def test_resultados_no_se_duplican_si_varios_criterios_coinciden(client):
    from app.domain.apartamento_service import declare_unit, get_or_create_apartamento

    apto = get_or_create_apartamento(client.db, "Las Flores", "Gómez", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana Gómez")])
    client.db.commit()
    _login_operador(client)

    # "gómez" coincide con el nombre de la Persona Y con el nombre de la
    # Torre -- debe aparecer una sola vez, no duplicada.
    r = client.get("/residentes", params={"q": "gómez"})
    assert r.status_code == 200
    assert r.text.count("Ana Gómez") == 1


def test_operador_ve_y_edita_la_ficha_de_otra_persona(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get(f"/residentes/{p.id}")
    assert r.status_code == 200
    assert "Ana" in r.text


def test_editar_guarda_parcialmente(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    client.post(f"/residentes/{p.id}", data={"email": "ana@x.com"})
    client.db.expire_all()
    p2 = client.db.get(Persona, p.id)
    assert p2.nombre == "Ana"  # no enviado, sigue igual
    assert p2.email == "ana@x.com"


def test_email_invalido_rechaza_sin_persistir(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.post(f"/residentes/{p.id}", data={"email": "no-es-email"})
    assert r.status_code == 400

    client.db.expire_all()
    assert client.db.get(Persona, p.id).email is None


def test_persona_inexistente_da_404(client):
    _login_operador(client)
    import uuid

    r = client.get(f"/residentes/{uuid.uuid4()}")
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Eliminar cliente (ticket 03) — solo ADMIN.
# --------------------------------------------------------------------------- #
def _login_admin(client, email="admin@club.com"):
    create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def test_admin_elimina_anonimiza_al_cliente(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_admin(client)

    r = client.post(f"/residentes/{p.id}/eliminar", follow_redirects=False)
    assert r.status_code == 303

    client.db.expire_all()
    p2 = client.db.get(Persona, p.id)
    assert p2.nombre == "Cliente eliminado"
    assert p2.telefono.startswith("DEL-")
    assert p2.eliminado_en is not None


def test_operador_no_puede_eliminar(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.post(f"/residentes/{p.id}/eliminar")
    assert r.status_code == 403

    client.db.expire_all()
    p2 = client.db.get(Persona, p.id)
    assert p2.nombre == "Ana"  # sin cambios


def test_eliminar_sin_sesion_redirige_a_login_de_staff(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    r = client.post(f"/residentes/{p.id}/eliminar", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_eliminar_id_inexistente_da_404(client):
    _login_admin(client)
    import uuid

    r = client.post(f"/residentes/{uuid.uuid4()}/eliminar")
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Preferencia de notificaciones desde la ficha de staff (ticket 02 de
# notification-preferences).
# --------------------------------------------------------------------------- #
def test_staff_desactiva_la_preferencia_del_cliente(client):
    from app.domain.paquete import EstadoPaquete
    from app.domain.preferencia_notificacion import CanalNotificacion
    from app.domain.preferencia_notificacion_service import preferencia_activa

    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    client.post(f"/residentes/{p.id}", data={})  # checkbox ausente

    client.db.expire_all()
    assert preferencia_activa(
        client.db, p.id, CanalNotificacion.SMS, EstadoPaquete.ANUNCIADO
    ) is False


def test_staff_reactiva_la_preferencia_del_cliente(client):
    from app.domain.paquete import EstadoPaquete
    from app.domain.preferencia_notificacion import CanalNotificacion
    from app.domain.preferencia_notificacion_service import preferencia_activa

    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    client.post(f"/residentes/{p.id}", data={})  # desactiva
    client.post(
        f"/residentes/{p.id}", data={"notificaciones_activas": "on"}
    )  # reactiva

    client.db.expire_all()
    assert preferencia_activa(
        client.db, p.id, CanalNotificacion.SMS, EstadoPaquete.ANUNCIADO
    ) is True


# --------------------------------------------------------------------------- #
# Ocupantes de la unidad (Grupo 7) — de solo lectura en esta ficha.
# --------------------------------------------------------------------------- #
def test_ficha_muestra_los_ocupantes_del_apartamento(client):
    from app.domain.apartamento_service import get_or_create_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    apto = get_or_create_apartamento(client.db, "Las Flores", "A", "101")
    papa = agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")
    agregar_ocupante(client.db, apto, "Mamá")
    client.db.commit()

    persona = client.db.get(Persona, papa.persona_id)
    persona.apartamento_actual_id = apto.id
    client.db.commit()

    _login_operador(client)
    r = client.get(f"/residentes/{persona.id}")
    assert r.status_code == 200
    assert "Papá" in r.text and "Mamá" in r.text
    assert "Principal" in r.text
    assert "+573001234567" in r.text


def test_ficha_sin_apartamento_no_muestra_ocupantes(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    _login_operador(client)
    r = client.get(f"/residentes/{p.id}")
    assert r.status_code == 200
    assert "Ocupantes de la unidad" not in r.text
