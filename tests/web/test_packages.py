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
    # Conversación 2026-08-15: el código de acceso SÍ se muestra -- esta
    # pantalla es staff-only (current_staff), el cliente sigue sin verlo en
    # /consultar ni /mis-paquetes (eso no cambió, solo /paquetes).
    assert p.access_code in r.text


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
# Recibir: paso nuevo de resolución de apartamento/destinatario
# (.scratch/ocupante-principal-escenarios, ticket 05)
# --------------------------------------------------------------------------- #
def test_recibir_declara_apartamento_cuando_el_destinatario_no_tenia(client):
    from app.domain.apartamento_service import resolver_apartamento

    _login_staff(client)
    resolver_apartamento(client.db, "TORRE 1", "101")  # asegura que existe en el catálogo
    p = _anunciar(client)  # sin apartamento -- Destinatario.yo_mismo(), sin unidad
    assert p.snapshot_apartamento is None

    r = client.post(
        f"/paquetes/{p.id}/recibir",
        data={"torre": "TORRE 1", "apartamento": "101"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.estado == EstadoPaquete.RECIBIDO
    assert p2.snapshot_torre == "TORRE 1"
    assert p2.snapshot_apartamento == "101"


def test_recibir_no_pisa_un_apartamento_que_ya_tenia(client):
    from app.domain.apartamento_service import resolver_apartamento

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
        apartamento=apto,
    )
    client.db.commit()

    r = client.post(
        f"/paquetes/{p.id}/recibir",
        data={"torre": "TORRE 2", "apartamento": "202"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.snapshot_torre == "TORRE 1"  # sin cambios -- ya tenía uno


def test_recibir_con_torre_apto_invalido_no_recibe(client):
    _login_staff(client)
    p = _anunciar(client)

    r = client.post(
        f"/paquetes/{p.id}/recibir",
        data={"torre": "TORRE 99", "apartamento": "101"},
    )
    assert r.status_code == 400

    client.db.expire_all()
    assert client.db.get(Paquete, p.id).estado == EstadoPaquete.ANUNCIADO


def test_recibir_elige_un_residente_existente_de_la_unidad(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Jesus Villalobos", "3033333333")
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
        f"/paquetes/{p.id}/recibir",
        data={"candidato_idx": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.estado == EstadoPaquete.RECIBIDO
    assert p2.recipient_name == "JESUS VILLALOBOS"


def test_recibir_registra_un_residente_nuevo_de_la_unidad(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante
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
        f"/paquetes/{p.id}/recibir",
        data={"candidato_idx": "nuevo", "nuevo_ocupante_nombre": "Hija"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.estado == EstadoPaquete.RECIBIDO
    assert p2.recipient_name == "HIJA"
    nuevo = (
        client.db.query(Ocupante)
        .filter(Ocupante.apartamento_id == apto.id, Ocupante.nombre == "HIJA")
        .one()
    )
    assert nuevo.id is not None


def test_recibir_con_candidato_invalido_no_recibe(client):
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

    r = client.post(f"/paquetes/{p.id}/recibir", data={"candidato_idx": "99"})
    assert r.status_code == 400

    client.db.expire_all()
    assert client.db.get(Paquete, p.id).estado == EstadoPaquete.ANUNCIADO


def test_recibir_sin_ambiguedad_no_pide_nada_nuevo(client):
    """Sin los campos nuevos, Recibir se comporta exactamente igual que
    siempre -- el paso de resolución es opcional."""
    staff = _login_staff(client)
    p = _anunciar(client)

    r = client.post(f"/paquetes/{p.id}/recibir", data={}, follow_redirects=False)
    assert r.status_code == 303

    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.estado == EstadoPaquete.RECIBIDO
    assert p2.received_by_usuario_id == staff.id


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


def test_modal_recibir_ofrece_declarar_apartamento_si_falta(client):
    _login_staff(client)
    _anunciar(client)  # sin apartamento -- Destinatario.yo_mismo(), sin unidad
    r = client.get("/paquetes")
    assert r.status_code == 200
    assert "todavía no tiene apartamento" in r.text
    assert 'data-torre-recibir' in r.text


def test_modal_recibir_no_ofrece_declarar_apartamento_si_ya_tiene(client):
    from app.domain.apartamento_service import resolver_apartamento

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
        apartamento=apto,
    )
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert "todavía no tiene apartamento" not in r.text


def test_modal_recibir_ofrece_elegir_o_crear_residente_con_candidatos(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Jesus Villalobos", "3033333333")
    client.db.commit()

    announce(
        client.db,
        anunciante_telefono="3044444444",
        anunciante_nombre="Portero",
        destinatario=Destinatario.declarado_por_cliente("Nombre Que No Coincide"),
        apartamento=apto,
    )
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert "A nombre de quién es" in r.text
    assert "JESUS VILLALOBOS" in r.text
    assert "Es un nuevo residente de este apartamento" in r.text


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
    # nombre distinto (typo o tercero) -- `solo_nombre` (no
    # `declarado_por_cliente`: desde la conversación 2026-08-15 ese
    # constructor SOLO honra nombres de co-residentes de la misma unidad,
    # cae al propio Anunciante si no hay match -- no serviría para este
    # escenario de mismatch).
    from app.domain.persona_service import get_or_create_persona

    get_or_create_persona(client.db, "3001234567", "Ana Perez")
    client.db.commit()
    announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana Perez",
        destinatario=Destinatario.solo_nombre("Ana Peres"),
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


def test_advertencia_es_clickeable_y_abre_corregir_destinatario_en_anunciado(client):
    # Conversación 2026-08-15 (pedido explícito): el ícono de advertencia
    # debe ser clickeable y abrir el modal "Corregir destinatario" -- mismo
    # modal que el ícono "Modificar" de Acciones.
    _login_staff(client)
    from app.domain.persona_service import get_or_create_persona

    get_or_create_persona(client.db, "3001234567", "Ana Perez")
    client.db.commit()
    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana Perez",
        destinatario=Destinatario.solo_nombre("Ana Peres"),
    )
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_correct = _segmento_modal(r.text, f"modal-correct-{p.id}")
    assert modal_correct  # el modal existe (paquete ANUNCIADO)
    assert f'data-open="modal-correct-{p.id}"' in r.text


def test_advertencia_es_clickeable_en_recibido_y_entregado(client):
    # Conversación 2026-08-16 (pedido explícito): "Corregir destinatario" se
    # amplió más allá de ANUNCIADO -- el typo no siempre se nota mientras el
    # paquete sigue anunciado. `ESTADOS_CORREGIBLES` (paquete_lifecycle.py).
    staff = _login_staff(client)
    from app.domain.persona_service import get_or_create_persona

    get_or_create_persona(client.db, "3001234567", "Ana Perez")
    client.db.commit()
    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana Perez",
        destinatario=Destinatario.solo_nombre("Ana Peres"),
    )
    client.db.commit()
    dom_receive(client.db, p, staff)
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert f'data-open="modal-correct-{p.id}"' in r.text

    dom_deliver(client.db, p, staff)
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert f'data-open="modal-correct-{p.id}"' in r.text


def test_advertencia_no_es_clickeable_en_cancelado(client):
    # CANCELADO es el único estado que queda fuera de `ESTADOS_CORREGIBLES`
    # -- no tiene sentido de negocio corregir a quién le iba a llegar un
    # paquete que nunca se entregó. El modal "Corregir destinatario" ni
    # existe en el DOM ahí, así que el ícono se queda plano, sin `data-open`.
    staff = _login_staff(client)
    from app.domain.persona_service import get_or_create_persona

    get_or_create_persona(client.db, "3001234567", "Ana Perez")
    client.db.commit()
    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana Perez",
        destinatario=Destinatario.solo_nombre("Ana Peres"),
    )
    client.db.commit()
    from app.domain.paquete_lifecycle import cancel as dom_cancel

    dom_cancel(client.db, p, staff, "NO_RECLAMADO")
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert "no coincide" in r.text.lower()
    assert f'data-open="modal-correct-{p.id}"' not in r.text


def test_advertencia_no_bloquea_las_acciones_normales(client):
    _login_staff(client)
    from app.domain.persona_service import get_or_create_persona

    get_or_create_persona(client.db, "3001234567", "Ana Perez")
    client.db.commit()
    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana Perez",
        destinatario=Destinatario.solo_nombre("Ana Peres"),
    )
    client.db.commit()

    r = client.post(
        f"/paquetes/{p.id}/recibir", data={}, follow_redirects=False
    )
    assert r.status_code == 303


# --------------------------------------------------------------------------- #
# Corregir destinatario (Grupo 6, ticket 02) — solo mientras ANUNCIADO.
# --------------------------------------------------------------------------- #
def test_boton_corregir_aparece_en_anunciado_recibido_y_entregado_no_en_cancelado(client):
    # Ampliado (conversación 2026-08-16, pedido explícito del cliente):
    # `ESTADOS_CORREGIBLES` (paquete_lifecycle.py) ya no es solo ANUNCIADO.
    staff = _login_staff(client)
    anunciado = _anunciar(client, tel="3001234567", nombre="Ana")
    recibido = _anunciar(client, tel="3019999999", nombre="Beto")
    dom_receive(client.db, recibido, staff)
    entregado = _anunciar(client, tel="3029999999", nombre="Caro")
    dom_receive(client.db, entregado, staff)
    dom_deliver(client.db, entregado, staff)
    cancelado = _anunciar(client, tel="3039999999", nombre="Dan")
    from app.domain.paquete_lifecycle import cancel as dom_cancel

    dom_cancel(client.db, cancelado, staff, "NO_RECLAMADO")
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert f'modal-correct-{anunciado.id}' in r.text
    assert f'modal-correct-{recibido.id}' in r.text
    assert f'modal-correct-{entregado.id}' in r.text
    assert f'modal-correct-{cancelado.id}' not in r.text


def test_corregir_actualiza_nombre_y_quita_la_advertencia(client):
    _login_staff(client)
    from app.domain.persona_service import get_or_create_persona

    get_or_create_persona(client.db, "3001234567", "Ana Perez")
    client.db.commit()
    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana Perez",
        destinatario=Destinatario.solo_nombre("Ana Peres"),
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


def test_modal_ver_muestra_boton_corregir_solo_si_hay_advertencia(client):
    # Conversación 2026-08-16 (pedido explícito): botón "Corregir" al lado
    # del botón de siguiente estado, dentro del modal "Ver" -- solo cuando
    # hay advertencia de nombre Y el estado sigue en `ESTADOS_CORREGIBLES`.
    staff = _login_staff(client)
    from app.domain.persona_service import get_or_create_persona

    get_or_create_persona(client.db, "3001234567", "Ana Perez")
    client.db.commit()
    con_advertencia = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana Perez",
        destinatario=Destinatario.solo_nombre("Ana Peres"),
    )
    sin_advertencia = _anunciar(client, tel="3009999999", nombre="Beto")
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_con = _segmento_modal(r.text, f"modal-ver-{con_advertencia.id}")
    modal_sin = _segmento_modal(r.text, f"modal-ver-{sin_advertencia.id}")
    assert f'data-open="modal-correct-{con_advertencia.id}"' in modal_con
    assert f'data-open="modal-correct-{sin_advertencia.id}"' not in modal_sin


def test_corregir_desde_ver_regresa_al_modal_ver(client):
    # `origen=ver` (puesto por el botón "Corregir" del propio modal "Ver")
    # hace que el éxito redirija a /paquetes?ver=<id> en vez del /paquetes
    # de siempre.
    _login_staff(client)
    from app.domain.persona_service import get_or_create_persona

    get_or_create_persona(client.db, "3001234567", "Ana Perez")
    client.db.commit()
    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana Perez",
        destinatario=Destinatario.solo_nombre("Ana Peres"),
    )
    client.db.commit()

    r = client.post(
        f"/paquetes/{p.id}/corregir",
        data={"candidato_idx": "0", "origen": "ver"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/paquetes?ver={p.id}"

    r2 = client.get(r.headers["location"])
    assert r2.status_code == 200
    modal_ver = _segmento_modal(r2.text, f"modal-ver-{p.id}")
    apertura = modal_ver[: modal_ver.index(">") + 1]
    assert "hidden" not in apertura  # el div raíz del modal reabre visible


def test_corregir_sin_origen_ver_mantiene_el_redirect_de_siempre(client):
    _login_staff(client)
    p = _anunciar(client, tel="3001234567", nombre="Ana")
    client.db.commit()

    r = client.post(
        f"/paquetes/{p.id}/corregir",
        data={"candidato_idx": "0"},  # sin "origen" -- entrada de tabla/Acciones
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/paquetes"


# --------------------------------------------------------------------------- #
# Conversación 2026-08-16 — vista previa en vivo de "+ Nuevo residente"
# (GET /paquetes/nuevo-residente/identificar).
# --------------------------------------------------------------------------- #
def test_identificar_nuevo_residente_encuentra_persona_por_telefono(client):
    _login_staff(client)
    from app.domain.persona_service import get_or_create_persona

    get_or_create_persona(client.db, "3005558888", "Persona Ya Registrada")
    client.db.commit()

    r = client.get("/paquetes/nuevo-residente/identificar", params={"contacto": "3005558888"})
    assert r.status_code == 200
    assert r.json() == {"encontrado": True, "nombre": "PERSONA YA REGISTRADA"}


def test_identificar_nuevo_residente_encuentra_persona_por_whatsapp(client):
    _login_staff(client)
    from app.domain.persona_service import get_or_create_persona_por_whatsapp

    get_or_create_persona_por_whatsapp(client.db, "residente.wa", "Persona Whatsapp")
    client.db.commit()

    r = client.get("/paquetes/nuevo-residente/identificar", params={"contacto": "residente.wa"})
    assert r.status_code == 200
    assert r.json() == {"encontrado": True, "nombre": "PERSONA WHATSAPP"}


def test_identificar_nuevo_residente_sin_match_devuelve_encontrado_false(client):
    _login_staff(client)

    r = client.get("/paquetes/nuevo-residente/identificar", params={"contacto": "3009998888"})
    assert r.status_code == 200
    assert r.json() == {"encontrado": False}

    r2 = client.get("/paquetes/nuevo-residente/identificar", params={"contacto": "300999"})  # a medio teclear
    assert r2.status_code == 200
    assert r2.json() == {"encontrado": False}

    r3 = client.get("/paquetes/nuevo-residente/identificar", params={"contacto": ""})
    assert r3.status_code == 200
    assert r3.json() == {"encontrado": False}


def test_identificar_nuevo_residente_requiere_sesion_de_staff(client):
    r = client.get(
        "/paquetes/nuevo-residente/identificar",
        params={"contacto": "3005558888"},
        follow_redirects=False,
    )
    assert r.status_code == 303


# --------------------------------------------------------------------------- #
# Grupo 16 (Ronda 2) — Corregir por selección de Ocupantes conocidos.
# --------------------------------------------------------------------------- #
def test_modal_corregir_muestra_candidatos_cuando_los_hay(client):
    _login_staff(client)
    p = _anunciar(client, tel="3001234567", nombre="Ana")

    r = client.get("/paquetes")
    assert r.status_code == 200
    idx = r.text.index(f'id="modal-correct-{p.id}"')
    fin = r.text.find('<div id="modal-', idx + 1)
    modal_html = r.text[idx:fin] if fin != -1 else r.text[idx:]
    assert f'name="candidato_idx"' in modal_html
    assert 'name="recipient_name"' not in modal_html


def test_modal_corregir_candidatos_son_tarjetas_de_un_clic(client):
    # Conversación 2026-08-15 (prototipado en
    # `prototype/corregir-destinatario-candidatos`, decisión del cliente):
    # cada candidato ES el submit -- sin <select> ni botón "Guardar" aparte
    # para el caso de elegir a alguien ya conocido.
    _login_staff(client)
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Jesus Villalobos", telefono="3033333333")
    client.db.commit()
    p = announce(
        client.db,
        anunciante_telefono="3044444444",
        anunciante_nombre="Portero",
        destinatario=Destinatario.solo_nombre("Nombre Que No Coincide"),
        apartamento=apto,
    )
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_correct = _segmento_modal(r.text, f"modal-correct-{p.id}")
    assert '<select' not in modal_correct
    assert f'<button type="submit" name="candidato_idx" value="0"' in modal_correct
    assert "JESUS VILLALOBOS" in modal_correct


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
            "nuevo_ocupante_contacto": "3021112233",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    paquete = client.db.get(Paquete, p.id)
    assert paquete.recipient_name == "HIJA"
    assert paquete.recipient_phone == "+573021112233"  # el propio de Hija


def test_corregir_declara_ocupante_nuevo_con_telefono_ya_registrado_ignora_el_nombre_tecleado(client):
    # Conversación 2026-08-16 (pedido explícito del cliente): server-side,
    # no solo la vista previa en vivo -- aunque el POST traiga un nombre
    # distinto (staff que bypasea el campo readonly, o un cliente HTTP
    # directo), el nombre real registrado manda.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante
    from app.domain.persona_service import get_or_create_persona

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", telefono="3033333333")
    get_or_create_persona(client.db, "3021112233", "Nombre Real Registrado")
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
            "nuevo_ocupante_nombre": "Nombre Que Alguien Intento Colar",
            "nuevo_ocupante_contacto": "3021112233",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    paquete = client.db.get(Paquete, p.id)
    assert paquete.recipient_name == "NOMBRE REAL REGISTRADO"


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


def test_corregir_ocupante_nuevo_sin_apartamento_en_snapshot_mensaje_especifico(client):
    """.scratch/ocupante-principal-escenarios, ticket 08 -- mensaje
    distinto de "falta el nombre" cuando la causa real es que el paquete
    no tiene apartamento resuelto en su snapshot."""
    _login_staff(client)
    p = _anunciar(client)  # sin apartamento

    r = client.post(
        f"/paquetes/{p.id}/corregir",
        data={"candidato_idx": "nuevo", "nuevo_ocupante_nombre": "Hija"},
    )
    assert r.status_code == 400
    assert "no tiene apartamento resuelto" in r.text
    assert "Escribí el nombre" not in r.text


def test_corregir_declara_ocupante_nuevo_con_whatsapp(client):
    """.scratch/ocupante-principal-escenarios, ticket 08 -- input único
    autoclasificado, mismo criterio que tab Residentes/`/mis-datos`."""
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante
    from app.domain.persona import Persona

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
            "nuevo_ocupante_contacto": "hija.whats",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    nuevo = client.db.query(Ocupante).filter(
        Ocupante.apartamento_id == apto.id, Ocupante.nombre == "HIJA"
    ).one()
    assert client.db.get(Persona, nuevo.persona_id).whatsapp_usuario == "hija.whats"


def test_corregir_nuevo_ocupante_contacto_ya_ocupante_bloquea_sin_mover(client):
    """.scratch/ocupante-principal-escenarios, ticket 12 -- sin marcar la
    casilla, queda bloqueado con el mensaje que ofrece mover."""
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", telefono="3033333333")
    apto_otra = resolver_apartamento(client.db, "TORRE 2", "202")
    agregar_ocupante(client.db, apto_otra, "Hija", telefono="3021112233")
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
            "nuevo_ocupante_nombre": "Cualquiera",
            "nuevo_ocupante_contacto": "3021112233",
        },
    )
    assert r.status_code == 400
    assert "Mover acá" in r.text


def test_corregir_nuevo_ocupante_mueve_marcando_la_casilla(client):
    """El nombre tecleado se ignora -- se corrige el destinatario a la
    identidad REAL (Hija), no se crea un residente nuevo "Cualquiera"."""
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", telefono="3033333333")
    apto_otra = resolver_apartamento(client.db, "TORRE 2", "202")
    hija = agregar_ocupante(client.db, apto_otra, "Hija", telefono="3021112233")
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
            "nuevo_ocupante_nombre": "Cualquiera",
            "nuevo_ocupante_contacto": "3021112233",
            "mover_de_otra_unidad": "1",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    movida = client.db.query(Ocupante).filter(
        Ocupante.apartamento_id == apto.id, Ocupante.persona_id == hija.persona_id
    ).one()
    assert movida.nombre == "HIJA"
    assert client.db.get(Ocupante, hija.id).desvinculado_en is not None
    paquete = client.db.get(Paquete, p.id)
    assert paquete.recipient_name == "HIJA"


def test_recibir_no_ofrece_mover_aunque_el_contacto_ya_sea_ocupante(client):
    """.scratch/ocupante-principal-escenarios, ticket 12 -- "mover" nunca
    se ofrece dentro de Recibir, ni marcando la casilla a mano (no existe
    en ese form; un POST directo con el campo tampoco debe moverlo)."""
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", telefono="3033333333")
    apto_otra = resolver_apartamento(client.db, "TORRE 2", "202")
    hija = agregar_ocupante(client.db, apto_otra, "Hija", telefono="3021112233")
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
        f"/paquetes/{p.id}/recibir",
        data={
            "candidato_idx": "nuevo",
            "nuevo_ocupante_nombre": "Cualquiera",
            "nuevo_ocupante_contacto": "3021112233",
            "mover_de_otra_unidad": "1",
        },
    )
    assert r.status_code == 400  # bloqueado -- no hay camino de "mover" acá

    client.db.expire_all()
    assert client.db.get(Ocupante, hija.id).desvinculado_en is None
    assert client.db.get(Ocupante, hija.id).apartamento_id == apto_otra.id


def _anunciar_con_mismatch(client, tel="3001234567", registrado="Ana Perez"):
    # Mismo patrón que test_corregir_actualiza_nombre_y_quita_la_advertencia:
    # sin Apartamento, el único candidato es el propio Anunciante (índice 0)
    # -- pero con su nombre REGISTRADO, distinto del declarado al anunciar,
    # así que seleccionarlo SÍ representa una corrección real.
    from app.domain.persona_service import get_or_create_persona

    get_or_create_persona(client.db, tel, registrado)
    client.db.commit()
    p = announce(
        client.db,
        anunciante_telefono=tel,
        anunciante_nombre=registrado,
        destinatario=Destinatario.solo_nombre(registrado[:-1] + "x"),  # typo deliberado
    )
    client.db.commit()
    return p


def test_corregir_un_recibido_y_un_entregado_se_permite(client):
    # Ampliado (conversación 2026-08-16, pedido explícito del cliente):
    # `ESTADOS_CORREGIBLES` ya no es solo ANUNCIADO.
    staff = _login_staff(client)
    p = _anunciar_con_mismatch(client, registrado="Ana Perez")
    dom_receive(client.db, p, staff)
    client.db.commit()

    r = client.post(
        f"/paquetes/{p.id}/corregir", data={"candidato_idx": "0"}, follow_redirects=False
    )
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.get(Paquete, p.id).recipient_name == "ANA PEREZ"

    p2 = _anunciar_con_mismatch(client, tel="3009999999", registrado="Beto Ruiz")
    dom_receive(client.db, p2, staff)
    dom_deliver(client.db, p2, staff)
    client.db.commit()

    r = client.post(
        f"/paquetes/{p2.id}/corregir", data={"candidato_idx": "0"}, follow_redirects=False
    )
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.get(Paquete, p2.id).recipient_name == "BETO RUIZ"


def test_corregir_un_cancelado_se_rechaza_sin_efecto(client):
    staff = _login_staff(client)
    p = _anunciar_con_mismatch(client, registrado="Ana Perez")
    from app.domain.paquete_lifecycle import cancel as dom_cancel

    dom_cancel(client.db, p, staff, "NO_RECLAMADO")
    client.db.commit()
    nombre_original = p.recipient_name

    r = client.post(f"/paquetes/{p.id}/corregir", data={"candidato_idx": "0"})
    assert r.status_code == 400

    client.db.expire_all()
    assert client.db.get(Paquete, p.id).recipient_name == nombre_original


def test_corregir_nuevo_ocupante_no_deja_huerfano_si_falla_despues(client):
    """.scratch/ocupante-principal-escenarios, ticket 09 -- si el Ocupante
    nuevo se creó pero corregir_destinatario falla después (carrera real:
    el paquete cambió de estado desde que se abrió la página), el Ocupante
    NO debe quedar persistido."""
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante

    staff = _login_staff(client)
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
    from app.domain.paquete_lifecycle import cancel as dom_cancel

    dom_cancel(client.db, p, staff, "NO_RECLAMADO")  # fuera de ESTADOS_CORREGIBLES -- fuerza la carrera
    client.db.commit()

    r = client.post(
        f"/paquetes/{p.id}/corregir",
        data={"candidato_idx": "nuevo", "nuevo_ocupante_nombre": "Huerfano"},
    )
    assert r.status_code == 400

    client.db.expire_all()
    existe = (
        client.db.query(Ocupante)
        .filter(Ocupante.apartamento_id == apto.id, Ocupante.nombre == "HUERFANO")
        .first()
    )
    assert existe is None


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
        destinatario=Destinatario.solo_nombre("Un Vecino"),
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


def test_paginacion_con_mas_de_10_paquetes(client):
    # _POR_PAGINA = 10 (ganador del prototipo de tabla, conversación
    # 2026-08-13) -- 25 paquetes caen en 3 páginas: 24..15 / 14..5 / 4..0.
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

    r2 = client.get("/paquetes", params={"pagina": 3})
    assert r2.status_code == 200
    assert "CLIENTE0" in r2.text  # el más viejo, cae en la última página


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


def test_historial_del_modal_atribuye_cada_actor_a_su_propio_hito(client):
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
    # issue 79: la lista ya no muestra el actor en la fila -- vive en el
    # modal "Ver" de ese paquete (`_segmento_modal`, definida más abajo).
    # Conversación 2026-08-15: el modal ahora muestra el HISTORIAL completo
    # (todos los hitos, no solo el último), así que los dos actores aparecen
    # -- lo que importa es que cada uno quede atribuido a SU PROPIO hito
    # (Recibió/Canceló), no mezclado con el del otro.
    modal_ver = _segmento_modal(r.text, f"modal-ver-{p2.id}")
    # Ancla en "Historial" -- el badge de estado ACTUAL en el encabezado del
    # modal también dice "Cancelado", así que buscar ">Cancelado<" desde el
    # inicio del modal encontraría ese badge, no el hito del timeline.
    historial = modal_ver[modal_ver.index("Historial"):]
    idx_recibido = historial.index(">Recibido<")
    idx_cancelado = historial.index(">Cancelado<")
    segmento_recibido = historial[idx_recibido:idx_cancelado]
    segmento_cancelado = historial[idx_cancelado:]
    assert staff_recibe.nombre in segmento_recibido
    assert staff_cancela.nombre not in segmento_recibido
    assert staff_cancela.nombre in segmento_cancelado
    assert staff_recibe.nombre not in segmento_cancelado


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


# --------------------------------------------------------------------------- #
# Issue 79 — columnas renombradas (Cliente/Dirección/Fecha) + Acciones
# ampliada (Whatsapp/Teléfono/Email/Ver/Modificar/Acción/Cancelar/Eliminar).
# --------------------------------------------------------------------------- #
def test_encabezados_de_columna_nuevos(client):
    _login_staff(client)
    _anunciar(client, nombre="Ana")
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    for encabezado in ("Estado", "Cliente", "Dirección", "Fecha", "Acciones"):
        assert f">{encabezado}<" in r.text
    # "Guía" y "Última acción" ya no son columnas propias (como <th>).
    assert ">Guía<" not in r.text
    assert ">Última acción<" not in r.text


def test_icono_email_en_acciones_usa_el_email_del_anunciante(client):
    # Antes quedaba SIEMPRE apagado (bug reportado en vivo, conversación
    # 2026-08-15) -- ahora usa `p.persona_anunciante.email`.
    from app.domain.persona import Persona

    _login_staff(client)
    p = _anunciar(client, tel="3001234567", nombre="Ana")
    persona = client.db.query(Persona).filter(Persona.telefono == "+573001234567").one()
    persona.email = "ana@example.com"
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert 'href="mailto:ana@example.com"' in r.text


def test_icono_email_apagado_sin_email_del_anunciante(client):
    _login_staff(client)
    _anunciar(client, tel="3001234567", nombre="Ana")
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert "mailto:" not in r.text


def test_direccion_no_duplica_la_palabra_torre(client):
    from app.domain.apartamento_service import resolver_apartamento

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 10", "101")
    client.db.commit()
    announce(client.db, "3001234567", "Ana", Destinatario.yo_mismo(), apartamento=apto)
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert "Torre 10 · Apt 101" in r.text
    assert "Torre TORRE 10" not in r.text


def test_fecha_columna_refleja_el_ultimo_cambio_de_estado(client):
    staff = _login_staff(client)
    p = _anunciar(client, nombre="Ana")
    _recibir(client, staff, p)
    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)

    r = client.get("/paquetes")
    assert r.status_code == 200
    # La fecha mostrada es la de received_at, no la de announced_at -- en
    # hora de Bogotá/Lima/Quito (`hora_local`, `templating.py`), NO en UTC
    # crudo (cerca de medianoche UTC el día puede diferir).
    from app.web.templating import hora_local

    assert hora_local(p2.received_at).strftime("%d/%m") in r.text


