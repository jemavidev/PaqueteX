# -*- coding: utf-8 -*-
"""
Capa web — vista de staff `/paquetes` (lista + Recibir).

Comportamiento observable por HTTP: la lista exige sesión y muestra el estado;
recibir transiciona el paquete y registra al actor de la sesión; recibir en un
estado inválido no tiene efecto; un id inexistente da 404.
"""

import uuid

from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_lifecycle import deliver as dom_deliver
from app.domain.paquete_lifecycle import receive as dom_receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin
from app.domain.usuario import Usuario

_PW = "Contrasena1"


def _login_staff(client, email="staff@club.com"):
    create_initial_admin(client.db, email, "Operador", _PW)
    client.db.commit()
    r = client.post("/ingresar", data={"email": email, "password": _PW})
    assert r.status_code == 200
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


def test_packages_sin_sesion_redirige_a_login(client):
    r = client.get("/paquetes", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_packages_con_sesion_lista_y_muestra_estado(client):
    _login_staff(client)
    p = _anunciar(client, nombre="Ana")
    r = client.get("/paquetes")
    assert r.status_code == 200
    assert "ANA" in r.text
    assert "ANUNCIADO" in r.text
    # sin columna de Código: el access_code no se muestra en la lista.
    assert p.access_code not in r.text


def test_encabezado_enlaza_a_announce(client):
    _login_staff(client)
    r = client.get("/paquetes")
    assert r.status_code == 200
    assert 'href="/announce"' in r.text


def test_recibir_transiciona_a_recibido_y_registra_al_actor(client):
    staff = _login_staff(client)
    p = _anunciar(client)

    r = client.post(
        f"/paquetes/{p.id}/recibir", data={"guide_number": "1Z-ABC-9"}, follow_redirects=False
    )
    assert r.status_code == 303  # PRG

    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.estado == EstadoPaquete.RECIBIDO
    assert p2.received_by_usuario_id == staff.id
    assert p2.guide_number == "1Z-ABC-9"


def test_recibir_sin_guia_es_valido(client):
    _login_staff(client)
    p = _anunciar(client)
    r = client.post(f"/paquetes/{p.id}/recibir", data={}, follow_redirects=False)
    assert r.status_code == 303
    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.estado == EstadoPaquete.RECIBIDO
    assert p2.guide_number is None


def test_recibir_con_tipo_condicion_y_foto(client):
    _login_staff(client)
    p = _anunciar(client)

    r = client.post(
        f"/paquetes/{p.id}/recibir",
        data={"package_type": "EXTRA_DIMENSIONADO", "package_condition": "ABIERTO"},
        files={"fotos": ("recibo.jpg", b"contenido-de-prueba", "image/jpeg")},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.package_type.value == "EXTRA_DIMENSIONADO"
    assert p2.package_condition.value == "ABIERTO"

    from app.domain.paquete_foto import PaqueteFoto

    fotos = client.db.query(PaqueteFoto).filter(PaqueteFoto.paquete_id == p.id).all()
    assert len(fotos) == 1
    assert fotos[0].url.startswith("/static/fotos-recibidas/")


# --------------------------------------------------------------------------- #
# Grupo 15 (Ronda 2) — hasta 3 fotos por paquete.
# --------------------------------------------------------------------------- #
def test_recibir_con_3_fotos_las_guarda_todas(client):
    from app.domain.paquete_foto import PaqueteFoto

    _login_staff(client)
    p = _anunciar(client)

    r = client.post(
        f"/paquetes/{p.id}/recibir",
        files=[
            ("fotos", ("a.jpg", b"foto-a", "image/jpeg")),
            ("fotos", ("b.jpg", b"foto-b", "image/jpeg")),
            ("fotos", ("c.jpg", b"foto-c", "image/jpeg")),
        ],
        follow_redirects=False,
    )
    assert r.status_code == 303

    fotos = client.db.query(PaqueteFoto).filter(PaqueteFoto.paquete_id == p.id).all()
    assert len(fotos) == 3


def test_recibir_con_4_fotos_solo_guarda_3_y_no_falla(client):
    from app.domain.paquete_foto import PaqueteFoto

    _login_staff(client)
    p = _anunciar(client)

    r = client.post(
        f"/paquetes/{p.id}/recibir",
        files=[
            ("fotos", ("a.jpg", b"foto-a", "image/jpeg")),
            ("fotos", ("b.jpg", b"foto-b", "image/jpeg")),
            ("fotos", ("c.jpg", b"foto-c", "image/jpeg")),
            ("fotos", ("d.jpg", b"foto-d", "image/jpeg")),
        ],
        follow_redirects=False,
    )
    assert r.status_code == 303  # recibir nunca falla por exceso de fotos

    client.db.expire_all()
    assert client.db.get(Paquete, p.id).estado == EstadoPaquete.RECIBIDO
    fotos = client.db.query(PaqueteFoto).filter(PaqueteFoto.paquete_id == p.id).all()
    assert len(fotos) == 3


def test_recibir_sin_tipo_ni_condicion_usa_defaults(client):
    _login_staff(client)
    p = _anunciar(client)
    client.post(f"/paquetes/{p.id}/recibir", data={})

    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.package_type.value == "NORMAL"
    assert p2.package_condition.value == "BUENO"


def test_recibir_un_no_anunciado_se_rechaza_sin_efecto(client):
    staff = _login_staff(client)
    p = _anunciar(client)
    dom_receive(client.db, p, staff)  # ya RECIBIDO por el dominio
    client.db.commit()

    r = client.post(f"/paquetes/{p.id}/recibir", data={})
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.get(Paquete, p.id).estado == EstadoPaquete.RECIBIDO


def test_recibir_id_inexistente_da_404(client):
    _login_staff(client)
    r = client.post(f"/paquetes/{uuid.uuid4()}/recibir", data={})
    assert r.status_code == 404


def test_recibir_sin_sesion_redirige_a_login(client):
    p = _anunciar(client)
    r = client.post(f"/paquetes/{p.id}/recibir", data={}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


# --------------------------------------------------------------------------- #
# Entregar (ticket 02)
# --------------------------------------------------------------------------- #
def _recibir(client, staff, p):
    dom_receive(client.db, p, staff)
    client.db.commit()


def test_entregar_un_recibido_transiciona_y_registra_al_actor(client):
    staff = _login_staff(client)
    p = _anunciar(client)
    _recibir(client, staff, p)

    r = client.post(f"/paquetes/{p.id}/entregar", follow_redirects=False)
    assert r.status_code == 303

    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.estado == EstadoPaquete.ENTREGADO
    assert p2.delivered_by_usuario_id == staff.id


def test_entregar_un_no_recibido_se_rechaza_sin_efecto(client):
    _login_staff(client)
    p = _anunciar(client)  # sigue ANUNCIADO

    r = client.post(f"/paquetes/{p.id}/entregar")
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.get(Paquete, p.id).estado == EstadoPaquete.ANUNCIADO


def test_entregar_sin_sesion_redirige_a_login(client):
    p = _anunciar(client)
    r = client.post(f"/paquetes/{p.id}/entregar", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


# --------------------------------------------------------------------------- #
# Cancelar (ticket 03)
# --------------------------------------------------------------------------- #
def test_cancelar_desde_anunciado_registra_actor_y_motivo(client):
    staff = _login_staff(client)
    p = _anunciar(client)

    r = client.post(
        f"/paquetes/{p.id}/cancelar",
        data={"motivo": "ANUNCIO_ERRONEO"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.estado == EstadoPaquete.CANCELADO
    assert p2.cancelled_by_usuario_id == staff.id
    assert p2.cancel_reason == "ANUNCIO_ERRONEO"


def test_cancelar_desde_recibido(client):
    staff = _login_staff(client)
    p = _anunciar(client)
    _recibir(client, staff, p)

    r = client.post(
        f"/paquetes/{p.id}/cancelar",
        data={"motivo": "DEVUELTO_AL_TRANSPORTADOR"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    client.db.expire_all()
    assert client.db.get(Paquete, p.id).estado == EstadoPaquete.CANCELADO


def test_cancelar_sin_motivo_se_rechaza_sin_efecto(client):
    _login_staff(client)
    p = _anunciar(client)

    r = client.post(f"/paquetes/{p.id}/cancelar", data={})
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.get(Paquete, p.id).estado == EstadoPaquete.ANUNCIADO


def test_cancelar_un_terminal_se_rechaza_sin_efecto(client):
    staff = _login_staff(client)
    p = _anunciar(client)
    dom_receive(client.db, p, staff)
    dom_deliver(client.db, p, staff)  # ENTREGADO (terminal)
    client.db.commit()

    r = client.post(f"/paquetes/{p.id}/cancelar", data={"motivo": "OTRO"})
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.get(Paquete, p.id).estado == EstadoPaquete.ENTREGADO


def test_cancelar_sin_sesion_redirige_a_login(client):
    p = _anunciar(client)
    r = client.post(
        f"/paquetes/{p.id}/cancelar", data={"motivo": "OTRO"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


# --------------------------------------------------------------------------- #
# Escáner ZXing (ticket 04) — cobertura automatizable: asset servido + disparador.
# El decode por cámara es client-side y se verifica manual/e2e (no hay cámara en CI).
# --------------------------------------------------------------------------- #
def test_el_asset_zxing_se_sirve(client):
    r = client.get("/static/vendor/zxing.min.js")
    assert r.status_code == 200
    assert "BrowserMultiFormatReader" in r.text


def test_el_modal_recibir_incluye_el_disparador_de_escaneo(client):
    _login_staff(client)
    _anunciar(client)
    r = client.get("/paquetes")
    assert r.status_code == 200
    assert "scan-btn" in r.text  # el botón "Escanear" vive en el modal Recibir
    assert "zxing.min.js" in r.text  # el bundle se carga (lazy) desde /static


# --------------------------------------------------------------------------- #
# Grupo 14 (Ronda 2) — doble escaneo de guía al entregar (opcional, visual).
# --------------------------------------------------------------------------- #
def test_modal_entregar_incluye_escaneo_si_el_paquete_tiene_guia(client):
    staff = _login_staff(client)
    p = _anunciar(client)
    dom_receive(client.db, p, staff, "1Z-ABC-9")
    client.db.commit()

    r = client.get("/paquetes")
    idx = r.text.index('id="modal-deliver-' + str(p.id) + '"')
    fin = r.text.find('<div id="modal-', idx + 1)
    modal_html = r.text[idx:fin] if fin != -1 else r.text[idx:]
    assert "scan-btn" in modal_html
    assert 'data-guia-esperada="1Z-ABC-9"' in modal_html


def test_modal_entregar_sin_escaneo_si_el_paquete_no_tiene_guia(client):
    staff = _login_staff(client)
    p = _anunciar(client)
    dom_receive(client.db, p, staff)  # sin guide_number
    client.db.commit()

    r = client.get("/paquetes")
    idx = r.text.index('id="modal-deliver-' + str(p.id) + '"')
    fin = r.text.find('<div id="modal-', idx + 1)
    modal_html = r.text[idx:fin] if fin != -1 else r.text[idx:]
    assert "data-guia-esperada" not in modal_html
    assert "Confirmar entrega" in modal_html  # el resto del modal sigue ahí


def test_entregar_sigue_funcionando_sin_confirmar_la_guia(client):
    """El escaneo en Entregar es puramente visual (JS) -- el POST no cambia,
    ningún campo nuevo es obligatorio."""
    staff = _login_staff(client)
    p = _anunciar(client)
    dom_receive(client.db, p, staff, "1Z-ABC-9")
    client.db.commit()

    r = client.post(f"/paquetes/{p.id}/entregar", follow_redirects=False)
    assert r.status_code == 303
    client.db.expire_all()
    assert client.db.get(Paquete, p.id).estado == EstadoPaquete.ENTREGADO


# --------------------------------------------------------------------------- #
# Advertencia de nombre no coincide (Grupo 1, ticket 03) — se calcula al leer.
# --------------------------------------------------------------------------- #
def test_advertencia_aparece_cuando_el_nombre_no_coincide_con_el_registrado(client):
    _login_staff(client)
    # Ana ya está registrada; alguien anuncia con su teléfono pero declara un
    # nombre distinto (typo o tercero) -- vía el nuevo modo del cliente.
    from app.domain.persona_service import get_or_create_persona

    get_or_create_persona(client.db, "3001234567", "Ana Perez")
    client.db.commit()
    announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana Perez",
        destinatario=Destinatario.declarado_por_cliente("Ana Peres"),
    )
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert "no coincide" in r.text.lower()


def test_advertencia_no_aparece_cuando_el_nombre_coincide(client):
    _login_staff(client)
    _anunciar(client, nombre="Ana")

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert "no coincide" not in r.text.lower()


def test_advertencia_no_bloquea_las_acciones_normales(client):
    _login_staff(client)
    from app.domain.persona_service import get_or_create_persona

    get_or_create_persona(client.db, "3001234567", "Ana Perez")
    client.db.commit()
    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana Perez",
        destinatario=Destinatario.declarado_por_cliente("Ana Peres"),
    )
    client.db.commit()

    r = client.post(
        f"/paquetes/{p.id}/recibir", data={}, follow_redirects=False
    )
    assert r.status_code == 303


# --------------------------------------------------------------------------- #
# Corregir destinatario (Grupo 6, ticket 02) — solo mientras ANUNCIADO.
# --------------------------------------------------------------------------- #
def test_boton_corregir_aparece_solo_en_anunciado(client):
    staff = _login_staff(client)
    anunciado = _anunciar(client, tel="3001234567", nombre="Ana")
    recibido = _anunciar(client, tel="3019999999", nombre="Beto")
    dom_receive(client.db, recibido, staff)
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert f'modal-correct-{anunciado.id}' in r.text
    assert f'modal-correct-{recibido.id}' not in r.text


def test_corregir_actualiza_nombre_y_quita_la_advertencia(client):
    _login_staff(client)
    from app.domain.persona_service import get_or_create_persona

    get_or_create_persona(client.db, "3001234567", "Ana Perez")
    client.db.commit()
    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana Perez",
        destinatario=Destinatario.declarado_por_cliente("Ana Peres"),
    )
    client.db.commit()

    # Único candidato posible aquí (sin Apartamento): el propio Anunciante,
    # "Ana Perez" -- índice 0.
    r = client.post(
        f"/paquetes/{p.id}/corregir",
        data={"candidato_idx": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.get(Paquete, p.id).recipient_name == "ANA PEREZ"
    r2 = client.get("/paquetes")
    assert "no coincide" not in r2.text.lower()


# --------------------------------------------------------------------------- #
# Grupo 16 (Ronda 2) — Corregir por selección de Ocupantes conocidos.
# --------------------------------------------------------------------------- #
def test_modal_corregir_muestra_select_cuando_hay_candidatos(client):
    _login_staff(client)
    p = _anunciar(client, tel="3001234567", nombre="Ana")

    r = client.get("/paquetes")
    assert r.status_code == 200
    idx = r.text.index(f'id="modal-correct-{p.id}"')
    fin = r.text.find('<div id="modal-', idx + 1)
    modal_html = r.text[idx:fin] if fin != -1 else r.text[idx:]
    assert f'name="candidato_idx"' in modal_html
    assert 'name="recipient_name"' not in modal_html


def test_corregir_con_candidato_invalido_se_rechaza_sin_efecto(client):
    _login_staff(client)
    p = _anunciar(client, tel="3001234567", nombre="Ana")

    r = client.post(f"/paquetes/{p.id}/corregir", data={"candidato_idx": "99"})
    assert r.status_code == 400

    client.db.expire_all()
    assert client.db.get(Paquete, p.id).recipient_name == "ANA"


def test_corregir_selecciona_ocupante_del_apartamento_del_snapshot(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    staff = _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Jesus Villalobos", "3033333333")
    client.db.commit()

    p = announce(
        client.db,
        anunciante_telefono="3044444444",
        anunciante_nombre="Portero",
        destinatario=Destinatario.declarado_por_cliente("Jesu Villalobos"),
        apartamento=apto,
    )
    client.db.commit()

    r = client.get("/paquetes")
    idx = r.text.index(f'id="modal-correct-{p.id}"')
    fin = r.text.find('<div id="modal-', idx + 1)
    modal_html = r.text[idx:fin] if fin != -1 else r.text[idx:]
    assert "JESUS VILLALOBOS" in modal_html

    r2 = client.post(
        f"/paquetes/{p.id}/corregir", data={"candidato_idx": "0"}, follow_redirects=False
    )
    assert r2.status_code == 303

    client.db.expire_all()
    assert client.db.get(Paquete, p.id).recipient_name == "JESUS VILLALOBOS"


# --------------------------------------------------------------------------- #
# Ticket 09 (.scratch/mis-datos) — "Corregir destinatario": declarar un
# Ocupante NUEVO del apartamento directamente ahí.
# --------------------------------------------------------------------------- #
def test_corregir_declara_ocupante_nuevo_sin_telefono(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    ana = agregar_ocupante(client.db, apto, "Ana", telefono="3033333333")
    confirmar_ocupante(client.db, ana, staff)  # Ana confirmada como principal (ticket 06)
    client.db.commit()

    p = announce(
        client.db,
        anunciante_telefono="3044444444",
        anunciante_nombre="Portero",
        destinatario=Destinatario.declarado_por_cliente("Nombre Que No Coincide"),
        apartamento=apto,
    )
    client.db.commit()

    r = client.post(
        f"/paquetes/{p.id}/corregir",
        data={"candidato_idx": "nuevo", "nuevo_ocupante_nombre": "Hijo"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    paquete = client.db.get(Paquete, p.id)
    assert paquete.recipient_name == "HIJO"
    assert paquete.recipient_phone == "+573033333333"  # el del principal (Ana)

    nuevo = client.db.query(Ocupante).filter(
        Ocupante.apartamento_id == apto.id, Ocupante.nombre == "HIJO"
    ).one()
    assert nuevo.persona_id is None


def test_corregir_declara_ocupante_nuevo_con_telefono(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", telefono="3033333333")
    client.db.commit()

    p = announce(
        client.db,
        anunciante_telefono="3044444444",
        anunciante_nombre="Portero",
        destinatario=Destinatario.declarado_por_cliente("Nombre Que No Coincide"),
        apartamento=apto,
    )
    client.db.commit()

    r = client.post(
        f"/paquetes/{p.id}/corregir",
        data={
            "candidato_idx": "nuevo",
            "nuevo_ocupante_nombre": "Hija",
            "nuevo_ocupante_telefono": "3021112233",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    paquete = client.db.get(Paquete, p.id)
    assert paquete.recipient_name == "HIJA"
    assert paquete.recipient_phone == "+573021112233"  # el propio de Hija


def test_corregir_ocupante_nuevo_sin_nombre_se_rechaza(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", telefono="3033333333")
    client.db.commit()

    p = announce(
        client.db,
        anunciante_telefono="3044444444",
        anunciante_nombre="Portero",
        destinatario=Destinatario.declarado_por_cliente("Nombre Que No Coincide"),
        apartamento=apto,
    )
    client.db.commit()

    r = client.post(
        f"/paquetes/{p.id}/corregir", data={"candidato_idx": "nuevo"}
    )
    assert r.status_code == 400


def test_corregir_un_recibido_se_rechaza_sin_efecto(client):
    staff = _login_staff(client)
    p = _anunciar(client)
    dom_receive(client.db, p, staff)
    client.db.commit()
    nombre_original = p.recipient_name

    r = client.post(
        f"/paquetes/{p.id}/corregir", data={"recipient_name": "Otro Nombre"}
    )
    assert r.status_code == 400

    client.db.expire_all()
    assert client.db.get(Paquete, p.id).recipient_name == nombre_original


# --------------------------------------------------------------------------- #
# Filtros y paginación (Grupo 5, ticket 02)
# --------------------------------------------------------------------------- #
def test_filtro_por_estado(client):
    staff = _login_staff(client)
    anunciado = _anunciar(client, tel="3001234567", nombre="Ana")
    recibido = _anunciar(client, tel="3019999999", nombre="Beto")
    dom_receive(client.db, recibido, staff)
    client.db.commit()

    r = client.get("/paquetes", params={"estado": "RECIBIDO"})
    assert r.status_code == 200
    assert "BETO" in r.text
    assert "ANA" not in r.text


def test_peticion_en_vivo_devuelve_solo_el_fragmento(client):
    # Ticket 03 (.scratch/paquetes-busqueda-viva): el fetch en vivo de la
    # barra de búsqueda marca su petición con X-Requested-With: fetch -- la
    # ruta responde SOLO tarjetas+paginación (sin el layout de la página
    # completa), mientras que una carga normal (sin el header) sigue
    # devolviendo la página entera.
    _login_staff(client)
    _anunciar(client, nombre="Ana")
    client.db.commit()

    normal = client.get("/paquetes")
    assert normal.status_code == 200
    assert "<h1" in normal.text
    assert "ANA" in normal.text

    fragmento = client.get("/paquetes", headers={"X-Requested-With": "fetch"})
    assert fragmento.status_code == 200
    assert "<h1" not in fragmento.text
    assert "<html" not in fragmento.text
    assert "ANA" in fragmento.text


def test_ausencia_de_estado_devuelve_todos_los_estados(client):
    # Ya no existe un ícono "Todos" (ticket 02, .scratch/paquetes-busqueda-viva)
    # -- la ausencia del parámetro `estado` en la URL ES "todos los estados",
    # el mismo resultado que antes daba el chip "Todos" explícito. Cubre tanto
    # la carga inicial de /paquetes como el resultado de "desactivar" un
    # ícono de Estado (que quita el parámetro de la URL) o de resetear.
    staff = _login_staff(client)
    _anunciar(client, tel="3001234567", nombre="Ana")
    recibido = _anunciar(client, tel="3019999999", nombre="Beto")
    dom_receive(client.db, recibido, staff)
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert "ANA" in r.text
    assert "BETO" in r.text

    r2 = client.get("/paquetes", params={"estado": ""})
    assert r2.status_code == 200
    assert "ANA" in r2.text
    assert "BETO" in r2.text


def test_filtro_por_q_encuentra_por_access_code_parcial(client):
    _login_staff(client)
    p = _anunciar(client, nombre="Ana")
    otro = _anunciar(client, tel="3019999999", nombre="Beto")
    client.db.commit()

    r = client.get("/paquetes", params={"q": p.access_code[:3]})
    assert r.status_code == 200
    assert "ANA" in r.text
    assert "BETO" not in r.text


def test_filtro_por_q_encuentra_por_guia_parcial(client):
    staff = _login_staff(client)
    p = _anunciar(client, tel="3001234567", nombre="Ana")
    otro = _anunciar(client, tel="3019999999", nombre="Beto")
    dom_receive(client.db, p, staff, "ABC123456")
    client.db.commit()

    r = client.get("/paquetes", params={"q": "ABC123"})
    assert r.status_code == 200
    assert "ANA" in r.text
    assert "BETO" not in r.text


def test_filtro_por_q_encuentra_por_nombre_parcial(client):
    _login_staff(client)
    _anunciar(client, tel="3001234567", nombre="Ana Perez")
    _anunciar(client, tel="3019999999", nombre="Beto Gomez")
    client.db.commit()

    r = client.get("/paquetes", params={"q": "perez"})
    assert r.status_code == 200
    assert "ANA PEREZ" in r.text
    assert "Beto Gomez" not in r.text


def test_filtro_por_q_encuentra_por_nombre_del_anunciante_cuando_difiere_del_destinatario(client):
    # El destinatario declarado puede diferir del nombre YA REGISTRADO del
    # Anunciante (ver test_advertencia_aparece_cuando_el_nombre_no_coincide_
    # con_el_registrado) -- q debe encontrar el paquete por CUALQUIERA de los
    # dos nombres, no solo por el del destinatario.
    from app.domain.persona_service import get_or_create_persona

    _login_staff(client)
    get_or_create_persona(client.db, "3001234567", "Ana Perez")
    client.db.commit()
    announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana Perez",
        destinatario=Destinatario.declarado_por_cliente("Un Vecino"),
    )
    client.db.commit()

    r = client.get("/paquetes", params={"q": "perez"})
    assert r.status_code == 200
    assert "UN VECINO" in r.text


def test_filtro_por_q_encuentra_por_whatsapp_usuario(client):
    from app.domain.persona_service import get_or_create_persona, update_datos_personales

    _login_staff(client)
    ana = get_or_create_persona(client.db, "3001234567", "Ana")
    update_datos_personales(client.db, ana, whatsapp_usuario="ana.whats")
    client.db.commit()
    _anunciar(client, tel="3001234567", nombre="Ana")
    _anunciar(client, tel="3019999999", nombre="Beto")
    client.db.commit()

    r = client.get("/paquetes", params={"q": "ana.whats"})
    assert r.status_code == 200
    assert "ANA" in r.text
    assert "BETO" not in r.text


def test_filtro_por_q_encuentra_por_telefono(client):
    _login_staff(client)
    _anunciar(client, tel="3001234567", nombre="Ana")
    _anunciar(client, tel="3019999999", nombre="Beto")
    client.db.commit()

    r = client.get("/paquetes", params={"q": "3001234567"})
    assert r.status_code == 200
    assert "ANA" in r.text
    assert "BETO" not in r.text


def test_filtro_por_q_encuentra_por_torre_o_apartamento(client):
    from app.domain.apartamento_service import resolver_apartamento

    _login_staff(client)
    apto1 = resolver_apartamento(client.db, "TORRE 1", "101")
    apto2 = resolver_apartamento(client.db, "TORRE 2", "202")
    client.db.commit()

    p1 = announce(
        client.db, "3001234567", "Ana", Destinatario.yo_mismo(), apartamento=apto1
    )
    p2 = announce(
        client.db, "3019999999", "Beto", Destinatario.yo_mismo(), apartamento=apto2
    )
    client.db.commit()

    r = client.get("/paquetes", params={"q": "TORRE 1"})
    assert r.status_code == 200
    assert "ANA" in r.text
    assert "BETO" not in r.text

    r2 = client.get("/paquetes", params={"q": "202"})
    assert "BETO" in r2.text
    assert "ANA" not in r2.text


def test_filtros_combinados(client):
    staff = _login_staff(client)
    from app.domain.apartamento_service import resolver_apartamento

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    client.db.commit()

    p1 = announce(client.db, "3001234567", "Ana", Destinatario.yo_mismo(), apartamento=apto)
    p2 = announce(client.db, "3019999999", "Beto", Destinatario.yo_mismo(), apartamento=apto)
    client.db.commit()
    dom_receive(client.db, p2, staff)
    client.db.commit()

    r = client.get("/paquetes", params={"q": "TORRE 1", "estado": "RECIBIDO"})
    assert r.status_code == 200
    assert "BETO" in r.text
    assert "ANA" not in r.text


def test_parametros_torre_apartamento_obsoletos_se_ignoran_sin_error(client):
    # Los parámetros dedicados desaparecieron de la ruta (folded en `q`) --
    # que alguien todavía los mande (enlace viejo en caché, etc.) no debe
    # romper la página.
    _login_staff(client)
    _anunciar(client, nombre="Ana")
    client.db.commit()

    r = client.get("/paquetes", params={"torre": "TORRE 1", "apartamento": "101"})
    assert r.status_code == 200
    assert "ANA" in r.text


def test_paginacion_con_mas_de_20_paquetes(client):
    _login_staff(client)
    for i in range(25):
        announce(
            client.db,
            f"300{i:07d}",
            f"Cliente{i}",
            Destinatario.yo_mismo(),
        )
    client.db.commit()

    r1 = client.get("/paquetes")
    assert r1.status_code == 200
    assert "CLIENTE24" in r1.text  # el más reciente, página 1
    assert 'aria-label="Paginación"' in r1.text  # el nav de paginación se renderiza

    r2 = client.get("/paquetes", params={"pagina": 2})
    assert r2.status_code == 200
    assert "CLIENTE0" in r2.text  # el más viejo, cae en la página 2


# --------------------------------------------------------------------------- #
# Grupo 11 (Ronda 2) — actor de la última acción visible en cada tarjeta.
# --------------------------------------------------------------------------- #
def test_tarjeta_de_anunciado_muestra_el_actor_del_anuncio(client):
    staff = _login_staff(client)
    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
        staff_actor=staff,
    )
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert staff.nombre in r.text


def test_tarjeta_de_cancelado_muestra_el_actor_de_la_cancelacion_no_el_de_recepcion(client):
    staff_recibe = _login_staff(client, email="recibe@club.com")
    p = _anunciar(client)
    _recibir(client, staff_recibe, p)

    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    from app.domain.paquete_lifecycle import cancel as dom_cancel
    from app.domain.staff_service import create_staff
    from app.domain.usuario import RolUsuario

    staff_cancela = create_staff(
        client.db, staff_recibe, "cancela@club.com", "Cancela", _PW, RolUsuario.OPERADOR
    )
    client.db.commit()
    dom_cancel(client.db, p2, staff_cancela, "NO_RECLAMADO")
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    idx = r.text.index(p2.recipient_name)
    tarjeta = r.text[idx : idx + 800]
    assert staff_cancela.nombre in tarjeta
    assert staff_recibe.nombre not in tarjeta


# --------------------------------------------------------------------------- #
# Regresión de rendimiento (auditoría 2026-08-10, .scratch/pendientes-cliente):
# cargar /paquetes disparaba una consulta de Persona/Usuario/Apartamento/
# Ocupante POR CADA paquete de la página (N+1) -- bajo carga concurrente
# agotaba el pool de conexiones de la BD y el sitio "se sentía pesado" al
# navegar. El fix batchea esas consultas a un puñado FIJO por página, sin
# importar cuántos paquetes tenga.
# --------------------------------------------------------------------------- #
def test_lista_no_dispara_una_query_de_persona_o_usuario_por_paquete(client):
    from sqlalchemy import event

    staff = _login_staff(client)
    from app.domain.staff_service import create_staff
    from app.domain.usuario import RolUsuario

    staff2 = create_staff(client.db, staff, "op2@club.com", "Op2", _PW, RolUsuario.OPERADOR)
    client.db.commit()

    # 8 paquetes, cada uno con un Anunciante DISTINTO (fuerza N Personas
    # distintas) y actores distintos (fuerza N Usuarios distintos) -- si el
    # N+1 volviera, esto lo haría subir de forma visible.
    paquetes = [_anunciar(client, tel=f"300111{i:04d}", nombre=f"Persona{i}") for i in range(8)]
    for i, p in enumerate(paquetes[:4]):
        client.db.expire_all()
        p2 = client.db.get(Paquete, p.id)
        dom_receive(client.db, p2, staff if i % 2 == 0 else staff2)
        client.db.commit()

    queries = []

    def _contar(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    engine = client.db.get_bind()
    event.listen(engine, "before_cursor_execute", _contar)
    try:
        r = client.get("/paquetes")
    finally:
        event.remove(engine, "before_cursor_execute", _contar)

    assert r.status_code == 200
    # Umbral generoso (deja margen para la query de listado/paginación/
    # count + un puñado de lookups batch) pero muy por debajo de lo que
    # daría 1+ query por cada uno de los 8 paquetes -- si el N+1 se
    # reintrodujera, este número saltaría con la cantidad de paquetes, no
    # se quedaría fijo.
    assert len(queries) <= 10, (
        f"{len(queries)} queries para 8 paquetes -- parece que volvió el N+1 "
        "(ver _listar en packages.py)"
    )
