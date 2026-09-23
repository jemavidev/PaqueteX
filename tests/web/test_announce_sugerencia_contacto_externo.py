# -*- coding: utf-8 -*-
"""
Capa web — `/announce`: sugerencia de un Contacto externo cuando el Teléfono
tecleado no existe como Persona (`.scratch/contactos-externos-en-announce`,
ticket 02 -- Teléfono; el usuario de WhatsApp es el ticket 03).

Seam único (spec, "Testing Decisions"): las rutas HTTP de `/announce` -- la
resolución en vivo (`GET /announce/identificar`), la selección de la
sugerencia (`GET /announce/identificar-sugerencia`) y el envío de registro
(`POST /announce`). La consulta a Contactos externos no se prueba por
separado: se cubre a través de esas rutas. Los Contactos externos se
siembran por la misma vía que usa la importación.
"""

import re
from html.parser import HTMLParser

from app.domain.contacto_externo import (
    ContactoExterno,
    ContactoExternoTelefono,
    ContactoExternoWhatsapp,
)
from app.domain.contacto_externo_service import (
    FUENTE_GOOGLE_CONTACTS,
    FUENTE_PRODUCCION_V1,
    FilaFuenteContacto,
    importar_contactos_externos,
)
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.persona import Persona
from app.domain.persona_service import (
    bloquear_persona,
    dar_de_baja_administrativa,
    get_or_create_persona,
)
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

_PW = "Contrasena1"

_MENSAJE = "Este usuario no registra en el sistema, pero podría ser:"


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    staff = create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})
    return staff


def _login_admin(client, email="admin@club.com"):
    create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _sembrar(client, *filas):
    importar_contactos_externos(client.db, list(filas))
    client.db.commit()


def _fila(nombre, telefonos=(), whatsapps=(), fuente=FUENTE_GOOGLE_CONTACTS):
    # Una fila cruda de una fuente externa -- lo que consume la importación.
    return FilaFuenteContacto(nombre=nombre, telefonos=tuple(telefonos), whatsapps=tuple(whatsapps), fuente=fuente)


def _texto_visible(html):
    # El nombre de la tarjetita se pinta partido en dos líneas (primera
    # palabra arriba, el resto abajo, como la lista de residentes de una
    # unidad) -- se compara el texto ya sin marcado y con espacios colapsados,
    # para no acoplar la prueba a ese detalle de presentación.
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _modal_receive_abierto(texto, paquete_id):
    """True si el HTML trae `#modal-receive-<id>` SIN el atributo `hidden`
    (mismo criterio que `test_announce_new.py`)."""
    marcador = f'id="modal-receive-{paquete_id}"'
    if marcador not in texto:
        return False
    inicio = texto.index(marcador)
    return "hidden" not in texto[inicio : texto.index(">", inicio)]


def _estado_de_contactos_externos(client):
    """Todo lo que guarda Contactos externos, para comprobar que usar la
    sugerencia no toca nada (nombre, teléfonos, WhatsApps, fuentes y fechas)."""
    client.db.expire_all()
    return {
        "contactos": [
            (c.id, c.nombre, tuple(c.fuentes), c.created_at, c.updated_at)
            for c in client.db.query(ContactoExterno).order_by(ContactoExterno.nombre)
        ],
        "telefonos": sorted(
            (t.contacto_externo_id, t.telefono) for t in client.db.query(ContactoExternoTelefono)
        ),
        "whatsapps": sorted(
            (w.contacto_externo_id, w.whatsapp_usuario) for w in client.db.query(ContactoExternoWhatsapp)
        ),
    }


class _ExtractorDeInputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = []

    def handle_starttag(self, tag, attrs):
        if tag == "input":
            self.inputs.append(dict(attrs))


def _inputs(html):
    """Los atributos de cada `<input>` del HTML (un atributo booleano, como
    `required`, queda con valor `None`) -- sin depender del orden en que la
    plantilla los escribe."""
    extractor = _ExtractorDeInputs()
    extractor.feed(html)
    return extractor.inputs