def test_columna_cliente_abre_el_modal_ver(client):
    # issue 80: el ícono "Ver" propio de Acciones se quitó (redundante) --
    # la columna Cliente queda como ÚNICO disparador del modal.
    _login_staff(client)
    p = _anunciar(client, nombre="Ana")
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert r.text.count(f'data-open="modal-ver-{p.id}"') == 1
    assert f'id="modal-ver-{p.id}"' in r.text


def _segmento_modal(texto, modal_id):
    """El HTML de UN modal, desde su `<div id="<modal_id>"` hasta el
    siguiente `<div id="modal-...` (el próximo modal, cualquiera que sea) o
    el final del documento -- más robusto que un ancho fijo en caracteres,
    que se desincroniza cada vez que cambia cuánto markup tiene el modal por
    dentro (ver issue 79/80). Ancla en `<div id="` (el wrapper del modal),
    NO en `id="` a secas -- el `<h2 id="modal-...-titulo">` de adentro
    también empieza con `id="modal-` y cortaría el segmento de inmediato."""
    inicio = texto.index(f'<div id="{modal_id}"')
    resto = texto[inicio:]
    fin = resto.find('<div id="modal-', 1)
    return resto if fin == -1 else resto[:fin]


def test_modal_ver_ya_no_tiene_seccion_anunciado_por(client):
    # Conversación 2026-08-15 (pedido explícito): la sección "Anunciado por"
    # se remueve del modal -- esa información (quién anunció) queda en el
    # Historial, en el hito "Anunciado" (fila "Anunció").
    _login_staff(client)
    p = _anunciar(client, tel="3001234567", nombre="Ana")
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_ver = _segmento_modal(r.text, f"modal-ver-{p.id}")
    assert "Anunciado por" not in modal_ver
    assert "Anunció" in modal_ver
    assert "ANA" in modal_ver


