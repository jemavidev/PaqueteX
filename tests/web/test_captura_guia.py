# -*- coding: utf-8 -*-
"""
Capa web — captura de la Guía en Recibir/Entregar
(`.scratch/captura-guia-lector-camara`).

Seam 1 (HTTP sobre el servidor): lo que el servidor decide y el HTML que entrega al
cliente. Lo que solo existe en un navegador real (Enter, foco, cámara) vive en el seam
de navegador (`tests/browser`, marcador `browser`).
"""

import re

from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin
from app.domain.usuario import Usuario

_PW = "Contrasena1"


def _login_staff(client, email="staff@club.com"):
    create_initial_admin(client.db, email, "Staff", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})
    return client.db.query(Usuario).filter(Usuario.email == email).one()


def _anunciar(client, tel="3001234567", nombre="Ana"):
    p = announce(
        client.db,
        anunciante_telefono=tel,
        anunciante_nombre=nombre,
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()
    return p


def _bloques_script(html):
    """Cuerpos de los `<script>` inline sin atributos (los que emite `recursos_recibir()`)."""
    return re.findall(r"<script>(.*?)</script>", html, re.S)


def _bloques_con(html, marca):
    return [b for b in _bloques_script(html) if marca in b]


# --------------------------------------------------------------------------- #
# Ticket 01 (prefactor) — la captura de guía es un bloque propio dentro del
# componente compartido, en cada página que lo usa.
# --------------------------------------------------------------------------- #
def test_la_captura_de_guia_es_un_bloque_propio_y_unico_en_cada_pagina(client):
    _login_staff(client)
    p = _anunciar(client)

    rutas = ["/paquetes", f"/consultar?q={p.access_code}", "/announce", "/residentes"]
    for ruta in rutas:
        r = client.get(ruta)
        assert r.status_code == 200, ruta
        captura = _bloques_con(r.text, "BrowserMultiFormatReader")
        resto = _bloques_con(r.text, "pickerRenderTorres")

        # Exactamente un bloque de captura y uno del resto (picker/modales), y distintos.
        assert len(captura) == 1, ruta
        assert len(resto) == 1, ruta
        assert captura[0] != resto[0], ruta

        # El bloque de captura no arrastra lo demás, ni al revés.
        assert "pickerRenderTorres" not in captura[0], ruta
        assert "cargarTimelineDiferido" not in captura[0], ruta
        assert "BrowserMultiFormatReader" not in resto[0], ruta

        # Los estilos de escaneo se emiten una sola vez por página.
        assert r.text.count(".scan-msg {") == 1, ruta


# --------------------------------------------------------------------------- #
# Ticket 03 — la guardia del Enter llega a /announce y al Recibir de /consultar por reusar el
# mismo componente: el bloque de captura es el MISMO en todas las páginas que lo cargan.
# El comportamiento en sí (Enter/Tab/envío) se prueba en el seam de navegador real.
# --------------------------------------------------------------------------- #
def test_el_bloque_de_captura_es_el_mismo_en_todas_las_paginas_con_recibir(client):
    _login_staff(client)
    p = _anunciar(client)

    rutas = ["/paquetes", f"/consultar?q={p.access_code}", "/announce", "/residentes"]
    bloques = {}
    for ruta in rutas:
        r = client.get(ruta)
        assert r.status_code == 200, ruta
        captura = _bloques_con(r.text, "BrowserMultiFormatReader")
        assert len(captura) == 1, ruta
        bloques[ruta] = captura[0]

    de_referencia = bloques["/paquetes"]
    for ruta, bloque in bloques.items():
        assert bloque == de_referencia, f"{ruta} carga un bloque de captura distinto"


# --------------------------------------------------------------------------- #
# Ticket 04 — guía de más de 50 caracteres: rechazo con mensaje, sin 500 y sin efectos.
# La columna admite 50; `receive()` guarda la guía NORMALIZADA (mayúsculas, espacios colapsados,
# recortada), así que ese es el largo que cuenta.
# --------------------------------------------------------------------------- #
def _tag_del_modal_recibir(html, paquete_id):
    """La etiqueta de apertura `<div id="modal-receive-...">` (dice si el modal llega abierto)."""
    m = re.search(rf'<div id="modal-receive-{paquete_id}"[^>]*>', html)
    assert m, "el modal Recibir del paquete no está en la página"
    return m.group(0)


def test_recibir_con_una_guia_de_mas_de_50_caracteres_reabre_el_modal_con_el_mensaje(client):
    from app.domain.paquete import EstadoPaquete, Paquete

    _login_staff(client)
    p = _anunciar(client)

    r = client.post(
        f"/paquetes/{p.id}/recibir", data={"guide_number": "A" * 51}, follow_redirects=False
    )

    assert r.status_code == 400
    mensaje = "La guía tiene 51 caracteres; el máximo es 50."
    assert mensaje in r.text
    # El modal Recibir de ESE paquete llega abierto y el mensaje está dentro de él, junto al campo.
    assert " hidden" not in _tag_del_modal_recibir(r.text, p.id)
    desde = r.text.index(f'id="modal-receive-{p.id}"')
    assert mensaje in r.text[desde:]
    client.db.expire_all()
    recibido = client.db.get(Paquete, p.id)
    assert recibido.estado == EstadoPaquete.ANUNCIADO
    assert recibido.guide_number is None


def test_la_validacion_del_largo_ocurre_antes_de_cualquier_efecto_de_recibir(client):
    """Recibir hace cosas ANTES de recibir (declarar la unidad, crear un Ocupante, registrar el pago
    al mensajero): un rechazo por guía larga no puede dejar ninguna a medias."""
    from app.domain.ocupante import Ocupante
    from app.domain.paquete import EstadoPaquete, Paquete
    from app.domain.persona import Persona
    from app.domain.saldo_contra_entrega import MovimientoSaldoContraEntrega

    _login_staff(client)
    p = _anunciar(client)
    personas_antes = client.db.query(Persona).count()

    r = client.post(
        f"/paquetes/{p.id}/recibir",
        data={
            "guide_number": "B" * 60,
            "torre": "TORRE 1",
            "apartamento": "101",
            "candidato_idx": "nuevo",
            "nuevo_ocupante_nombre": "Luis",
            "nuevo_ocupante_contacto": "3009998877",
            "monto_pagado_mensajero": "3000",
        },
        follow_redirects=False,
    )

    assert r.status_code == 400
    client.db.expire_all()
    intacto = client.db.get(Paquete, p.id)
    assert intacto.estado == EstadoPaquete.ANUNCIADO
    assert intacto.snapshot_apartamento is None  # la unidad no se declaró
    assert client.db.query(Ocupante).count() == 0  # ningún Ocupante creado
    assert client.db.query(Persona).count() == personas_antes  # ninguna Persona nueva
    assert client.db.query(MovimientoSaldoContraEntrega).count() == 0  # ningún pago registrado


def test_una_guia_de_exactamente_50_caracteres_se_guarda(client):
    from app.domain.paquete import EstadoPaquete, Paquete

    _login_staff(client)
    p = _anunciar(client)

    r = client.post(
        f"/paquetes/{p.id}/recibir", data={"guide_number": "c" * 50}, follow_redirects=False
    )

    assert r.status_code == 303
    client.db.expire_all()
    recibido = client.db.get(Paquete, p.id)
    assert recibido.estado == EstadoPaquete.RECIBIDO
    assert recibido.guide_number == "C" * 50


def test_una_guia_larga_que_queda_en_50_o_menos_al_normalizar_se_guarda(client):
    """60 caracteres crudos, pero 30 + 1 + 15 = 46 una vez colapsados los espacios."""
    from app.domain.paquete import EstadoPaquete, Paquete

    _login_staff(client)
    p = _anunciar(client)

    r = client.post(
        f"/paquetes/{p.id}/recibir",
        data={"guide_number": "a" * 30 + " " * 15 + "b" * 15},
        follow_redirects=False,
    )

    assert r.status_code == 303
    client.db.expire_all()
    recibido = client.db.get(Paquete, p.id)
    assert recibido.estado == EstadoPaquete.RECIBIDO
    assert recibido.guide_number == "A" * 30 + " " + "B" * 15


def test_desde_consultar_una_guia_larga_reabre_el_recibir_de_consultar_con_el_mensaje(client):
    """Revisión del ticket 04 (historia 40 del spec): antes, con `origen=consultar`, el rechazo era un 303 mudo
    y el Operador no veía nada. Ahora /consultar se vuelve a pintar con el modal Recibir abierto y el mensaje
    DENTRO, sin recibir el paquete."""
    from app.domain.paquete import EstadoPaquete, Paquete

    _login_staff(client)
    p = _anunciar(client)

    r = client.post(
        f"/paquetes/{p.id}/recibir",
        data={"guide_number": "A" * 51, "origen": "consultar", "q": p.access_code},
        follow_redirects=False,
    )

    assert r.status_code == 400
    assert " hidden" not in _tag_del_modal_recibir(r.text, p.id)  # el modal llega abierto
    desde = r.text.index(f'id="modal-receive-{p.id}"')
    assert "La guía tiene 51 caracteres; el máximo es 50." in r.text[desde:]
    client.db.expire_all()
    assert client.db.get(Paquete, p.id).estado == EstadoPaquete.ANUNCIADO


def test_el_campo_guia_de_recibir_no_lleva_maxlength(client):
    """Un `maxlength` cortaría EN SILENCIO lo que inyecta el lector del F7: el rechazo es un mensaje."""
    _login_staff(client)
    _anunciar(client)

    html = client.get("/paquetes").text
    campo = re.search(r'<input[^>]*name="guide_number"[^>]*>', html)
    assert campo, "no está el campo Guía"
    assert "maxlength" not in campo.group(0)


# --------------------------------------------------------------------------- #
# Ticket 08 — aviso de guía repetida: un servicio solo para Staff que devuelve CANTIDAD y ESTADOS
# (todos, incluido Cancelado), sin datos de nadie. La política no cambia: la Guía sigue siendo una
# referencia sin unicidad; dos paquetes con la misma guía se pueden recibir.
# --------------------------------------------------------------------------- #
def _paquete_con_guia(client, staff, guia, estado="RECIBIDO", tel="3001234567", nombre="Ana"):
    """Un Paquete que ya pasó por Recibir con `guia`, y llegó a `estado` (RECIBIDO, ENTREGADO o CANCELADO)."""
    from app.domain.paquete_lifecycle import cancel, deliver, receive

    p = _anunciar(client, tel=tel, nombre=nombre)
    receive(client.db, p, staff, guia)
    if estado == "ENTREGADO":
        deliver(client.db, p, staff)
    elif estado == "CANCELADO":
        cancel(client.db, p, staff, "ANUNCIO_ERRONEO")
    client.db.commit()
    return p


def test_el_servicio_de_guia_repetida_devuelve_cantidad_y_estados_sin_datos_personales(client):
    staff = _login_staff(client)
    con_guia = [
        _paquete_con_guia(client, staff, "GUIA-1", estado, tel="3001110000", nombre="Marta")
        for estado in ("RECIBIDO", "ENTREGADO", "CANCELADO")
    ]
    _paquete_con_guia(client, staff, "OTRA-2", "RECIBIDO", tel="3002220000", nombre="Sofia")

    r = client.get("/paquetes/guia-repetida", params={"guia": "guia-1"})

    assert r.status_code == 200
    assert r.json() == {
        "cantidad": 3,
        "por_estado": {"RECIBIDO": 1, "ENTREGADO": 1, "CANCELADO": 1},
    }
    # Nada que permita ver o identificar esos paquetes: ni nombres, ni teléfonos, ni códigos de acceso.
    texto = r.text.lower()
    for ajeno in ["marta", "3001110000"] + [p.access_code.lower() for p in con_guia]:
        assert ajeno not in texto


def test_el_servicio_de_guia_repetida_es_solo_para_staff(client):
    r = client.get(
        "/paquetes/guia-repetida", params={"guia": "GUIA-1"}, follow_redirects=False
    )

    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_la_comparacion_normaliza_la_guia_como_al_guardarla(client):
    staff = _login_staff(client)
    _paquete_con_guia(client, staff, "abc   123")  # se guarda como "ABC 123"

    r = client.get("/paquetes/guia-repetida", params={"guia": "  abc 123  "})

    assert r.json()["cantidad"] == 1


def test_el_servicio_puede_excluir_el_paquete_actual(client):
    staff = _login_staff(client)
    a = _paquete_con_guia(client, staff, "G-2", tel="3001110000", nombre="Marta")
    _paquete_con_guia(client, staff, "G-2", tel="3002220000", nombre="Sofia")

    r = client.get("/paquetes/guia-repetida", params={"guia": "G-2", "excluir": str(a.id)})

    assert r.json() == {"cantidad": 1, "por_estado": {"RECIBIDO": 1}}


def test_guia_vacia_en_blanco_o_demasiado_larga_no_cuenta_nada_ni_da_error(client):
    staff = _login_staff(client)
    _paquete_con_guia(client, staff, "G-3")

    for guia in ("", "   ", "X" * 60):
        r = client.get("/paquetes/guia-repetida", params={"guia": guia})
        assert r.status_code == 200
        assert r.json() == {"cantidad": 0, "por_estado": {}}


def test_dos_paquetes_con_la_misma_guia_se_pueden_recibir(client):
    from app.domain.paquete import EstadoPaquete, Paquete

    _login_staff(client)
    a = _anunciar(client, tel="3001110000", nombre="Marta")
    b = _anunciar(client, tel="3002220000", nombre="Sofia")

    for p in (a, b):
        r = client.post(
            f"/paquetes/{p.id}/recibir", data={"guide_number": "MISMA-1"}, follow_redirects=False
        )
        assert r.status_code == 303

    client.db.expire_all()
    for p in (a, b):
        recibido = client.db.get(Paquete, p.id)
        assert recibido.estado == EstadoPaquete.RECIBIDO
        assert recibido.guide_number == "MISMA-1"


def test_el_modal_recibir_trae_el_lugar_del_aviso_de_repetida_y_el_paquete_del_campo(client):
    _login_staff(client)
    p = _anunciar(client)

    html = client.get("/paquetes").text

    campo = re.search(r'<input[^>]*name="guide_number"[^>]*>', html).group(0)
    assert f'data-paquete-id="{p.id}"' in campo
    assert re.search(r'<p class="guia-repetida-msg"[^>]*\bhidden\b', html)


# --------------------------------------------------------------------------- #
# Ticket 10 — modo lector: el interruptor "Este equipo tiene lector" vive en el menú de cuenta (solo Staff),
# NO en el modal Recibir. Su comportamiento (foco, teclado, persistencia) se prueba en navegador real.
# --------------------------------------------------------------------------- #
def test_el_menu_de_cuenta_de_staff_trae_el_interruptor_del_modo_lector_apagado(client):
    _login_staff(client)
    _anunciar(client)

    for ruta in ("/paquetes", "/announce", "/residentes", "/consultar"):
        html = client.get(ruta).text
        boton = re.search(r'<button[^>]*data-modo-lector[^>]*>.*?</button>', html, re.S)
        assert boton, f"{ruta}: falta el interruptor en el menú de cuenta"
        assert "Este equipo tiene lector" in boton.group(0), ruta
        assert "Desactivado" in boton.group(0), ruta  # el HTML sale apagado; el JS lo pinta según el equipo


def test_el_interruptor_del_modo_lector_no_vive_en_el_modal_recibir(client):
    _login_staff(client)
    p = _anunciar(client)

    html = client.get("/paquetes").text

    desde = html.index(f'id="modal-receive-{p.id}"')
    hasta = html.index("</form>", desde)
    assert "data-modo-lector" not in html[desde:hasta]
    assert "Este equipo tiene lector" not in html[desde:hasta]


def test_un_visitante_sin_sesion_no_ve_el_interruptor_del_modo_lector(client):
    for ruta in ("/anunciar", "/consultar", "/ayuda"):
        html = client.get(ruta).text
        assert "data-modo-lector" not in html, ruta
        assert "Este equipo tiene lector" not in html, ruta