def _campos_ocultos(html):
    # `{name: value}` de los `<input type="hidden">` -- lo que el navegador
    # manda de vuelta al enviar el formulario de esa tarjeta.
    return {i["name"]: i.get("value", "") for i in _inputs(html) if i.get("type") == "hidden"}


# --------------------------------------------------------------------------- #
# GET /announce/identificar -- el fragmento con la sugerencia
# --------------------------------------------------------------------------- #
def test_telefono_sin_persona_pero_con_contacto_externo_sugiere_el_nombre(client):
    _login_operador(client)
    _sembrar(client, _fila("juan perez", telefonos=["3001234567"]))

    r = client.get("/announce/identificar", params={"q": "3001234567"})

    assert r.status_code == 200
    assert _MENSAJE in r.text
    assert "JUAN PEREZ" in _texto_visible(r.text)  # ya canonicalizado, como se va a registrar
    # La sugerencia REEMPLAZA al formulario "No encontramos a nadie".
    assert "No encontramos a nadie" not in r.text


def test_coincide_sin_importar_el_formato_en_que_se_teclea_el_telefono(client):
    _login_operador(client)
    _sembrar(client, _fila("juan perez", telefonos=["3001234567"]))

    for tecleado in ("3001234567", "300 123 4567", "300-123-4567", "+57 300 123 4567", "+573001234567"):
        r = client.get("/announce/identificar", params={"q": tecleado})
        assert _MENSAJE in r.text, f"{tecleado!r} debería coincidir"
        assert "JUAN PEREZ" in _texto_visible(r.text)


def test_coincide_con_cualquiera_de_los_telefonos_del_contacto(client):
    _login_operador(client)
    _sembrar(client, _fila("juan perez", telefonos=["3001111111", "3002222222"]))

    for tecleado in ("3001111111", "3002222222"):
        r = client.get("/announce/identificar", params={"q": tecleado})
        assert _MENSAJE in r.text
        assert "JUAN PEREZ" in _texto_visible(r.text)


def test_la_sugerencia_y_la_tarjeta_seleccionada_exponen_solo_el_nombre(client):
    _login_operador(client)
    # Dos fuentes que comparten un teléfono -> un solo contacto con las dos
    # fuentes, otro teléfono y un usuario de WhatsApp.
    _sembrar(
        client,
        _fila(
            "juan perez",
            telefonos=["3001234567", "3007654321"],
            whatsapps=["juan.p"],
            fuente=FUENTE_PRODUCCION_V1,
        ),
        _fila("juan perez", telefonos=["3001234567"], fuente=FUENTE_GOOGLE_CONTACTS),
    )
    client.db.expire_all()
    contacto = client.db.query(ContactoExterno).one()
    assert sorted(contacto.fuentes) == [FUENTE_GOOGLE_CONTACTS, FUENTE_PRODUCCION_V1]  # el sembrado es el esperado

    for ruta in ("/announce/identificar", "/announce/identificar-sugerencia"):
        html = client.get(ruta, params={"q": "3001234567"}).text

        assert "3007654321" not in html, ruta  # el otro teléfono
        assert "juan.p" not in html, ruta  # el usuario de WhatsApp
        assert FUENTE_PRODUCCION_V1 not in html, ruta  # las fuentes
        assert FUENTE_GOOGLE_CONTACTS not in html, ruta
        for fecha in (contacto.created_at, contacto.updated_at):  # las fechas
            assert fecha.strftime("%Y-%m-%d") not in html, ruta
            assert fecha.strftime("%d/%m/%Y") not in html, ruta


# --------------------------------------------------------------------------- #
# Cuándo NO se sugiere: la base de paquetes siempre tiene prioridad
# --------------------------------------------------------------------------- #
def test_no_sugiere_si_existe_una_persona_con_ese_telefono_aunque_el_contacto_exista(client):
    _login_operador(client)
    _sembrar(client, _fila("juan perez", telefonos=["3001234567"]))
    get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    r = client.get("/announce/identificar", params={"q": "3001234567"})

    assert "Ya registrado" in r.text  # la tarjeta de siempre
    assert "ANA" in r.text
    assert _MENSAJE not in r.text
    assert "JUAN" not in r.text