def test_modal_ver_telefono_debajo_del_titulo_es_el_propio_si_lo_tiene(client):
    # Conversación 2026-08-16 (pedido explícito): el teléfono de contacto se
    # movió de la sección "Destinatario" (retirada) a una línea justo debajo
    # del título del modal -- si el destinatario tiene teléfono propio, es
    # ese.
    _login_staff(client)
    p = _anunciar(client, tel="3001234567", nombre="Ana")
    client.db.commit()

    r = client.get("/paquetes")
    modal_ver = _segmento_modal(r.text, f"modal-ver-{p.id}")
    idx_titulo = modal_ver.index(f'modal-ver-{p.id}-titulo')
    idx_telefono = modal_ver.index("+573001234567")
    assert idx_telefono > idx_titulo  # debajo del título, no es casualidad de orden
    assert "Destinatario" not in modal_ver  # la sección vieja ya no existe


def test_modal_ver_telefono_cae_al_telefono_del_anunciante_sin_telefono_propio(client):
    # Sin teléfono propio del destinatario (`Destinatario.solo_nombre`), la
    # línea cae al Anunciante -- mismo fallback que usa el envío real de SMS
    # (`resolver_destino_notificable`).
    _login_staff(client)
    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.solo_nombre("Invitado Sin Telefono"),
    )
    client.db.commit()
    assert p.recipient_phone is None

    r = client.get("/paquetes")
    modal_ver = _segmento_modal(r.text, f"modal-ver-{p.id}")
    assert "+573001234567" in modal_ver


