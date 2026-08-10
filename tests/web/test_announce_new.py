# -*- coding: utf-8 -*-
"""
Capa web — `/announce` (rediseño `.scratch/announce-rapido`, ticket 04):
campo único inteligente (Teléfono/WhatsApp) + Anunciar.

Comportamiento observable por HTTP: exige sesión de staff (CUALQUIER rol);
`GET /announce/identificar` clasifica el valor en el servidor (nunca confía
en el cliente) y devuelve el fragmento correcto; `POST /announce` anuncia
usando la Persona resuelta (existente o recién creada) como Anunciante Y
Destinatario (`Destinatario.yo_mismo()`). La rama Torre+Apartamento es del
ticket 05 -- acá solo se prueba que "no calza" no rompe nada.
"""

from app.domain.paquete import Paquete
from app.domain.persona import Persona
from app.domain.persona_service import get_or_create_persona, get_or_create_persona_por_whatsapp
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

_PW = "Contrasena1"


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    staff = create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})
    return staff


def test_sin_sesion_redirige_a_login(client):
    r = client.get("/announce", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_operador_ve_el_campo_unico_y_el_enlace_a_residentes(client):
    _login_operador(client)
    r = client.get("/announce")
    assert r.status_code == 200
    assert 'name="q"' in r.text
    assert 'href="/residentes"' in r.text
    # El formulario viejo de 3 bloques desapareció.
    assert 'name="torre"' not in r.text
    assert 'name="conjunto"' not in r.text


# --------------------------------------------------------------------------- #
# GET /announce/identificar -- clasificación server-side + fragmento
# --------------------------------------------------------------------------- #
def test_identificar_sin_sesion_redirige_a_login(client):
    r = client.get("/announce/identificar", params={"q": "3001234567"}, follow_redirects=False)
    assert r.status_code == 303


def test_identificar_telefono_con_match_muestra_a_la_persona(client):
    _login_operador(client)
    get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    r = client.get("/announce/identificar", params={"q": "3001234567"})
    assert r.status_code == 200
    assert "ANA" in r.text
    assert 'name="telefono"' in r.text
    assert 'name="nombre"' not in r.text  # ya existe, no pide nombre


def test_identificar_telefono_sin_match_pide_nombre(client):
    _login_operador(client)
    r = client.get("/announce/identificar", params={"q": "3001234567"})
    assert r.status_code == 200
    assert 'name="telefono"' in r.text
    assert 'name="nombre"' in r.text


def test_identificar_nombre_del_fragmento_no_lleva_autofocus(client):
    # Bug real encontrado en code-review antes de desplegar: el fragmento se
    # re-renderiza (innerHTML) en CADA tecleo del campo principal -- un
    # Nombre con autofocus le robaría el foco de vuelta en cada actualización
    # mientras el staff sigue escribiendo. Ver `_identificar.html`.
    _login_operador(client)
    r = client.get("/announce/identificar", params={"q": "3001234567"})
    assert r.status_code == 200
    assert "autofocus" not in r.text


def test_identificar_telefono_incompleto_no_dispara_nada(client):
    # Mismo bug: sin este umbral, el primer dígito ("3") ya clasificaba
    # como candidato completo.
    _login_operador(client)
    for prefijo in ("3", "30", "300123"):
        r = client.get("/announce/identificar", params={"q": prefijo})
        assert r.status_code == 200
        assert r.text == "", f"prefijo {prefijo!r} no debería disparar nada todavía"


def test_identificar_whatsapp_de_una_o_dos_letras_no_dispara_nada(client):
    _login_operador(client)
    for prefijo in ("a", "an"):
        r = client.get("/announce/identificar", params={"q": prefijo})
        assert r.status_code == 200
        assert r.text == ""


def test_identificar_whatsapp_con_match_muestra_a_la_persona(client):
    _login_operador(client)
    get_or_create_persona_por_whatsapp(client.db, "ana.whats", "Ana")
    client.db.commit()

    r = client.get("/announce/identificar", params={"q": "ana.whats"})
    assert r.status_code == 200
    assert "ANA" in r.text
    assert 'name="whatsapp_usuario"' in r.text
    assert 'name="nombre"' not in r.text


def test_identificar_whatsapp_sin_match_pide_nombre(client):
    _login_operador(client)
    r = client.get("/announce/identificar", params={"q": "ana.whats"})
    assert r.status_code == 200
    assert 'name="whatsapp_usuario"' in r.text
    assert 'name="nombre"' in r.text


def test_identificar_torre_apto_no_resuelve_nada_todavia(client):
    # Ticket 05 -- acá solo se confirma que no rompe ni inventa un match.
    _login_operador(client)
    r = client.get("/announce/identificar", params={"q": "01106"})
    assert r.status_code == 200
    assert r.text == ""


def test_identificar_valor_sin_candidato_no_devuelve_nada(client):
    _login_operador(client)
    r = client.get("/announce/identificar", params={"q": "500 no es nada"})
    assert r.status_code == 200
    assert r.text == ""


def test_identificar_vacio_no_devuelve_nada(client):
    _login_operador(client)
    r = client.get("/announce/identificar", params={"q": ""})
    assert r.status_code == 200
    assert r.text == ""


def test_identificar_reclasifica_en_servidor_sin_confiar_en_el_cliente(client):
    # El "cliente" (este test) manda un valor con forma de Torre+Apto (ticket
    # 05, no resuelve nada) -- el servidor no lo reclasifica como Teléfono ni
    # WhatsApp solo porque alguien lo pida distinto.
    _login_operador(client)
    r = client.get("/announce/identificar", params={"q": "0110699999999999"})
    assert r.status_code == 200
    assert r.text == ""  # empieza en 0 -> torre_apto, NUNCA telefono aunque sea largo


# --------------------------------------------------------------------------- #
# POST /announce -- Anunciar
# --------------------------------------------------------------------------- #
def test_anunciar_por_telefono_de_persona_existente(client):
    staff = _login_operador(client)
    ana = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    r = client.post("/announce", data={"telefono": "3001234567"})
    assert r.status_code == 200
    assert "ANA" in r.text  # toast de confirmación

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "ANA"
    assert p.announced_by_persona_id == ana.id
    assert p.announced_by_phone == "+573001234567"
    assert p.announced_by_usuario_id == staff.id


def test_anunciar_por_telefono_nuevo_crea_persona(client):
    _login_operador(client)
    r = client.post("/announce", data={"telefono": "3001234567", "nombre": "Ana"})
    assert r.status_code == 200

    client.db.expire_all()
    persona = client.db.query(Persona).one()
    assert persona.telefono == "+573001234567"
    assert persona.nombre == "ANA"
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "ANA"


def test_anunciar_por_telefono_nuevo_sin_nombre_falla(client):
    _login_operador(client)
    r = client.post("/announce", data={"telefono": "3001234567"})
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.query(Persona).count() == 0
    assert client.db.query(Paquete).count() == 0


def test_anunciar_por_whatsapp_de_persona_existente(client):
    _login_operador(client)
    ana = get_or_create_persona_por_whatsapp(client.db, "ana.whats", "Ana")
    client.db.commit()

    r = client.post("/announce", data={"whatsapp_usuario": "ana.whats"})
    assert r.status_code == 200

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.announced_by_persona_id == ana.id
    assert p.announced_by_phone is None
    assert p.recipient_phone is None


def test_anunciar_por_whatsapp_nuevo_crea_persona_solo_whatsapp(client):
    _login_operador(client)
    r = client.post("/announce", data={"whatsapp_usuario": "ana.whats", "nombre": "Ana"})
    assert r.status_code == 200

    client.db.expire_all()
    persona = client.db.query(Persona).one()
    assert persona.telefono is None
    assert persona.whatsapp_usuario == "ana.whats"


def test_anunciar_sin_telefono_ni_whatsapp_falla(client):
    _login_operador(client)
    r = client.post("/announce", data={"nombre": "Ana"})
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.query(Paquete).count() == 0


def test_anunciar_con_telefono_y_whatsapp_juntos_falla(client):
    _login_operador(client)
    r = client.post(
        "/announce",
        data={"telefono": "3001234567", "whatsapp_usuario": "ana.whats", "nombre": "Ana"},
    )
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.query(Paquete).count() == 0


def test_anunciar_deja_el_formulario_listo_para_el_siguiente(client):
    _login_operador(client)
    r = client.post("/announce", data={"telefono": "3001234567", "nombre": "Ana"})
    assert r.status_code == 200
    # El campo único vuelve a estar presente y vacío, listo para el próximo.
    assert 'name="q"' in r.text
    assert "autofocus" in r.text