def test_no_sugiere_si_la_persona_esta_de_baja_o_bloqueada(client):
    _login_operador(client)
    _sembrar(
        client,
        _fila("juan perez", telefonos=["3001111111"]),
        _fila("maria lopez", telefonos=["3002222222"]),
    )
    de_baja = get_or_create_persona(client.db, "3001111111", "Ana")
    dar_de_baja_administrativa(client.db, de_baja)
    bloqueada = get_or_create_persona(client.db, "3002222222", "Luisa")
    bloquear_persona(client.db, bloqueada, "Motivo")
    client.db.commit()

    for tecleado, nombre_registrado in (("3001111111", "ANA"), ("3002222222", "LUISA")):
        r = client.get("/announce/identificar", params={"q": tecleado})
        assert "Ya registrado" in r.text  # sigue la tarjeta de siempre, con sus avisos
        assert nombre_registrado in r.text
        assert _MENSAJE not in r.text


def test_sin_coincidencia_en_contactos_externos_el_fragmento_es_identico_al_de_hoy(client):
    _login_operador(client)
    antes = client.get("/announce/identificar", params={"q": "3009998888"}).text
    _sembrar(client, _fila("juan perez", telefonos=["3001234567"]))

    despues = client.get("/announce/identificar", params={"q": "3009998888"}).text

    assert despues == antes
    assert "No encontramos a nadie con ese dato — regístralo:" in despues
    assert _MENSAJE not in despues


def test_torre_apto_valor_incompleto_y_campo_vacio_no_cambian_con_contactos_externos(client):
    _login_operador(client)
    valores = ("01106", "3", "30", "300123", "")
    antes = {q: client.get("/announce/identificar", params={"q": q}).text for q in valores}
    _sembrar(client, _fila("juan perez", telefonos=["3001234567"]))

    for q in valores:
        despues = client.get("/announce/identificar", params={"q": q}).text
        assert despues == antes[q], f"{q!r} no debería cambiar"
        assert _MENSAJE not in despues
    # Los incompletos y el vacío siguen sin disparar nada.
    for q in ("3", "30", "300123", ""):
        assert antes[q] == ""


def test_el_fragmento_trae_nuevo_residente_plegado_con_el_formulario_de_siempre(client):
    _login_operador(client)
    _sembrar(client, _fila("juan perez", telefonos=["3001234567"]))

    html = client.get("/announce/identificar", params={"q": "3001234567"}).text

    desplegable = re.search(r"<details[^>]*data-nueva-persona[^>]*>", html)
    assert desplegable, "falta el desplegable 'Nuevo residente'"
    assert not re.search(r"\bopen\b", desplegable.group(0)), "debe empezar plegado"
    assert "Nuevo residente" in _texto_visible(html)
    assert "Nueva persona" not in _texto_visible(html)  # issue 368: la etiqueta se renombró
    # El formulario de persona nueva de siempre: UNA sola instancia (el campo
    # Nombre tiene un `id` fijo), Nombre obligatorio, Anunciar y Recibir, con
    # el Teléfono tecleado como dato oculto -- y sin el aviso "No encontramos
    # a nadie", que aquí no aplica.
    campos_nombre = [i for i in _inputs(html) if i.get("id") == "nombre"]
    assert len(campos_nombre) == 1
    assert "required" in campos_nombre[0]
    assert html.count('name="accion"') == 2
    assert 'value="anunciar"' in html and 'value="recibir"' in html
    assert _campos_ocultos(html) == {"telefono": "3001234567"}
    assert "No encontramos a nadie" not in html
    assert "autofocus" not in html  # el fragmento se re-renderiza en cada tecleo