def test_modal_ver_telefono_cae_al_whatsapp_del_anunciante_sin_telefono(client):
    # Anunciante solo-WhatsApp (sin teléfono): la línea cae a su WhatsApp --
    # nunca queda vacía (`announce()` exige uno de los dos).
    _login_staff(client)
    p = announce(
        client.db,
        anunciante_whatsapp="ana.whats",
        anunciante_nombre="Ana",
        destinatario=Destinatario.solo_nombre("Invitado Sin Telefono"),
    )
    client.db.commit()
    assert p.recipient_phone is None

    r = client.get("/paquetes")
    modal_ver = _segmento_modal(r.text, f"modal-ver-{p.id}")
    assert "ana.whats" in modal_ver


def test_modal_ver_muestra_residentes_de_la_unidad(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 10", "101")
    agregar_ocupante(client.db, apto, "Otro Residente", telefono="3009876543")
    client.db.commit()
    p = announce(client.db, "3001234567", "Ana", Destinatario.yo_mismo(), apartamento=apto)
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_ver = _segmento_modal(r.text, f"modal-ver-{p.id}")
    assert "Residentes de la unidad" in modal_ver
    assert "OTRO RESIDENTE" in modal_ver


def test_modal_ver_residentes_icono_de_email_solo_si_existe(client):
    # Conversación 2026-08-15 (pedido explícito): agregar ícono de Email a
    # "Residentes de la unidad", mismo criterio que WhatsApp/Teléfono --
    # solo aparece para quien SÍ tiene el dato.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante
    from app.domain.persona_service import update_datos_personales
    from app.domain.persona import Persona

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 10", "101")
    con_email = agregar_ocupante(client.db, apto, "Con Email", telefono="3009876543")
    agregar_ocupante(client.db, apto, "Sin Email", telefono="3001112222")
    persona_con_email = client.db.get(Persona, con_email.persona_id)
    update_datos_personales(client.db, persona_con_email, email="con.email@club.com")
    client.db.commit()
    p = announce(client.db, "3005556666", "Ana", Destinatario.yo_mismo(), apartamento=apto)
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_ver = _segmento_modal(r.text, f"modal-ver-{p.id}")
    # Único mailto: del modal -- la sección "Anunciado por" (que sí tenía
    # mailto:) se removió en esta misma conversación, y "Destinatario" nunca
    # mostró email.
    assert modal_ver.count("mailto:") == 1
    assert "mailto:con.email@club.com" in modal_ver


def test_eliminar_solo_visible_para_admin_en_anunciado(client):
    from app.domain.staff_service import create_staff
    from app.domain.usuario import RolUsuario

    admin = _login_staff(client)  # create_initial_admin -> ADMIN
    p = _anunciar(client, nombre="Ana")
    client.db.commit()

    r = client.get("/paquetes")
    assert f'data-open="modal-eliminar-{p.id}"' in r.text

    operador = create_staff(client.db, admin, "op@club.com", "Operador", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/salir")
    client.post("/ingresar", data={"email": "op@club.com", "password": _PW})
    r2 = client.get("/paquetes")
    assert f'data-open="modal-eliminar-{p.id}"' not in r2.text


def test_eliminar_admin_borra_un_paquete_anunciado(client):
    admin = _login_staff(client)
    p = _anunciar(client, nombre="Ana")
    client.db.commit()
    pid = p.id

    r = client.post(f"/paquetes/{pid}/eliminar", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/paquetes"
    client.db.expire_all()
    assert client.db.get(Paquete, pid) is None


def test_eliminar_rechaza_un_paquete_ya_recibido(client):
    staff = _login_staff(client)
    p = _anunciar(client, nombre="Ana")
    _recibir(client, staff, p)
    pid = p.id

    r = client.post(f"/paquetes/{pid}/eliminar")
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.get(Paquete, pid) is not None


def test_eliminar_sin_ser_admin_da_403(client):
    from app.domain.staff_service import create_staff
    from app.domain.usuario import RolUsuario

    admin = _login_staff(client)
    p = _anunciar(client, nombre="Ana")
    client.db.commit()
    pid = p.id

    create_staff(client.db, admin, "op@club.com", "Operador", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/salir")
    client.post("/ingresar", data={"email": "op@club.com", "password": _PW})

    r = client.post(f"/paquetes/{pid}/eliminar")
    assert r.status_code == 403
    client.db.expire_all()
    assert client.db.get(Paquete, pid) is not None


# --------------------------------------------------------------------------- #
# "Asignar apartamento" (conversación 2026-08-14) -- ícono + modal
# independientes para corregir_apartamento (excepción ADR-0001, solo ANUNCIADO).
# --------------------------------------------------------------------------- #
def test_modal_asignar_apartamento_es_flujo_guiado_de_3_pasos(client):
    # Conversación 2026-08-15 -- 2 rondas del campo de búsqueda libre
    # (`prototype/asignar-apartamento-buscar`) seguían confundiendo Torre
    # con Apartamento; pedido explícito del cliente: escribir SOLO el
    # número de Apartamento, elegir la Torre de una lista de tarjetas, ver
    # residentes/Libre, confirmar -- sin <select> de Torre/Apartamento en
    # cascada tampoco.
    _login_staff(client)
    p = _anunciar(client, nombre="Ana")  # sin unidad

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_asignar = _segmento_modal(r.text, f"modal-asignar-apto-{p.id}")
    assert f'id="asignar-apto-input-{p.id}"' in modal_asignar
    assert f'id="asignar-torres-posibles-{p.id}"' in modal_asignar
    assert f'id="asignar-resumen-{p.id}"' in modal_asignar
    assert "<select" not in modal_asignar
    assert 'name="torre"' in modal_asignar
    assert 'name="apartamento"' in modal_asignar


def test_modal_asignar_apartamento_expone_residentes_por_unidad(client):
    # Conversación 2026-08-15 (pedido explícito): al buscar una unidad,
    # debe verse si está libre o ya tiene residentes -- para no asociar
    # por error a alguien con la familia equivocada.
    import json
    import re

    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Jesus Villalobos", telefono="3033333333")
    client.db.commit()
    p = _anunciar(client, nombre="Ana")  # sin unidad

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_asignar = _segmento_modal(r.text, f"modal-asignar-apto-{p.id}")
    match = re.search(
        rf'id="residentes-unidad-asignar-{p.id}">(.*?)</script>', modal_asignar, re.S
    )
    assert match, "no se encontró el script de residentes por unidad"
    residentes = json.loads(match.group(1))
    assert residentes["TORRE 1"]["101"] == ["JESUS VILLALOBOS"]
    # Torre 1/102 nunca tuvo Ocupante -- está libre, por eso ausente del dict.
    assert "102" not in residentes.get("TORRE 1", {})


def test_icono_asignar_apartamento_solo_en_anunciado_sin_unidad(client):
    staff = _login_staff(client)
    anunciado = _anunciar(client, tel="3001234567", nombre="Ana")  # sin unidad
    recibido = _anunciar(client, tel="3019999999", nombre="Beto")  # sin unidad
    dom_receive(client.db, recibido, staff)
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert f'data-open="modal-asignar-apto-{anunciado.id}"' in r.text
    assert f'data-open="modal-asignar-apto-{recibido.id}"' not in r.text
    # El estado RECIBIDO sin unidad se queda con el texto de siempre (nada que ofrecer).
    assert "SIN APARTAMENTO" in r.text.upper()


def test_asignar_apartamento_exitoso(client):
    from app.domain.apartamento_service import resolver_apartamento

    staff = _login_staff(client)
    resolver_apartamento(client.db, "TORRE 5", "501")
    client.db.commit()
    p = _anunciar(client, nombre="Ana")
    assert p.snapshot_apartamento is None

    r = client.post(
        f"/paquetes/{p.id}/asignar-apartamento",
        data={"torre": "TORRE 5", "apartamento": "501"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.snapshot_torre == "TORRE 5"
    assert p2.snapshot_apartamento == "501"
    assert p2.corrected_by_usuario_id == staff.id


def test_asignar_apartamento_rechaza_si_ya_no_esta_anunciado(client):
    from app.domain.apartamento_service import resolver_apartamento

    staff = _login_staff(client)
    resolver_apartamento(client.db, "TORRE 5", "501")
    client.db.commit()
    p = _anunciar(client, nombre="Ana")
    _recibir(client, staff, p)

    r = client.post(
        f"/paquetes/{p.id}/asignar-apartamento",
        data={"torre": "TORRE 5", "apartamento": "501"},
    )
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.get(Paquete, p.id).snapshot_apartamento is None


def test_asignar_apartamento_sin_datos_no_hace_nada(client):
    _login_staff(client)
    p = _anunciar(client, nombre="Ana")

    r = client.post(f"/paquetes/{p.id}/asignar-apartamento", data={})
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.get(Paquete, p.id).snapshot_apartamento is None


def test_asignar_apartamento_terna_inexistente_no_hace_nada(client):
    _login_staff(client)
    p = _anunciar(client, nombre="Ana")

    r = client.post(
        f"/paquetes/{p.id}/asignar-apartamento",
        data={"torre": "TORRE FANTASMA", "apartamento": "999"},
    )
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.get(Paquete, p.id).snapshot_apartamento is None