def test_el_fragmento_trae_el_contenedor_de_la_tarjeta_seleccionada_debajo_del_mensaje(client):
    # Los mismos ganchos que la lista de residentes de una unidad (JS de
    # `form.html`): el contenedor `#announce-unidad-accion` recibe la tarjeta
    # al elegir, y el clic lleva el valor tecleado. El mensaje queda FUERA del
    # contenedor -- elegir solo reemplaza el contenido del contenedor, así que
    # el mensaje sigue visible.
    _login_operador(client)
    _sembrar(client, _fila("juan perez", telefonos=["3001234567"]))

    html = client.get("/announce/identificar", params={"q": "3001234567"}).text

    assert re.search(r'<div[^>]*id="announce-unidad-accion"[^>]*>\s*</div>', html), "debe empezar vacío"
    assert html.index(_MENSAJE) < html.index('id="announce-unidad-accion"')
    assert 'data-sugerencia-valor="3001234567"' in html


# --------------------------------------------------------------------------- #
# GET /announce/identificar-sugerencia -- elegir la tarjetita
# --------------------------------------------------------------------------- #
def test_elegir_la_sugerencia_muestra_la_tarjeta_de_contacto_externo_con_anunciar_y_recibir(client):
    _login_operador(client)
    _sembrar(client, _fila("juan perez", telefonos=["3001234567"]))

    r = client.get("/announce/identificar-sugerencia", params={"q": "3001234567"})

    assert r.status_code == 200
    assert "Contacto externo" in _texto_visible(r.text)  # el subtítulo
    assert "JUAN PEREZ" in _texto_visible(r.text)
    # Identidad tecleada y nombre a registrar, como datos ocultos.
    assert _campos_ocultos(r.text) == {"telefono": "3001234567", "nombre": "JUAN PEREZ"}
    # Anunciar Y Recibir, ambos presentes: la Persona todavía no existe, no
    # hay bandera de recepción automática que consultar -- comportamiento del
    # formulario de persona nueva, NO el de la tarjeta de un residente
    # registrado (que con la bandera apagada reemplaza Recibir por un pedido
    # de autorización por WhatsApp).
    assert r.text.count('name="accion"') == 2
    assert 'value="anunciar"' in r.text and 'value="recibir"' in r.text
    assert "wa.me" not in r.text
    assert "Pedir autorización" not in r.text
    assert "Auto</span>" not in r.text  # no hay bandera, ni la píldora que la anuncia
    # El mensaje NO se repite acá: ya está visible fuera del contenedor.
    assert _MENSAJE not in r.text


# --------------------------------------------------------------------------- #
# POST /announce -- Anunciar / Recibir desde la tarjeta seleccionada
# --------------------------------------------------------------------------- #
def _elegir_la_sugerencia(client, q="3001234567"):
    """Lo que hace el Staff: teclear, ver la sugerencia y tocarla. Devuelve los
    campos ocultos de la tarjeta que aparece -- exactamente lo que el
    navegador manda al pulsar Anunciar o Recibir."""
    assert _MENSAJE in client.get("/announce/identificar", params={"q": q}).text
    tarjeta = client.get("/announce/identificar-sugerencia", params={"q": q}).text
    return _campos_ocultos(tarjeta)


def test_anunciar_desde_la_tarjeta_registra_a_la_persona_con_el_nombre_en_mayusculas(client):
    _login_operador(client)
    # Nombre tal como viene de una fuente heterogénea: minúscula y con espacios de más.
    _sembrar(client, _fila("juan   perez", telefonos=["3001234567"]))
    campos = _elegir_la_sugerencia(client)

    r = client.post("/announce", data={**campos, "accion": "anunciar"})

    assert r.status_code == 200
    assert "modal-receive-" not in r.text  # Anunciar no abre el modal de recepción
    client.db.expire_all()
    persona = client.db.query(Persona).one()
    assert persona.nombre == "JUAN PEREZ"
    assert persona.telefono == "+573001234567"
    paquete = client.db.query(Paquete).one()
    assert paquete.estado == EstadoPaquete.ANUNCIADO
    # La misma Persona es Anunciante y Destinatario.
    assert paquete.announced_by_persona_id == persona.id
    assert paquete.announced_by_phone == "+573001234567"
    assert paquete.recipient_name == "JUAN PEREZ"
    assert paquete.recipient_phone == "+573001234567"


def test_recibir_desde_la_tarjeta_anuncia_y_abre_el_modal_de_recepcion(client):
    _login_operador(client)
    _sembrar(client, _fila("juan perez", telefonos=["3001234567"]))
    campos = _elegir_la_sugerencia(client)

    r = client.post("/announce", data={**campos, "accion": "recibir"})

    assert r.status_code == 200
    client.db.expire_all()
    persona = client.db.query(Persona).one()
    assert persona.nombre == "JUAN PEREZ"
    paquete = client.db.query(Paquete).one()
    assert paquete.estado == EstadoPaquete.ANUNCIADO  # el modal lo lleva a RECIBIDO después
    assert _modal_receive_abierto(r.text, paquete.id)
    assert f'action="/paquetes/{paquete.id}/recibir"' in r.text


def test_nuevo_residente_registra_con_el_nombre_que_se_escriba_a_mano(client):
    # Si la sugerencia no corresponde (el número cambió de dueño), el Staff
    # abre "Nuevo residente" y registra a alguien con OTRO nombre.
    _login_operador(client)
    _sembrar(client, _fila("juan perez", telefonos=["3001234567"]))
    fragmento = client.get("/announce/identificar", params={"q": "3001234567"}).text

    r = client.post(
        "/announce",
        data={**_campos_ocultos(fragmento), "nombre": "pedro gomez", "accion": "anunciar"},
    )

    assert r.status_code == 200
    client.db.expire_all()
    persona = client.db.query(Persona).one()
    assert persona.nombre == "PEDRO GOMEZ"
    assert persona.telefono == "+573001234567"
    assert client.db.query(Paquete).one().recipient_name == "PEDRO GOMEZ"


def test_usar_la_sugerencia_no_modifica_el_contacto_externo(client):
    _login_operador(client)
    _sembrar(
        client,
        _fila(
            "juan perez",
            telefonos=["3001234567", "3007654321"],
            whatsapps=["juan.p"],
            fuente=FUENTE_PRODUCCION_V1,
        ),
    )
    antes = _estado_de_contactos_externos(client)
    campos = _elegir_la_sugerencia(client)

    client.post("/announce", data={**campos, "accion": "recibir"})

    assert client.db.query(Persona).count() == 1  # el flujo sí corrió
    assert _estado_de_contactos_externos(client) == antes


# --------------------------------------------------------------------------- #
# Quién la ve
# --------------------------------------------------------------------------- #
def test_sin_sesion_ni_la_sugerencia_ni_el_clic_responden_y_redirigen_al_login(client):
    _sembrar(client, _fila("juan perez", telefonos=["3001234567"]))

    for ruta in ("/announce/identificar", "/announce/identificar-sugerencia"):
        r = client.get(ruta, params={"q": "3001234567"}, follow_redirects=False)
        assert r.status_code == 303, ruta
        assert r.headers["location"].endswith("/ingresar"), ruta
        assert "JUAN" not in r.text, ruta  # ni un solo dato del contacto


def test_un_admin_ve_la_misma_sugerencia_que_un_operador(client):
    _login_admin(client)
    _sembrar(client, _fila("juan perez", telefonos=["3001234567"]))

    fragmento = client.get("/announce/identificar", params={"q": "3001234567"})
    tarjeta = client.get("/announce/identificar-sugerencia", params={"q": "3001234567"})

    assert _MENSAJE in fragmento.text
    assert "JUAN PEREZ" in _texto_visible(fragmento.text)
    assert "Contacto externo" in _texto_visible(tarjeta.text)
