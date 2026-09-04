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


def test_recibir_declara_apartamento_autocompleta_al_anunciante_como_residente(client):
    # Issue 189 (ronda 5, pedido explícito -- flujo /announce "anunciar +
    # recibir" en un solo paso): declarar Torre+Apartamento dentro de
    # Recibir "para mí mismo" (yo_mismo), en una unidad que YA tiene
    # residentes reales (Angélica), ya NO bloquea ni exige re-teclear un
    # nombre/teléfono que YA se capturaron al anunciar -- se autocompleta
    # "+ Nuevo residente" con la identidad del propio Anunciante y termina
    # de recibir en el mismo envío. Reemplaza el bloqueo de la ronda 4 para
    # este caso puntual (yo mismo): el sistema YA sabe quién es, no hace
    # falta preguntarle a un segundo modal.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Angelica Arrazola", "3001112233")
    client.db.commit()
    p = _anunciar(client, tel="3007778888", nombre="Fantasma")  # sin apartamento

    r = client.post(
        f"/paquetes/{p.id}/recibir",
        data={"torre": "TORRE 1", "apartamento": "101", "guide_number": "1Z-OK"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/paquetes"

    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.estado == EstadoPaquete.RECIBIDO
    assert p2.guide_number == "1Z-OK"
    assert p2.snapshot_apartamento == "101"
    assert p2.recipient_name == "FANTASMA"

    nuevo = (
        client.db.query(Ocupante)
        .filter(Ocupante.apartamento_id == apto.id, Ocupante.nombre == "FANTASMA")
        .one()
    )
    assert nuevo.persona_id is not None
    assert nuevo.desvinculado_en is None


def test_recibir_declara_apartamento_en_unidad_vacia_autocompleta_tambien(client):
    # Guard (issue 189 ronda 5): el autocompletado aplica IGUAL en una
    # unidad genuinamente vacía -- antes ni bloqueaba ni registraba a nadie
    # (recibía "para mí mismo" sin dejar ningún Ocupante real detrás, así
    # que /residentes nunca reflejaba esto); ahora Ana SÍ queda registrada
    # como residente real, en ambos casos (con y sin residentes previos) de
    # forma consistente.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")  # existe, sin Ocupantes
    p = _anunciar(client, nombre="Ana")  # sin apartamento

    r = client.post(
        f"/paquetes/{p.id}/recibir",
        data={"torre": "TORRE 1", "apartamento": "101"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/paquetes"

    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.estado == EstadoPaquete.RECIBIDO
    assert p2.snapshot_apartamento == "101"

    nuevo = (
        client.db.query(Ocupante)
        .filter(Ocupante.apartamento_id == apto.id, Ocupante.nombre == "ANA")
        .one()
    )
    assert nuevo.es_principal is True  # primera de la unidad -> se promueve


def test_recibir_declara_apartamento_sin_ser_yo_mismo_sigue_bloqueando(client):
    # Issue 189 (ronda 5): el autocompletado SOLO aplica a "para mí mismo"
    # -- sin ese dato (destinatario declarado como un tercero, sin
    # coincidir con el Anunciante), el bloqueo de la ronda 4 sigue vigente:
    # no hay ninguna identidad propia con la que autocompletar, así que
    # sigue haciendo falta que el staff elija o registre a alguien.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Angelica Arrazola", "3001112233")
    client.db.commit()
    p = announce(
        client.db,
        anunciante_telefono="3044444444",
        anunciante_nombre="Portero",
        destinatario=Destinatario.solo_nombre("Alguien Random"),
    )
    client.db.commit()

    r = client.post(
        f"/paquetes/{p.id}/recibir",
        data={"torre": "TORRE 1", "apartamento": "101"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/paquetes?recibir={p.id}&aviso=recepcion_pendiente"

    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.estado == EstadoPaquete.ANUNCIADO
    assert p2.snapshot_apartamento == "101"

    # Segundo envío eligiendo a Angélica sí completa la recepción.
    r2 = client.post(
        f"/paquetes/{p.id}/recibir",
        data={"candidato_idx": "0"},
        follow_redirects=False,
    )
    assert r2.status_code == 303
    assert r2.headers["location"] == "/paquetes"

    client.db.expire_all()
    p3 = client.db.get(Paquete, p.id)
    assert p3.estado == EstadoPaquete.RECIBIDO
    assert p3.recipient_name == "ANGELICA ARRAZOLA"


def test_recibir_declara_apartamento_con_nuevo_residente_no_redirige_a_corregir(client):
    # Guard: cuando SÍ se resolvió a alguien (candidato o nuevo residente)
    # en el mismo envío, la asociación real ya quedó completa -- sigue
    # redirigiendo a `/paquetes` (o `/consultar`) como siempre.
    from app.domain.apartamento_service import resolver_apartamento

    _login_staff(client)
    resolver_apartamento(client.db, "TORRE 1", "101")
    client.db.commit()
    p = _anunciar(client, nombre="Ana")

    r = client.post(
        f"/paquetes/{p.id}/recibir",
        data={
            "torre": "TORRE 1",
            "apartamento": "101",
            "candidato_idx": "nuevo",
            "nuevo_ocupante_nombre": "Lais Hernandez",
            "nuevo_ocupante_contacto": "3009998877",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/paquetes"


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


def test_recibir_declara_unidad_nueva_y_registra_residente_en_un_solo_envio(client):
    """Issue 148 (.scratch/pendientes-cliente) -- antes había que declarar la
    unidad primero (POST 1, `torre`/`apartamento`) y recién en una VISITA
    POSTERIOR a "Corregir destinatario" resolver quién vive ahí (POST 2,
    `candidato_idx`/`nuevo_ocupante_nombre`), porque `modal_recibir` escondía
    la sección "¿A nombre de quién es?" entera mientras el paquete no tuviera
    apartamento -- el staff podía terminar con un paquete Recibido con
    dirección pero NINGÚN Ocupante creado. Ahora "Nuevo residente" (la única
    opción segura sin candidatos numerados pre-declaración, ver el
    componente) va en el MISMO POST que declara la unidad."""
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante

    _login_staff(client)
    resolver_apartamento(client.db, "TORRE 10", "302")  # asegura que existe en el catálogo
    p = _anunciar(client, nombre="Jesus Maria Villalobos")
    assert p.snapshot_apartamento is None

    r = client.post(
        f"/paquetes/{p.id}/recibir",
        data={
            "torre": "TORRE 10",
            "apartamento": "302",
            "candidato_idx": "nuevo",
            "nuevo_ocupante_nombre": "Jesus Maria Villalobos",
            # Primer Ocupante de la unidad -- `agregar_ocupante` exige
            # Teléfono o WhatsApp para el primero (lo necesita para poder
            # quedar como principal al confirmarse más adelante).
            "nuevo_ocupante_contacto": "3005551234",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.estado == EstadoPaquete.RECIBIDO
    assert p2.snapshot_torre == "TORRE 10"
    assert p2.snapshot_apartamento == "302"

    apto = resolver_apartamento(client.db, "TORRE 10", "302")
    ocupante = (
        client.db.query(Ocupante)
        .filter(Ocupante.apartamento_id == apto.id, Ocupante.nombre == "JESUS MARIA VILLALOBOS")
        .one()
    )
    # `agregar_ocupante` crea pending, pero `receive()` (paquete_lifecycle)
    # ya dispara `promover_al_recibir` (ticket 04) DESPUÉS de la transición
    # -- resuelve el Ocupante destinatario del paquete recién Recibido y,
    # si su unidad no tiene principal todavía, lo promueve (y confirma) en
    # el mismo acto. Esta pieza ya existía; lo único que faltaba era que el
    # Ocupante se creara -- por eso alcanza con haber destrabado el paso 2.
    assert ocupante.es_principal is True
    assert ocupante.confirmado_en is not None


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
    # El catálogo hoy solo tiene "Otro" (`.scratch/motivos-cancelacion-
    # catalogo`, reducido de 4 a 1 motivo genérico en vivo el 2026-09-03).
    staff = _login_staff(client)
    p = _anunciar(client)

    r = client.post(
        f"/paquetes/{p.id}/cancelar",
        data={"motivo": "Otro"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.estado == EstadoPaquete.CANCELADO
    assert p2.cancelled_by_usuario_id == staff.id
    assert p2.cancel_reason == "Otro"


def test_cancelar_desde_recibido(client):
    staff = _login_staff(client)
    p = _anunciar(client)
    _recibir(client, staff, p)

    r = client.post(
        f"/paquetes/{p.id}/cancelar",
        data={"motivo": "Otro"},
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

    r = client.post(f"/paquetes/{p.id}/cancelar", data={"motivo": "Otro"})
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.get(Paquete, p.id).estado == EstadoPaquete.ENTREGADO


def test_cancelar_otro_con_texto_libre_guarda_el_texto_como_motivo(client):
    # Conversación 2026-08-17, pedido explícito: "Otro" revela un input de
    # texto libre -- lo tecleado ahí (no el literal "Otro") es lo que queda
    # en `cancel_reason`.
    staff = _login_staff(client)
    p = _anunciar(client)

    r = client.post(
        f"/paquetes/{p.id}/cancelar",
        data={"motivo": "Otro", "motivo_otro": "  Cliente canceló por WhatsApp  "},
        follow_redirects=False,
    )
    assert r.status_code == 303
    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.estado == EstadoPaquete.CANCELADO
    assert p2.cancel_reason == "Cliente canceló por WhatsApp"  # recortado


def test_cancelar_otro_sin_texto_libre_guarda_otro_como_fallback(client):
    _login_staff(client)
    p = _anunciar(client)

    r = client.post(
        f"/paquetes/{p.id}/cancelar",
        data={"motivo": "Otro", "motivo_otro": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    client.db.expire_all()
    assert client.db.get(Paquete, p.id).cancel_reason == "Otro"


def test_cancelar_sin_sesion_redirige_a_login(client):
    p = _anunciar(client)
    r = client.post(
        f"/paquetes/{p.id}/cancelar", data={"motivo": "Otro"}, follow_redirects=False
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


def test_modal_recibir_textos_compactados_y_camara_habilitada(client):
    # Conversación 2026-08-17, pedido explícito: etiquetas de Guía/Fotos
    # quitadas (pasan a placeholder o desaparecen), botón "Confirmar
    # recibo" -> "Recibir", cámara móvil habilitada para las fotos.
    _login_staff(client)
    p = _anunciar(client)
    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_recibir = _segmento_modal(r.text, f"modal-receive-{p.id}")
    assert 'placeholder="Guía"' in modal_recibir
    assert "Guía del transportador (opcional)</label>" not in modal_recibir
    assert "Fotos (opcional, hasta 3 ángulos)" not in modal_recibir
    assert 'capture="environment"' in modal_recibir
    assert ">Recibir</button>" in modal_recibir
    assert "Confirmar recibo" not in modal_recibir


def test_modal_recibir_ofrece_declarar_apartamento_si_falta(client):
    # Mismo picker número->Torre que "Asignar apartamento" (conversación
    # 2026-08-17, unificado vía `components/_picker_apartamento.html`) --
    # ya no es una cascada de <select>. Sin párrafo explicativo (mismo
    # día, pedido explícito posterior): el placeholder del input ya dice
    # "Apartamento".
    _login_staff(client)
    p = _anunciar(client)  # sin apartamento -- Destinatario.yo_mismo(), sin unidad
    r = client.get("/paquetes")
    assert r.status_code == 200
    assert "todavía no tiene apartamento" not in r.text
    assert f'id="picker-apto-input-recibir-{p.id}"' in r.text
    modal_recibir = _segmento_modal(r.text, f"modal-receive-{p.id}")
    assert 'placeholder="Apartamento (ej. 302)"' in modal_recibir
    # La cascada vieja Torre-><select> de Apartamento ya no existe.
    assert 'id="recibir-torre-' not in modal_recibir
    assert 'id="recibir-apto-' not in modal_recibir
    # "¿A nombre de quién es?" NO se ofrece sin apartamento resuelto
    # (conversación 2026-08-17, pedido explícito): `candidatos_correccion`
    # siempre trae al menos al Anunciante, así que sin este guard la
    # sección aparecía como si "perteneciera" a una unidad inexistente --
    # y su índice podía desalinearse contra la unidad recién declarada en
    # el picker de arriba, en el mismo envío.
    assert "A nombre de quién es" not in modal_recibir


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
    # Con apartamento ya resuelto, las tarjetas de candidato siguen
    # ofreciéndose (issue 117: sin etiqueta ni <select>, tarjetas de un
    # clic) -- este guard es solo para el caso sin unidad.
    assert 'name="candidato_idx"' in r.text
    assert "Nuevo residente" in r.text


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
    assert "JESUS VILLALOBOS" in r.text
    assert "Nuevo residente" in r.text


def test_modal_recibir_candidatos_no_muestran_badge_de_estado_de_ocupante(client):
    # Retroalimentación en vivo 2026-08-18 (revierte el alcance de la
    # conversación 2026-08-17 solo para Recibir -- Corregir destinatario
    # conserva el badge, ver test_modal_corregir_candidatos_muestran_badge_
    # de_estado_de_ocupante): "que se remueba esta badge o etiqueta, que
    # solo aparezca el nombre del residente y siga teniendo la
    # funcionalidad de poder seleccionar cualquiera de estos".
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    carlos = agregar_ocupante(client.db, apto, "Carlos", telefono="3011110001")
    sofia = agregar_ocupante(client.db, apto, "Sofia", telefono="3011110002")
    agregar_ocupante(client.db, apto, "Pedro", telefono="3011110003")  # queda pendiente
    client.db.commit()
    confirmar_ocupante(client.db, carlos, staff)  # sin principal todavía -> lo promueve
    confirmar_ocupante(client.db, sofia, staff)  # ya hay principal -> se queda confirmado
    client.db.commit()

    p = announce(
        client.db,
        anunciante_telefono="3099999999",
        anunciante_nombre="Portero",
        destinatario=Destinatario.solo_nombre("Alguien Mas"),
        apartamento=apto,
    )
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_recibir = _segmento_modal(r.text, f"modal-receive-{p.id}")

    # Los nombres siguen ahí y la selección (radio real, aunque oculto tras
    # `sr-only`) sigue funcionando -- solo el badge de texto desaparece. La
    # ventana angosta tras cada nombre (no todo el modal) evita falsos
    # positivos: el JS de "+ Nuevo residente" (issue 123) más abajo en el
    # mismo modal SÍ dice "Residente Principal de..." como parte de su
    # aviso de conflicto, sin relación con el badge que se quita acá.
    idx_carlos = modal_recibir.index("CARLOS")
    idx_sofia = modal_recibir.index("SOFIA")
    idx_pedro = modal_recibir.index("PEDRO")
    assert "Principal" not in modal_recibir[idx_carlos : idx_carlos + 60]
    assert "Confirmado" not in modal_recibir[idx_sofia : idx_sofia + 60]
    assert "Pendiente" not in modal_recibir[idx_pedro : idx_pedro + 60]
    assert modal_recibir.count('name="candidato_idx"') == 5  # Carlos/Sofia/Pedro/Anunciante + "Nuevo residente"


def test_modal_recibir_candidato_actual_tiene_fondo_pero_ningun_radio_marcado(client):
    # Conversación 2026-08-17, pedido explícito: mostrar cuál candidato es
    # el actual "solo para saber cual esta seleccionado" -- puramente
    # informativo, con fondo de color (no badge de texto -- pedido
    # explícito posterior, mismo criterio que Estado/duración). Ningún
    # radio debe llevar `checked`: si el actual quedara pre-marcado,
    # enviar "Recibir" sin tocar nada mandaría igual `candidato_idx`,
    # disparando `corregir_destinatario` de nuevo (apaga la advertencia
    # de nombre, issue 102) sin que hubiera corrección real. Sin
    # teléfono en las tarjetas (pedido explícito posterior).
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Jesus Villalobos", "3033333333")
    client.db.commit()
    # yo_mismo(): recipient_name/telefono == los del Anunciante -- el
    # Anunciante es uno de los candidatos, así que SÍ hay match.
    p = announce(
        client.db,
        anunciante_telefono="3055555555",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
        apartamento=apto,
    )
    client.db.commit()
    assert p.recipient_name == "ANA"

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_recibir = _segmento_modal(r.text, f"modal-receive-{p.id}")
    assert "JESUS VILLALOBOS" in modal_recibir
    assert "ANA" in modal_recibir
    assert "+573055555555" not in modal_recibir  # sin teléfono en las tarjetas
    assert "+573033333333" not in modal_recibir

    import re

    idx_ana = modal_recibir.index("ANA")
    fragmento_ana = modal_recibir[max(0, idx_ana - 400) : idx_ana]
    assert "bg-slate-100" in fragmento_ana  # fondo del candidato actual

    # Ningún radio de candidato queda pre-marcado (a diferencia de Tipo/
    # Condición, que sí llevan un default -- eso es aparte, no se toca acá).
    radios_candidato = re.findall(r'<input type="radio" name="candidato_idx"[^>]*>', modal_recibir)
    assert radios_candidato, "no se encontraron radios de candidato"
    assert all("checked" not in radio for radio in radios_candidato)


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
    assert ">Entregar</button>" in modal_html  # el resto del modal sigue ahí


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


def test_advertencia_es_clickeable_en_recibido_no_en_entregado(client):
    # `ESTADOS_CORREGIBLES` incluyó ENTREGADO un rato (2026-08-16), se
    # retiró al día siguiente (2026-08-17, pedido explícito: "en caso que
    # ya el paquete esté en estado Entregado o Cancelado no aparezca el
    # botón" -- confirmado que se ve mejor). RECIBIDO sigue clickeable.
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
    assert f'data-open="modal-correct-{p.id}"' not in r.text


def test_advertencia_no_es_clickeable_en_cancelado(client):
    # CANCELADO queda fuera de `ESTADOS_CORREGIBLES` (igual que ENTREGADO,
    # ver el test de arriba) -- no tiene sentido de negocio corregir a
    # quién le iba a llegar un paquete que nunca se entregó. El modal
    # "Corregir destinatario" ni existe en el DOM ahí, así que el ícono se
    # queda plano, sin `data-open`.
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
def test_boton_corregir_aparece_en_anunciado_y_recibido_no_en_entregado_ni_cancelado(client):
    # `ESTADOS_CORREGIBLES` (paquete_lifecycle.py) incluyó ENTREGADO un
    # rato (2026-08-16), se retiró al día siguiente (2026-08-17, pedido
    # explícito del cliente: "en caso que ya el paquete esté en estado
    # Entregado o Cancelado no aparezca el botón... se vería mejor").
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
    assert f'modal-correct-{entregado.id}' not in r.text
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


def test_corregir_a_una_persona_distinta_del_anunciante_tambien_quita_la_advertencia(client):
    # Conversación 2026-08-17 ("Opción A", pedido explícito): antes, la
    # advertencia solo se apagaba si el nombre corregido coincidía
    # exactamente con el Anunciante -- confuso, corregir a propósito a
    # alguien DISTINTO (un co-residente, alguien nuevo) dejaba el ícono
    # prendido como si no se hubiera hecho nada. Ahora cualquier corrección
    # explícita apaga la advertencia, sin importar a quién se corrigió.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante
    from app.domain.persona_service import get_or_create_persona

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    get_or_create_persona(client.db, "3001234567", "Ana Perez")
    agregar_ocupante(client.db, apto, "Otra Persona Distinta", telefono="3009998888")
    client.db.commit()
    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana Perez",
        destinatario=Destinatario.solo_nombre("Ana Peres"),
        apartamento=apto,
    )
    client.db.commit()

    # candidatos_correccion: Ocupantes de la unidad primero, Anunciante al
    # final (`_construir_candidatos`) -- índice 0 es "Otra Persona
    # Distinta" (Ocupante de `apto`), NO el Anunciante "Ana Perez".
    r = client.post(
        f"/paquetes/{p.id}/corregir",
        data={"candidato_idx": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    corregido = client.db.get(Paquete, p.id)
    assert corregido.recipient_name == "OTRA PERSONA DISTINTA"
    assert corregido.corrected_at is not None

    r2 = client.get("/paquetes")
    assert "no coincide" not in r2.text.lower()


def test_asignar_apartamento_a_si_mismo_autocompleta_como_residente(client):
    # Issue 189 (ronda 5, pedido explícito -- ejemplo real reportado en vivo
    # "FANTASMA 2"): se anuncia "para mí mismo" SIN unidad -- el staff le
    # asigna una unidad REAL con residentes reales (Angélica), sin elegir
    # ninguno de ellos ni llenar "+ Nuevo residente" a mano. Ya NO queda
    # sin confirmar (ronda 4) -- se autocompleta con la identidad YA
    # conocida del propio Anunciante, igual que en Recibir.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 2", "302")
    agregar_ocupante(client.db, apto, "Angelica Arrazola", telefono="3001112233")
    client.db.commit()
    p = _anunciar(client, tel="3006667777", nombre="Fantasma 2")  # yo_mismo, sin unidad

    r = client.post(
        f"/paquetes/{p.id}/asignar-apartamento",
        data={"torre": "TORRE 2", "apartamento": "302"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/paquetes"

    r2 = client.get("/paquetes")
    assert r2.status_code == 200
    modal_ver = _segmento_modal(r2.text, f"modal-ver-{p.id}")
    assert "no coincide" not in r2.text.lower()
    assert "Residentes de la unidad" in modal_ver
    assert "FANTASMA 2" in modal_ver
    assert "ANGELICA ARRAZOLA" in modal_ver
    # Al quedar realmente confirmado, el nombre y la Torre/Apto SÍ enlazan
    # a la ficha real (issue 189 ronda 3 -- ya no a una ficha vacía).
    assert '<a href="/residentes/' in modal_ver

    client.db.expire_all()
    nuevo = (
        client.db.query(Ocupante)
        .filter(Ocupante.apartamento_id == apto.id, Ocupante.nombre == "FANTASMA 2")
        .one()
    )
    assert nuevo.persona_id is not None


def test_asignar_apartamento_sin_ser_yo_mismo_sigue_con_advertencia(client):
    # Issue 189 (ronda 5): el autocompletado SOLO aplica a "para mí mismo"
    # -- un destinatario declarado como un tercero (sin coincidir con el
    # Anunciante) sigue sin confirmar y reabre "Corregir destinatario",
    # mismo criterio de la ronda 2.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 2", "302")
    agregar_ocupante(client.db, apto, "Angelica Arrazola", telefono="3001112233")
    client.db.commit()
    p = announce(
        client.db,
        anunciante_telefono="3044444444",
        anunciante_nombre="Portero",
        destinatario=Destinatario.solo_nombre("Alguien Random"),
    )
    client.db.commit()

    r = client.post(
        f"/paquetes/{p.id}/asignar-apartamento",
        data={"torre": "TORRE 2", "apartamento": "302"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/paquetes?corregir={p.id}&aviso=residente_pendiente"

    r2 = client.get("/paquetes")
    assert r2.status_code == 200
    modal_ver = _segmento_modal(r2.text, f"modal-ver-{p.id}")
    assert "no coincide" in r2.text.lower()
    assert "Residentes de la unidad" not in modal_ver
    assert "ANGELICA ARRAZOLA" not in modal_ver
    # Issue 189 (ronda 3): tampoco enlaza a una ficha real pero vacía.
    assert '<a href="/residentes/' not in modal_ver


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
# Conversación 2026-08-16/17 — vista previa en vivo de "+ Nuevo residente"
# (GET /paquetes/{paquete_id}/nuevo-residente/identificar).
# --------------------------------------------------------------------------- #
def test_identificar_nuevo_residente_encuentra_persona_por_telefono(client):
    _login_staff(client)
    from app.domain.persona_service import get_or_create_persona

    get_or_create_persona(client.db, "3005558888", "Persona Ya Registrada")
    p = _anunciar(client, tel="3009990000", nombre="Portero")
    client.db.commit()

    r = client.get(f"/paquetes/{p.id}/nuevo-residente/identificar", params={"contacto": "3005558888"})
    assert r.status_code == 200
    assert r.json() == {"encontrado": True, "nombre": "PERSONA YA REGISTRADA", "conflicto": None}


def test_identificar_nuevo_residente_encuentra_persona_por_whatsapp(client):
    _login_staff(client)
    from app.domain.persona_service import get_or_create_persona_por_whatsapp

    get_or_create_persona_por_whatsapp(client.db, "residente.wa", "Persona Whatsapp")
    p = _anunciar(client, tel="3009990001", nombre="Portero")
    client.db.commit()

    r = client.get(f"/paquetes/{p.id}/nuevo-residente/identificar", params={"contacto": "residente.wa"})
    assert r.status_code == 200
    assert r.json() == {"encontrado": True, "nombre": "PERSONA WHATSAPP", "conflicto": None}


def test_identificar_nuevo_residente_sin_match_devuelve_encontrado_false(client):
    _login_staff(client)
    p = _anunciar(client, tel="3009990002", nombre="Portero")
    client.db.commit()

    r = client.get(f"/paquetes/{p.id}/nuevo-residente/identificar", params={"contacto": "3009998888"})
    assert r.status_code == 200
    assert r.json() == {"encontrado": False}

    r2 = client.get(f"/paquetes/{p.id}/nuevo-residente/identificar", params={"contacto": "300999"})  # a medio teclear
    assert r2.status_code == 200
    assert r2.json() == {"encontrado": False}

    r3 = client.get(f"/paquetes/{p.id}/nuevo-residente/identificar", params={"contacto": ""})
    assert r3.status_code == 200
    assert r3.json() == {"encontrado": False}


def test_identificar_nuevo_residente_conflicto_no_principal(client):
    # El contacto ya es Ocupante activo NO-principal de otra unidad --
    # "conflicto" trae la unidad real y es_principal=False, para que el JS
    # muestre "Mudar residente a <unidad de ESTE paquete>".
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_staff(client)
    apto_conflicto = resolver_apartamento(client.db, "TORRE 2", "202")
    agregar_ocupante(client.db, apto_conflicto, "Hija", telefono="3005557777")
    apto_paquete = resolver_apartamento(client.db, "TORRE 3", "303")
    p = announce(
        client.db,
        anunciante_telefono="3009990003",
        anunciante_nombre="Portero",
        destinatario=Destinatario.yo_mismo(),
        apartamento=apto_paquete,
    )
    client.db.commit()

    r = client.get(f"/paquetes/{p.id}/nuevo-residente/identificar", params={"contacto": "3005557777"})
    assert r.status_code == 200
    data = r.json()
    assert data["encontrado"] is True
    assert data["conflicto"] == {
        "es_principal": False,
        "torre": "TORRE 2",
        "apartamento": "202",
        "persona_id": data["conflicto"]["persona_id"],
    }


def test_identificar_nuevo_residente_conflicto_principal(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_staff(client)
    apto_conflicto = resolver_apartamento(client.db, "TORRE 2", "202")
    principal = agregar_ocupante(client.db, apto_conflicto, "Principal", telefono="3005556666")
    confirmar_ocupante(client.db, principal, staff)
    apto_paquete = resolver_apartamento(client.db, "TORRE 3", "303")
    p = announce(
        client.db,
        anunciante_telefono="3009990004",
        anunciante_nombre="Portero",
        destinatario=Destinatario.yo_mismo(),
        apartamento=apto_paquete,
    )
    client.db.commit()

    r = client.get(f"/paquetes/{p.id}/nuevo-residente/identificar", params={"contacto": "3005556666"})
    assert r.status_code == 200
    data = r.json()
    assert data["conflicto"]["es_principal"] is True
    assert data["conflicto"]["torre"] == "TORRE 2"
    assert data["conflicto"]["apartamento"] == "202"


# --------------------------------------------------------------------------- #
# Conversación 2026-08-17 — "Promover a otro residente" sin salir de
# "Corregir destinatario" (GET /paquetes/promover-candidatos,
# POST /paquetes/promover-principal).
# --------------------------------------------------------------------------- #
def test_promover_candidatos_excluye_al_principal_actual(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 6", "601")
    principal = agregar_ocupante(client.db, apto, "Principal", telefono="3007771111")
    confirmar_ocupante(client.db, principal, staff)
    secundario = agregar_ocupante(client.db, apto, "Secundario", telefono="3007772222")
    client.db.commit()

    r = client.get("/paquetes/promover-candidatos", params={"torre": "TORRE 6", "apartamento": "601"})
    assert r.status_code == 200
    data = r.json()
    assert data["unidad"] == "TORRE 6 · Apto 601"
    assert data["candidatos"] == [{"ocupante_id": str(secundario.id), "nombre": "SECUNDARIO"}]


def test_promover_candidatos_vacio_si_no_hay_mas_residentes(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 6", "602")
    principal = agregar_ocupante(client.db, apto, "Solo Principal", telefono="3007773333")
    confirmar_ocupante(client.db, principal, staff)
    client.db.commit()

    r = client.get("/paquetes/promover-candidatos", params={"torre": "TORRE 6", "apartamento": "602"})
    assert r.status_code == 200
    assert r.json() == {"unidad": "TORRE 6 · Apto 602", "candidatos": []}


def test_promover_candidatos_unidad_invalida_devuelve_vacio(client):
    _login_staff(client)
    r = client.get("/paquetes/promover-candidatos", params={"torre": "TORRE 99", "apartamento": "999"})
    assert r.status_code == 200
    assert r.json() == {"unidad": None, "candidatos": []}


def test_promover_principal_promueve_y_degrada_al_anterior(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 6", "603")
    principal = agregar_ocupante(client.db, apto, "Viejo Principal", telefono="3007774444")
    confirmar_ocupante(client.db, principal, staff)
    secundario = agregar_ocupante(client.db, apto, "Nuevo Principal", telefono="3007775555")
    client.db.commit()

    r = client.post(
        "/paquetes/promover-principal",
        data={"ocupante_id": str(secundario.id)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/paquetes"

    client.db.expire_all()
    assert client.db.get(Ocupante, secundario.id).es_principal is True
    assert client.db.get(Ocupante, principal.id).es_principal is False


def test_promover_principal_con_paquete_y_contacto_redirige_a_corregir(client):
    # Conversación 2026-08-17 (pedido explícito): tras promover desde "+
    # Nuevo residente" de un paquete puntual, vuelve reabriendo ESE modal
    # "Corregir" + el contacto re-tecleado solo, en vez del /paquetes de
    # siempre.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 6", "604")
    principal = agregar_ocupante(client.db, apto, "Viejo Principal Dos", telefono="3007776666")
    confirmar_ocupante(client.db, principal, staff)
    secundario = agregar_ocupante(client.db, apto, "Nuevo Principal Dos", telefono="3007777777")
    p = _anunciar(client, tel="3007778888", nombre="Portero")
    client.db.commit()

    r = client.post(
        "/paquetes/promover-principal",
        data={"ocupante_id": str(secundario.id), "paquete_id": str(p.id), "contacto": "3007776666"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/paquetes?corregir={p.id}&recontactar=3007776666"

    r2 = client.get(r.headers["location"])
    assert r2.status_code == 200
    modal_correct = _segmento_modal(r2.text, f"modal-correct-{p.id}")
    apertura = modal_correct[: modal_correct.index(">") + 1]
    assert "hidden" not in apertura  # el modal reabre visible


def test_promover_principal_con_origen_recibir_redirige_a_recibir(client):
    # Conversación 2026-08-17, pedido explícito ("punto 2" -- portar la
    # vista previa de "+ Nuevo residente" a Recibir también): el mismo
    # modal "Promover" ahora sirve a los dos -- `origen=recibir` decide
    # cuál de los dos reabre.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 6", "605")
    principal = agregar_ocupante(client.db, apto, "Viejo Principal Tres", telefono="3007779999")
    confirmar_ocupante(client.db, principal, staff)
    secundario = agregar_ocupante(client.db, apto, "Nuevo Principal Tres", telefono="3007771111")
    # El paquete necesita SU PROPIO apartamento para que Recibir ofrezca
    # "+ Nuevo residente" (issue 116: esa sección se oculta por completo
    # sin apartamento resuelto) -- distinto del apartamento del conflicto.
    apto_paquete = resolver_apartamento(client.db, "TORRE 6", "606")
    p = announce(
        client.db,
        anunciante_telefono="3007772222",
        anunciante_nombre="Portero",
        destinatario=Destinatario.yo_mismo(),
        apartamento=apto_paquete,
    )
    client.db.commit()

    r = client.post(
        "/paquetes/promover-principal",
        data={
            "ocupante_id": str(secundario.id),
            "paquete_id": str(p.id),
            "contacto": "3007779999",
            "origen": "recibir",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/paquetes?recibir={p.id}&recontactar=3007779999"

    r2 = client.get(r.headers["location"])
    assert r2.status_code == 200
    modal_recibir = _segmento_modal(r2.text, f"modal-receive-{p.id}")
    apertura = modal_recibir[: modal_recibir.index(">") + 1]
    assert "hidden" not in apertura  # el modal reabre visible
    assert f'id="recibir-candidato-nuevo-{p.id}"' in modal_recibir


def test_entregar_query_param_reabre_el_modal_entregar(client):
    # Issue 164 (.scratch/pendientes-cliente): mismo mecanismo que `?ver=`/
    # `?corregir=`/`?recibir=` -- `/paquetes?entregar=<id>` abre el modal
    # "Entregar" directo, sin que el staff tenga que buscarlo en la lista
    # (usado desde /announce al identificar a un residente con un paquete
    # RECIBIDO en curso).
    staff = _login_staff(client)
    p = _anunciar(client, tel="3001234567", nombre="Ana")
    dom_receive(client.db, p, staff)
    client.db.commit()

    r = client.get(f"/paquetes?entregar={p.id}")
    assert r.status_code == 200
    modal_deliver = _segmento_modal(r.text, f"modal-deliver-{p.id}")
    apertura = modal_deliver[: modal_deliver.index(">") + 1]
    assert "hidden" not in apertura  # el modal reabre visible


def test_sin_entregar_query_param_el_modal_entregar_queda_cerrado(client):
    staff = _login_staff(client)
    p = _anunciar(client, tel="3001234567", nombre="Ana")
    dom_receive(client.db, p, staff)
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_deliver = _segmento_modal(r.text, f"modal-deliver-{p.id}")
    apertura = modal_deliver[: modal_deliver.index(">") + 1]
    assert "hidden" in apertura


def test_promover_principal_ocupante_inexistente_da_404(client):
    _login_staff(client)
    r = client.post("/paquetes/promover-principal", data={"ocupante_id": "no-es-un-uuid"})
    assert r.status_code == 404


def test_identificar_nuevo_residente_sin_conflicto_si_ya_es_de_esta_misma_unidad(client):
    # Si el contacto ya es Ocupante de la MISMA unidad de este paquete, no
    # hay nada que "mudar" -- conflicto queda None (ya candidatos_correccion
    # lo ofrecería como tarjeta, este endpoint no debe sumar ruido).
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 3", "303")
    agregar_ocupante(client.db, apto, "Ya Residente Acá", telefono="3005559999")
    p = announce(
        client.db,
        anunciante_telefono="3009990005",
        anunciante_nombre="Portero",
        destinatario=Destinatario.yo_mismo(),
        apartamento=apto,
    )
    client.db.commit()

    r = client.get(f"/paquetes/{p.id}/nuevo-residente/identificar", params={"contacto": "3005559999"})
    assert r.status_code == 200
    assert r.json() == {"encontrado": True, "nombre": "YA RESIDENTE ACÁ", "conflicto": None}


def test_identificar_nuevo_residente_requiere_sesion_de_staff(client):
    p = _anunciar(client, tel="3009990006", nombre="Portero")
    r = client.get(
        f"/paquetes/{p.id}/nuevo-residente/identificar",
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


def test_nuevo_residente_nombre_oculto_hasta_teclear_contacto(client):
    # Conversación 2026-08-17 (pedido explícito): el campo Nombre arranca
    # oculto -- el JS lo revela recién al teclear el contacto (relleno y de
    # solo lectura si ya existe, vacío y editable si no). Acá solo se
    # verifica el estado SERVIDO inicial: el wrapper trae `hidden`, y el
    # contacto aparece ANTES que el Nombre en el HTML (orden Contacto ->
    # Nombre, no al revés).
    _login_staff(client)
    p = _anunciar(client, tel="3001234567", nombre="Ana")

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_correct = _segmento_modal(r.text, f"modal-correct-{p.id}")
    idx_wrap = modal_correct.index(f'id="nuevo-ocupante-nombre-wrap-{p.id}"')
    apertura_wrap = modal_correct[idx_wrap : modal_correct.index(">", idx_wrap) + 1]
    assert "hidden" in apertura_wrap

    idx_contacto = modal_correct.index('name="nuevo_ocupante_contacto"')
    assert idx_contacto < idx_wrap


def test_nuevo_residente_boton_visible_si_el_paquete_tiene_apartamento(client):
    from app.domain.apartamento_service import resolver_apartamento

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 6", "101")
    p = announce(client.db, "3001234567", "Ana", Destinatario.yo_mismo(), apartamento=apto)
    client.db.commit()

    r = client.get("/paquetes")
    modal_correct = _segmento_modal(r.text, f"modal-correct-{p.id}")
    assert "Nuevo residente" in modal_correct
    assert "Sin apartamento asignado" not in modal_correct


def test_nuevo_residente_ofrece_asignar_apartamento_si_anunciado_y_sin_apartamento(client):
    # Conversación 2026-08-17 (pedido explícito): "+ Nuevo residente" exige
    # que el paquete YA tenga apartamento (`agregar_ocupante`/`mover_
    # ocupante` necesitan saber a cuál) -- en ANUNCIADO existe "Asignar
    # apartamento" (issue 85-88) para resolver eso primero, así que la
    # opción hace swap directo a ese modal en vez de solo desaparecer.
    _login_staff(client)
    p = _anunciar(client, tel="3001234567", nombre="Ana")  # yo_mismo, sin apartamento
    client.db.commit()

    r = client.get("/paquetes")
    modal_correct = _segmento_modal(r.text, f"modal-correct-{p.id}")
    assert "Nuevo residente" not in modal_correct
    assert "Sin apartamento asignado -- asignar apartamento primero" in modal_correct
    assert f'data-open="modal-asignar-apto-{p.id}"' in modal_correct
    assert f'data-close="modal-correct-{p.id}"' in modal_correct


def test_nuevo_residente_ofrece_asignar_apartamento_si_recibido_y_sin_apartamento(client):
    # Issue 135, pedido explícito 2026-08-19: "la asignacion de apartamento
    # para esta vista 'Corregir destinatario' podra ser para los estados
    # Anunciado y Recibido" -- antes RECIBIDO se quedaba con el aviso
    # bloqueante "no se puede agregar un nuevo residente acá" sin ninguna
    # acción; ahora ofrece el mismo swap a "Asignar apartamento" que ya
    # tenía ANUNCIADO.
    staff = _login_staff(client)
    p = _anunciar(client, tel="3001234567", nombre="Ana")
    dom_receive(client.db, p, staff)
    client.db.commit()

    r = client.get("/paquetes")
    modal_correct = _segmento_modal(r.text, f"modal-correct-{p.id}")
    assert "Nuevo residente" not in modal_correct
    assert "no se puede agregar un nuevo residente acá" not in modal_correct
    assert "Sin apartamento asignado -- asignar apartamento primero" in modal_correct
    assert f'data-open="modal-asignar-apto-{p.id}"' in modal_correct
    assert f'data-close="modal-correct-{p.id}"' in modal_correct


def test_nuevo_residente_solo_aviso_si_entregado_y_sin_apartamento(client):
    # ENTREGADO no llega a este modal en absoluto (`ESTADOS_CORREGIBLES`
    # excluye Entregado/Cancelado, ver issue 105) -- este test documenta
    # ese límite, no una restricción propia de "Asignar apartamento".
    staff = _login_staff(client)
    p = _anunciar(client, tel="3001234567", nombre="Ana")
    dom_receive(client.db, p, staff)
    dom_deliver(client.db, p, staff)
    client.db.commit()

    r = client.get("/paquetes")
    assert f'id="modal-correct-{p.id}"' not in r.text


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


def test_modal_corregir_candidatos_muestran_badge_de_estado_de_ocupante(client):
    # Mismo badge que Recibir (conversación 2026-08-17, pedido explícito
    # de unificar look and feel con /residentes).
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    carlos = agregar_ocupante(client.db, apto, "Carlos", telefono="3011110001")
    agregar_ocupante(client.db, apto, "Pedro", telefono="3011110003")  # queda pendiente
    client.db.commit()
    confirmar_ocupante(client.db, carlos, staff)  # sin principal todavía -> lo promueve
    client.db.commit()

    p = announce(
        client.db,
        anunciante_telefono="3099999999",
        anunciante_nombre="Portero",
        destinatario=Destinatario.solo_nombre("Alguien Mas"),
        apartamento=apto,
    )
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_correct = _segmento_modal(r.text, f"modal-correct-{p.id}")
    idx_carlos = modal_correct.index("CARLOS")
    idx_pedro = modal_correct.index("PEDRO")
    assert "Principal" in modal_correct[idx_carlos : idx_carlos + 300]
    assert "Pendiente" in modal_correct[idx_pedro : idx_pedro + 300]


def test_modal_corregir_candidatos_sin_icono_y_actual_con_fondo(client):
    # Conversación 2026-08-17 (unificación con Recibir, issue 117/118):
    # sin ícono de persona (Recibir tampoco lo tiene, dirección "menos" en
    # vez de agregarlo ahí), y el candidato que ya es el destinatario
    # actual lleva el mismo fondo `bg-slate-100` que en Recibir. El
    # teléfono SÍ se queda (a diferencia de Recibir) -- decisión explícita,
    # ver docstring del template.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Jesus Villalobos", telefono="3033333333")
    client.db.commit()
    # yo_mismo(): recipient_name/telefono == los del Anunciante -- match.
    p = announce(
        client.db,
        anunciante_telefono="3044444444",
        anunciante_nombre="Portero",
        destinatario=Destinatario.yo_mismo(),
        apartamento=apto,
    )
    client.db.commit()
    assert p.recipient_name == "PORTERO"

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_correct = _segmento_modal(r.text, f"modal-correct-{p.id}")
    assert "JESUS VILLALOBOS" in modal_correct
    assert "+573033333333" in modal_correct  # teléfono se queda acá
    assert "PORTERO" in modal_correct
    assert "h-5 w-5 text-slate-400 shrink-0" not in modal_correct  # sin ícono de persona

    idx_portero = modal_correct.index("PORTERO")
    fragmento_portero = modal_correct[max(0, idx_portero - 400) : idx_portero]
    assert "bg-slate-100" in fragmento_portero  # candidato actual

    idx_jesus = modal_correct.index("JESUS VILLALOBOS")
    fragmento_jesus = modal_correct[max(0, idx_jesus - 400) : idx_jesus]
    assert "bg-slate-100" not in fragmento_jesus  # no es el actual


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
    assert "activa la opción de mudarlo" in r.text


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


def test_recibir_nuevo_ocupante_mueve_marcando_la_casilla(client):
    # Conversación 2026-08-17, pedido explícito -- reemplaza la prueba
    # anterior ("Recibir no ofrece mover"): antes bloqueaba en seco con el
    # mensaje genérico de `agregar_ocupante`; ahora usa el mismo mecanismo
    # que ya tenía Corregir destinatario (ticket 12), nada nuevo, solo
    # conectado acá también. El nombre tecleado se ignora -- se corrige el
    # destinatario a la identidad REAL (Hija), no se crea un residente
    # nuevo "Cualquiera".
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
    assert paquete.estado == EstadoPaquete.RECIBIDO


def test_recibir_nuevo_ocupante_contacto_ya_ocupante_bloquea_sin_mover(client):
    # Sin marcar la casilla, sigue bloqueado -- pero ahora con el mensaje
    # ENRIQUECIDO ("activa la opción de mudarlo"), no el genérico y sin
    # salida de antes.
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
        f"/paquetes/{p.id}/recibir",
        data={
            "candidato_idx": "nuevo",
            "nuevo_ocupante_nombre": "Cualquiera",
            "nuevo_ocupante_contacto": "3021112233",
        },
    )
    assert r.status_code == 400
    assert "activa la opción de mudarlo" in r.text


def test_modal_recibir_nuevo_residente_tiene_vista_previa_en_vivo(client):
    # Conversación 2026-08-17, pedido explícito ("punto 2"): la misma
    # vista previa en vivo de "+ Nuevo residente" que ya tenía Corregir
    # destinatario -- contacto, nombre bloqueable, preview, "Mudar
    # residente" con la unidad REAL de este paquete. Issue 159 (.scratch/
    # pendientes-cliente): un Principal ya no desvía a un aviso aparte con
    # "Degradarlo" -- el mismo checkbox de "Mudar residente" alcanza (se
    # degrada automáticamente al mover).
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 3", "301")
    agregar_ocupante(client.db, apto, "Alguien", telefono="3099990000")
    client.db.commit()

    p = announce(
        client.db,
        anunciante_telefono="3011112222",
        anunciante_nombre="Portero",
        destinatario=Destinatario.yo_mismo(),
        apartamento=apto,
    )
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_recibir = _segmento_modal(r.text, f"modal-receive-{p.id}")
    assert f'id="recibir-nuevo-ocupante-nombre-wrap-{p.id}"' in modal_recibir
    assert f'id="recibir-nuevo-ocupante-preview-{p.id}"' in modal_recibir
    assert "Mudar residente a TORRE 3 · Apto 301" in modal_recibir


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


def test_corregir_un_recibido_se_permite(client):
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


def test_corregir_un_entregado_se_rechaza_sin_efecto(client):
    # `ESTADOS_CORREGIBLES` incluyó ENTREGADO un rato (2026-08-16), se
    # retiró al día siguiente (2026-08-17, pedido explícito del cliente).
    staff = _login_staff(client)
    p = _anunciar_con_mismatch(client, registrado="Beto Ruiz")
    dom_receive(client.db, p, staff)
    dom_deliver(client.db, p, staff)
    client.db.commit()
    nombre_original = p.recipient_name

    r = client.post(
        f"/paquetes/{p.id}/corregir", data={"candidato_idx": "0"}, follow_redirects=False
    )
    assert r.status_code == 400

    client.db.expire_all()
    assert client.db.get(Paquete, p.id).recipient_name == nombre_original


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


def test_badges_conteo_anunciado_y_recibido_en_la_barra_de_filtros(client):
    # Issue 126, pedido explícito: badges de conteo (solo número) sobre los
    # íconos Anunciado/Recibido de la barra de filtros -- GLOBAL, no
    # filtrado por la búsqueda/estado activo.
    staff = _login_staff(client)
    _anunciar(client, tel="3001234561", nombre="Ana")
    _anunciar(client, tel="3001234562", nombre="Beto")  # 2 en ANUNCIADO
    recibido = _anunciar(client, tel="3001234563", nombre="Cami")
    dom_receive(client.db, recibido, staff)  # 1 en RECIBIDO
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    idx_anunciado = r.text.index('data-estado-icono="ANUNCIADO"')
    idx_recibido = r.text.index('data-estado-icono="RECIBIDO"')
    idx_entregado = r.text.index('data-estado-icono="ENTREGADO"')
    idx_cancelado = r.text.index('data-estado-icono="CANCELADO"')
    assert ">2</span>" in r.text[idx_anunciado : idx_anunciado + 1050]
    assert ">1</span>" in r.text[idx_recibido : idx_recibido + 1050]
    # Entregado/Cancelado nunca llevan badge (issue 126: solo Anunciado/
    # Recibido), sin importar si hay paquetes en esos estados.
    assert "rounded-full bg-red-600 text-white" not in r.text[idx_entregado : idx_entregado + 1050]
    assert "rounded-full bg-red-600 text-white" not in r.text[idx_cancelado : idx_cancelado + 1050]


def test_badges_conteo_es_global_no_filtrado_por_busqueda_activa(client):
    staff = _login_staff(client)
    _anunciar(client, tel="3001234561", nombre="Ana")
    _anunciar(client, tel="3001234562", nombre="Beto")  # 2 en ANUNCIADO
    recibido = _anunciar(client, tel="3001234563", nombre="Cami")
    dom_receive(client.db, recibido, staff)
    client.db.commit()

    # Filtrando por RECIBIDO, el badge de ANUNCIADO sigue mostrando el
    # total real (2) -- no se reduce a lo que hay en pantalla (0 filas
    # ANUNCIADO visibles bajo este filtro).
    r = client.get("/paquetes", params={"estado": "RECIBIDO"})
    assert r.status_code == 200
    idx_anunciado = r.text.index('data-estado-icono="ANUNCIADO"')
    assert ">2</span>" in r.text[idx_anunciado : idx_anunciado + 1050]


def test_badges_conteo_ausente_cuando_no_hay_pendientes(client):
    _login_staff(client)
    r = client.get("/paquetes")
    assert r.status_code == 200
    idx_anunciado = r.text.index('data-estado-icono="ANUNCIADO"')
    idx_recibido = r.text.index('data-estado-icono="RECIBIDO"')
    # Sin paquetes, ningún badge -- el `<span>` con conteo simplemente no
    # se renderiza (no es "0" visible, es ausencia total).
    assert "rounded-full bg-red-600 text-white" not in r.text[idx_anunciado : idx_anunciado + 1050]
    assert "rounded-full bg-red-600 text-white" not in r.text[idx_recibido : idx_recibido + 1050]


def test_badges_conteo_no_aparecen_en_el_fragmento_de_busqueda_en_vivo(client):
    # La barra de filtros (con los badges) vive fuera de `_resultados.html`
    # -- el fetch en vivo nunca la incluye, confirmando que no hace falta
    # recalcular el conteo en cada tecleo.
    _login_staff(client)
    _anunciar(client, nombre="Ana")
    client.db.commit()

    r = client.get("/paquetes", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert 'data-estado-icono' not in r.text


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


def test_filtro_por_q_encuentra_por_telefono_parcial_ultimos_4_digitos(client):
    # Pedido 2026-08-20: antes exigía el teléfono COMPLETO y válido -- ahora
    # alcanza con los últimos 4 dígitos (o cualquier substring de >= 4).
    _login_staff(client)
    _anunciar(client, tel="3001234567", nombre="Ana")
    _anunciar(client, tel="3019999999", nombre="Beto")
    client.db.commit()

    r = client.get("/paquetes", params={"q": "4567"})
    assert r.status_code == 200
    assert "ANA" in r.text
    assert "BETO" not in r.text


def test_filtro_por_q_con_menos_de_4_digitos_no_busca_por_telefono(client):
    # Menos de 4 dígitos no dispara el matching de teléfono -- evita que un
    # texto corto (torre/apartamento) haga falsos positivos contra
    # prácticamente cualquier número.
    _login_staff(client)
    _anunciar(client, tel="3001234567", nombre="Ana")
    client.db.commit()

    r = client.get("/paquetes", params={"q": "567"})
    assert r.status_code == 200
    assert "ANA" not in r.text


def test_filtro_por_q_encuentra_por_email_del_anunciante(client):
    from app.domain.persona_service import get_or_create_persona, update_datos_personales

    _login_staff(client)
    ana = get_or_create_persona(client.db, "3001234567", "Ana")
    update_datos_personales(client.db, ana, email="ana@correo.com")
    client.db.commit()
    _anunciar(client, tel="3001234567", nombre="Ana")
    _anunciar(client, tel="3019999999", nombre="Beto")
    client.db.commit()

    r = client.get("/paquetes", params={"q": "ana@correo.com"})
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


def test_lista_ordena_por_ultimo_cambio_de_estado_no_por_anuncio(client):
    # Conversación 2026-08-17, pedido explícito: "ordenado desde el
    # ultimo cambio de estado hasta el mas antiguo... siempre lo mas
    # reciente de primero". "Anunciado Primero" se anuncia ANTES que
    # "Anunciado Segundo", pero se RECIBE después de que el segundo ya
    # fue anunciado -- su último cambio de estado (recibido) es más
    # reciente que el simple anuncio del segundo, así que debe aparecer
    # ANTES en la lista pese a haberse anunciado primero.
    staff = _login_staff(client)
    primero = _anunciar(client, tel="3001111111", nombre="Anunciado Primero")
    segundo = _anunciar(client, tel="3002222222", nombre="Anunciado Segundo")
    dom_receive(client.db, primero, staff)
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    idx_primero = r.text.index("ANUNCIADO PRIMERO")
    idx_segundo = r.text.index("ANUNCIADO SEGUNDO")
    assert idx_primero < idx_segundo


def test_paginacion_con_mas_de_20_paquetes(client):
    # _POR_PAGINA = 20 (de vuelta a 20 el 2026-08-20, pedido explícito) -- 25
    # paquetes caen en 2 páginas: 24..5 / 4..0.
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
    # count + un puñado de lookups batch, incluido `_personas_por_telefono`
    # -- conversación 2026-08-17, WhatsApp del destinatario --,
    # `_personas_por_nombre` -- issue 101, .scratch/pendientes-cliente:
    # ampliado de "solo paquetes sin teléfono" a TODO `recipient_name`, así
    # que ahora corre siempre (antes se saltaba si todos los paquetes de la
    # página tenían teléfono) --, `_conteos_pendientes` -- issue 126, badges
    # de Anunciado/Recibido en la barra de filtros --, `cambios_recientes_
    # de_apartamento` -- issue 165, ícono 🔄 --, `preferencias_activas_por_
    # persona` -- issue 222, .scratch/pendientes-cliente: gate del botón de
    # WhatsApp por preferencia --, y `listar_motivos` -- `.scratch/motivos-
    # cancelacion-catalogo`, ticket 03: opciones del picker de "Cancelar
    # paquete", lee el catálogo en vez del enum fijo (antes 0 queries,
    # iteración de un enum Python en memoria): cada una 1 query agrupada
    # FIJA, no por paquete) pero muy por debajo de lo que daría 1+ query por
    # cada uno de los 8 paquetes -- si el N+1 se reintrodujera, este número
    # saltaría con la cantidad de paquetes, no se quedaría fijo.
    assert len(queries) <= 16, (
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
    for encabezado in ("Cliente", "Dirección", "Fecha", "Acciones"):
        assert f">{encabezado}<" in r.text
    # "Guía" y "Última acción" ya no son columnas propias (como <th>).
    assert ">Guía<" not in r.text
    assert ">Última acción<" not in r.text
    # "Estado" tampoco (issue 129, pedido explícito) -- el chip de código
    # de acceso (columna Cliente) ya lleva el color por Estado.
    assert ">Estado<" not in r.text


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


def test_icono_whatsapp_en_acciones_prioriza_el_username_sobre_el_telefono(client):
    # Conversación 2026-08-17 (pedido explícito): "el ícono de WhatsApp...
    # debería estar enfocado al nombre de usuario de whatsapp antes que el
    # número de teléfono" -- mismo criterio que `persona_service.
    # url_whatsapp`, que ya hacía esto en el resto de la app (Ver, /residentes)
    # pero no en la columna Acciones de /paquetes, que armaba el link
    # directo del teléfono crudo del snapshot.
    from app.domain.persona import Persona
    from app.domain.persona_service import update_datos_personales

    _login_staff(client)
    p = _anunciar(client, tel="3001234567", nombre="Ana")
    persona = client.db.query(Persona).filter(Persona.telefono == "+573001234567").one()
    update_datos_personales(client.db, persona, whatsapp_usuario="ana.whats")
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert 'href="https://wa.me/ana.whats?text=' in r.text
    assert "https://wa.me/573001234567" not in r.text


def test_icono_telefono_en_acciones_cae_al_telefono_del_anunciante_sin_telefono_propio(client):
    # Bug real reportado en vivo (conversación 2026-08-17, ejemplo real
    # "6Y5U"): un destinatario SOLO-NOMBRE (`Destinatario.solo_nombre`,
    # `recipient_phone` vacío A PROPÓSITO, ADR-0007) deja el ícono de
    # Llamar apagado en Acciones aunque el Anunciante SÍ tenga teléfono --
    # el modal "Ver" ya usa este mismo fallback en su línea de teléfono
    # (`persona_anunciante.telefono`), la columna Acciones no lo tenía.
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
    assert r.status_code == 200
    # Ojo: `tel:+573001234567` TAMBIÉN aparece en el modal "Ver" (que ya
    # tenía este fallback) -- hay que anclar al `<a>` de Acciones
    # específicamente (su combo de clases es propio, distinto del de Ver),
    # no a cualquier aparición del href en la página completa. Título
    # explícito "del anunciante" (mismo criterio que ya usa el ícono de
    # Email) -- puede no ser el teléfono del destinatario real.
    esperado = (
        f'<a href="tel:+573001234567" class="h-9 w-9 shrink-0 rounded-lg flex items-center '
        f'justify-center transition focus-visible:outline-none focus-visible:ring-2 '
        f'focus-visible:ring-offset-2 hover:bg-slate-100 text-blue-800 hover:text-blue-900 '
        f'focus-visible:ring-blue-300" '
        f'aria-label="Llamar al anunciante de {p.recipient_name}" '
        f'title="Teléfono del anunciante: +573001234567">'
    )
    assert esperado in r.text


def test_icono_whatsapp_en_acciones_cae_al_telefono_sin_username(client):
    _login_staff(client)
    p = _anunciar(client, tel="3001234567", nombre="Ana")
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert 'href="https://wa.me/573001234567?text=' in r.text
    # Issue 305 (.scratch/pendientes-cliente): link desktop coexiste en el
    # HTML (CSS decide cuál se ve) -- web.whatsapp.com captura la PWA de
    # Chrome, algo que wa.me no hace en un escritorio.
    assert 'href="https://web.whatsapp.com/send?phone=573001234567&amp;text=' in r.text


def test_whatsapp_url_destinatario_sin_persona_resuelta_cae_al_telefono(client):
    # `recipient_phone` sin ninguna Persona registrada detrás -- posible
    # (aunque no común) vía el texto libre de "Corregir destinatario",
    # sin pasar por `get_or_create_persona`. Se prueba la función
    # directamente: forzar ese estado por HTTP requeriría un paquete sin
    # NINGÚN candidato posible (el Anunciante siempre cuenta como uno),
    # un caso borde frágil de armar de punta a punta.
    from app.web.routes.packages import _whatsapp_url_destinatario

    _login_staff(client)
    p = _anunciar(client, tel="3001234567", nombre="Ana")
    client.db.commit()

    assert _whatsapp_url_destinatario(p, None) == "https://wa.me/573001234567"
    # Issue 305: variante desktop del mismo fallback (recipient_phone sin
    # Persona resuelta) -- mismo criterio de dominio que el resto.
    assert (
        _whatsapp_url_destinatario(p, None, desktop=True)
        == "https://web.whatsapp.com/send?phone=573001234567"
    )


def test_icono_whatsapp_en_acciones_resuelve_por_nombre_sin_telefono_en_snapshot(client):
    # Bug real reportado en vivo (conversación 2026-08-17, ejemplo "CAMILA
    # OSPINA"): un destinatario SOLO-WhatsApp (sin Teléfono propio,
    # ADR-0007) deja `recipient_phone` vacío A PROPÓSITO
    # (`telefono_notificacion_ocupante` nunca mete un username de WhatsApp
    # ahí, ver su docstring) -- sin teléfono que buscar, la búsqueda por
    # teléfono (issue 103) nunca la encontraba, y el ícono quedaba
    # apagado aunque la Persona SÍ tuviera `whatsapp_usuario`. Ahora, sin
    # teléfono en el snapshot, cae a buscarla por nombre exacto.
    from app.domain.persona_service import get_or_create_persona_por_whatsapp

    _login_staff(client)
    get_or_create_persona_por_whatsapp(client.db, "camila.wa", "Camila Ospina")
    client.db.commit()
    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Portero",
        destinatario=Destinatario.solo_nombre("Camila Ospina"),
    )
    client.db.commit()
    assert p.recipient_phone is None

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert 'href="https://wa.me/camila.wa?text=' in r.text


def test_icono_whatsapp_en_acciones_cae_al_anunciante_si_nada_mas_resuelve(client):
    # Mismo bug real ("6Y5U") que el ícono de Teléfono de arriba, pero para
    # WhatsApp: destinatario SOLO-NOMBRE sin ningún match de Persona (no
    # "Camila Ospina" -- un nombre que NO existe como Persona registrada) y
    # sin `recipient_phone` -- antes quedaba en `None` (ícono apagado)
    # aunque el Anunciante SÍ tenga teléfono. Cae al WhatsApp derivado del
    # teléfono del Anunciante (`persona_service.url_whatsapp`, sin
    # `whatsapp_usuario` propio cae al teléfono).
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
    assert r.status_code == 200
    assert 'href="https://wa.me/573001234567?text=' in r.text
    assert 'href="https://web.whatsapp.com/send?phone=573001234567&amp;text=' in r.text


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


def test_modal_ver_titulo_enlaza_nombre_a_residentes_y_codigo_a_consultar(client):
    # Conversación 2026-08-21, pedido explícito: nombre -> /residentes/<id>
    # (SOLO si se resuelve una Persona real detrás del destinatario, mismo
    # `persona_destino` ya resuelto para el ícono de WhatsApp) y código ->
    # /consultar?q=.
    from app.domain.persona_service import get_or_create_persona

    _login_staff(client)
    p = _anunciar(client, tel="3001234567", nombre="Ana")  # Destinatario.yo_mismo()
    client.db.commit()
    persona = get_or_create_persona(client.db, "3001234567", "Ana")

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_ver = _segmento_modal(r.text, f"modal-ver-{p.id}")
    assert f'<a href="/residentes/{persona.id}" class="text-blue-800 hover:underline">ANA</a>' in modal_ver
    assert f'<a href="/consultar?q={p.access_code}"' in modal_ver
    assert f'>{p.access_code}</a>' in modal_ver


def test_modal_ver_titulo_sin_persona_resuelta_nombre_queda_como_texto(client):
    # Destinatario.solo_nombre: recipient_phone queda NULL a propósito (`un
    # nombre bajo el teléfono del Anunciante, sin Persona` -- ver
    # paquete_service.announce) y "Nombre Que No Coincide" no matchea a
    # nadie registrado -- ni `_personas_por_telefono` ni su fallback
    # `_personas_por_nombre` resuelven nada, el nombre no tiene a dónde
    # enlazar y se queda como texto plano. El código de acceso SÍ sigue
    # enlazando siempre.
    _login_staff(client)
    p = announce(
        client.db,
        anunciante_telefono="3044444444",
        anunciante_nombre="Portero",
        destinatario=Destinatario.solo_nombre("Nombre Que No Coincide"),
    )
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_ver = _segmento_modal(r.text, f"modal-ver-{p.id}")
    assert '<a href="/residentes/' not in modal_ver
    assert "NOMBRE QUE NO COINCIDE" in modal_ver
    assert f'<a href="/consultar?q={p.access_code}"' in modal_ver


def test_modal_ver_torre_apto_enlaza_a_tab_residentes(client):
    # issue 100, .scratch/pendientes-cliente: Torre/Apto (direccion_corta)
    # enlaza a la tab "Residentes del apartamento" de /residentes cuando SÍ
    # se resolvió una Persona real detrás del destinatario -- mismo guard
    # que ya usa el nombre del título (persona_destino_id).
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante
    from app.domain.persona_service import get_or_create_persona

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 10", "101")
    # Issue 189 (ronda 3, pedido explícito): "para mí mismo" ya no confirma
    # por sí solo con Apartamento resuelto -- Ana debe ser Ocupante real de
    # esa unidad para que este link se considere resuelto.
    agregar_ocupante(client.db, apto, "Ana", telefono="3001234567")
    client.db.commit()
    p = announce(client.db, "3001234567", "Ana", Destinatario.yo_mismo(), apartamento=apto)
    client.db.commit()
    persona = get_or_create_persona(client.db, "3001234567", "Ana")

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_ver = _segmento_modal(r.text, f"modal-ver-{p.id}")
    assert f'<a href="/residentes/{persona.id}?tab=residentes"' in modal_ver
    assert "Torre 10 · Apt 101" in modal_ver


def test_modal_ver_torre_apto_sin_persona_resuelta_queda_como_texto(client):
    # Mismo caso que el nombre del título (test_modal_ver_titulo_sin_
    # persona_resuelta_nombre_queda_como_texto): sin persona_destino_id
    # resuelto no hay ficha a la que enlazar Torre/Apto -- se queda como
    # texto plano.
    from app.domain.apartamento_service import resolver_apartamento

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 10", "101")
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
    modal_ver = _segmento_modal(r.text, f"modal-ver-{p.id}")
    assert '<a href="/residentes/' not in modal_ver
    assert "Torre 10 · Apt 101" in modal_ver


def test_modal_ver_identidad_no_usa_telefono_prestado_de_otra_persona(client):
    # issue 101, .scratch/pendientes-cliente -- bug real reportado en vivo
    # (ejemplo real "JESUS VILLALOBOS"/código "J2PY"): un residente SIN
    # teléfono propio se auto-anuncia un paquete (Destinatario.yo_mismo,
    # por WhatsApp) mientras vive en una unidad cuyo Principal SÍ tiene
    # teléfono -- issue 163 usa a propósito el teléfono del Principal como
    # `recipient_phone` (fallback de NOTIFICACIÓN, "siempre debe haber un
    # número responsable"). El bug: el link de IDENTIDAD (título del modal,
    # antes también el nuevo link de Torre/Apto de issue 100) confiaba en
    # ese mismo teléfono para decidir a qué ficha de /residentes enlazar --
    # así que "JESUS VILLALOBOS" enlazaba a la ficha de su Principal
    # (persona real DISTINTA), pareciendo que Jesús "vivía" en la unidad de
    # ella.
    #
    # Seguimiento el mismo día (pedido explícito, "en caso que tenga usuario
    # de whatsapp?"): el ícono de WhatsApp de la fila TAMBIÉN se corrigió --
    # como Jesús SÍ tiene WhatsApp propio (solo le falta teléfono), el ícono
    # debe escribirle a ÉL, no a Angélica -- el teléfono prestado de issue
    # 163 solo debe usarse como último recurso, cuando el destinatario no
    # tiene NINGÚN canal propio (ver el siguiente test).
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 2", "302")
    principal = agregar_ocupante(client.db, apto, "Angelica Arrazola", telefono="3009999999")
    confirmar_ocupante(client.db, principal, staff)  # sin Principal todavía -> se promueve
    jesus = agregar_ocupante(client.db, apto, "Jesus Villalobos", whatsapp_usuario="jesuswa")
    confirmar_ocupante(client.db, jesus, staff)  # ya hay Principal -> se queda confirmado, no-principal
    client.db.commit()

    p = announce(
        client.db,
        anunciante_whatsapp="jesuswa",
        anunciante_nombre="Jesus Villalobos",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    # El fallback de issue 163 sigue intacto EN EL SNAPSHOT: notificación
    # (SMS/OTP) por el teléfono del Principal, no el de Jesús (que no tiene
    # teléfono, solo WhatsApp -- `recipient_phone` nunca guarda un usuario
    # de WhatsApp, ver `telefono_notificacion_de_persona`).
    assert p.recipient_phone == "+573009999999"
    assert p.recipient_name == "JESUS VILLALOBOS"

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_ver = _segmento_modal(r.text, f"modal-ver-{p.id}")
    # El título enlaza a la ficha de JESÚS (su propia Persona, aunque sin
    # teléfono propio) -- NUNCA a la de Angélica, solo porque prestó su
    # teléfono para la notificación.
    assert f'<a href="/residentes/{jesus.persona_id}"' in modal_ver
    assert f'/residentes/{principal.persona_id}' not in modal_ver
    # El ícono de WhatsApp (columna Acciones de la fila, no dentro del
    # modal) ahora escribe al WhatsApp PROPIO de Jesús -- no al teléfono
    # prestado de Angélica.
    assert 'href="https://wa.me/jesuswa?text=' in r.text
    assert "https://wa.me/573009999999" not in r.text


def test_modal_ver_whatsapp_cae_al_contacto_prestado_sin_canal_propio(client):
    # Contraparte del test anterior: si el destinatario identificado NO
    # tiene ningún canal propio (ni teléfono ni WhatsApp), la garantía de
    # issue 163 ("siempre debe haber un número responsable") sigue vigente
    # -- el ícono de WhatsApp cae al contacto prestado del Principal.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 4", "401")
    principal = agregar_ocupante(client.db, apto, "Principal Cuatro", telefono="3004444444")
    confirmar_ocupante(client.db, principal, staff)
    client.db.commit()

    # Ocupante "liviano" -- ni teléfono ni WhatsApp propios (docstring de
    # `agregar_ocupante`) -- solo nombre, sin Persona propia detrás.
    agregar_ocupante(client.db, apto, "Sin Contacto Propio")
    client.db.commit()

    p = announce(
        client.db,
        anunciante_telefono="3004444444",
        anunciante_nombre="Principal Cuatro",
        destinatario=Destinatario.declarado_por_cliente("Sin Contacto Propio"),
    )
    client.db.commit()

    assert p.recipient_name == "SIN CONTACTO PROPIO"
    assert p.recipient_phone == "+573004444444"  # fallback al Principal, issue 163

    r = client.get("/paquetes")
    assert r.status_code == 200
    # Sin Persona propia detrás del nombre, no hay a dónde enlazar la
    # identidad -- pero el WhatsApp SÍ debe seguir siendo el del Principal
    # (único canal real disponible).
    assert 'href="https://wa.me/573004444444?text=' in r.text
    # Issue 305: variante desktop -- `&amp;` (no `&`) porque Jinja autoescapa
    # el atributo `href` (HTML válido, el navegador lo interpreta como `&`)
    # y `web.whatsapp.com/send?phone=` ya trae su propio `?`.
    assert 'href="https://web.whatsapp.com/send?phone=573004444444&amp;text=' in r.text


def test_modal_ver_residentes_de_la_unidad_sigue_al_destinatario_que_se_mudo(client):
    # Tercer seguimiento el mismo día de issue 101 (.scratch/pendientes-
    # cliente, pedido explícito del cliente en vivo, ejemplo real
    # "ANGELICA ARRAZOLA"/"UKT7"). Dos intentos previos, ambos
    # descartados por el cliente:
    #   1. Mostrar la sección igual, con un aviso de texto -- rechazado,
    #      seguía leyéndose como si Jesús (quien ahora vive en la unidad
    #      del snapshot) fuera compañero de Angélica.
    #   2. Ocultar la sección por completo cuando el destinatario ya no
    #      vive en la unidad del snapshot -- rechazado también: "veo que
    #      Angélica y Daniela no aparecen... estas SÍ están asociadas
    #      directamente y deberían aparecer" -- Angélica y Daniela viven
    #      juntas HOY (aunque en una unidad distinta a la del snapshot de
    #      este paquete viejo), esa relación actual sí es real y debía
    #      seguir visible.
    # Fix final: "Residentes de la unidad" sigue la unidad ACTUAL del
    # destinatario identificado (`apartamento_actual_id`), no la del
    # snapshot congelado -- Angélica se mudó de Torre 1 · 302 (donde
    # anunció este paquete) a Torre 2 · 302 (donde vive con Daniela
    # ahora); el paquete UKT7 debe mostrar a Daniela + Angélica (su unidad
    # ACTUAL), no a quien vive hoy en la vieja Torre 1 · 302 (Jesús, sin
    # ninguna relación con Angélica ni con este paquete).
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante, mover_ocupante

    staff = _login_staff(client)
    torre1 = resolver_apartamento(client.db, "TORRE 1", "302")
    torre2 = resolver_apartamento(client.db, "TORRE 2", "302")
    angelica = agregar_ocupante(client.db, torre1, "Angelica Arrazola", telefono="3009999999")
    confirmar_ocupante(client.db, angelica, staff)  # sin Principal todavía -> se promueve
    client.db.commit()

    # Angélica anuncia un paquete para sí misma mientras vive en Torre 1 · 302.
    p = announce(
        client.db,
        "3009999999",
        "Angelica Arrazola",
        Destinatario.yo_mismo(),
    )
    client.db.commit()

    # Después, Angélica se muda a Torre 2 · 302, donde Daniela ya vive --
    # con WhatsApp propio, sin teléfono (mismo perfil de contacto que el
    # bug real reportado en vivo, ejemplo "LAIS HERNANDEZ": una Persona
    # traída SOLO por el batch adicional de la unidad nueva, con
    # `whatsapp_usuario` propio que debe seguir mostrándose).
    daniela = agregar_ocupante(client.db, torre2, "Daniela Arrazola", whatsapp_usuario="daniela.wa")
    confirmar_ocupante(client.db, daniela, staff)  # primera de la unidad -> Principal
    mover_ocupante(client.db, angelica, torre2)
    client.db.commit()

    # El paquete ya está Entregado -- sin excepción por estado (pedido
    # explícito: "en caso que no viva allí NO DEBE APARECER" aplicaba a la
    # dirección VIEJA; acá lo que se prueba es que la unidad NUEVA sí
    # aparece, cerrado o no).
    dom_receive(client.db, p, staff)
    dom_deliver(client.db, p, staff)
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_ver = _segmento_modal(r.text, f"modal-ver-{p.id}")
    assert "Residentes de la unidad" in modal_ver
    assert "DANIELA ARRAZOLA" in modal_ver
    assert "ANGELICA ARRAZOLA" in modal_ver
    # Bug real reportado en vivo (conversación 2026-08-23, ejemplo "LAIS
    # HERNANDEZ"/"RAFAEL TORRES"): Torre 2 · 302 (la unidad NUEVA de
    # Angélica) no es snapshot de NINGÚN paquete de esta página -- sus
    # Ocupantes solo llegan por el batch adicional (`ids_faltantes`), y su
    # `Persona` correspondiente se quedaba sin resolver (`personas` ya
    # estaba armado antes de ese batch) -- el ícono de WhatsApp de Daniela
    # (y su teléfono/email, mismo `r.persona`) desaparecía aunque SÍ
    # tuviera WhatsApp propio.
    assert 'href="https://wa.me/daniela.wa"' in modal_ver
    assert 'href="tel:+573009999999"' in modal_ver  # el teléfono de Angélica también depende de `r.persona`
    # Nadie de la unidad VIEJA (Torre 1 · 302, ahora vacía en este test) --
    # nada que enlistar ahí, y no debe confundirse con la unidad nueva.


def test_modal_ver_sin_aviso_si_el_destinatario_sigue_en_la_unidad(client):
    # Contraparte de los dos tests anteriores: mientras el destinatario NO
    # se haya mudado, "Residentes de la unidad" se queda como estaba -- es
    # el caso normal, la inmensa mayoría de los paquetes.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_staff(client)
    torre2 = resolver_apartamento(client.db, "TORRE 2", "302")
    agregar_ocupante(client.db, torre2, "Angelica Arrazola", telefono="3009999999")
    jesus = agregar_ocupante(client.db, torre2, "Jesus Villalobos", whatsapp_usuario="jesuswa")
    confirmar_ocupante(client.db, jesus, staff)
    client.db.commit()

    p = announce(
        client.db,
        anunciante_whatsapp="jesuswa",
        anunciante_nombre="Jesus Villalobos",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    modal_ver = _segmento_modal(r.text, f"modal-ver-{p.id}")
    assert "Residentes de la unidad" in modal_ver
    assert "ya no vive aquí" not in modal_ver


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
    # Issue 189 (ronda 2, pedido explícito): "para mí mismo" ya no confirma
    # por sí solo con Apartamento resuelto -- Ana debe ser Ocupante real de
    # esa unidad para que la caja se considere resuelta (mismo teléfono con
    # el que anuncia, así `candidatos_correccion` la matchea).
    agregar_ocupante(client.db, apto, "Ana", telefono="3001234567")
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
    # Issue 189 (ronda 2, pedido explícito): "para mí mismo" ya no confirma
    # por sí solo con Apartamento resuelto -- Ana debe ser Ocupante real de
    # esa unidad para que la caja se considere resuelta.
    agregar_ocupante(client.db, apto, "Ana", telefono="3005556666")
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
    # Ids con prefijo "asignar-" (conversación 2026-08-17, picker
    # compartido con Recibir -- `components/_picker_apartamento.html`).
    assert f'id="picker-apto-input-asignar-{p.id}"' in modal_asignar
    assert f'id="picker-torres-posibles-asignar-{p.id}"' in modal_asignar
    assert f'id="picker-resumen-asignar-{p.id}"' in modal_asignar
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


def test_modal_recibir_picker_expone_residentes_por_unidad(client):
    # Issue 127, pedido explícito: al declarar unidad desde Recibir (sin
    # apartamento previo), muestra quiénes viven ya ahí -- mismo dato
    # (`residentes_por_unidad`) que ya usa "Asignar apartamento". Sin
    # aviso aparte de "no calza con el destinatario" (issue 129, pedido
    # explícito posterior: "ya se sobre entiende") -- la lista de
    # nombres ya lo deja claro.
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
    modal_recibir = _segmento_modal(r.text, f"modal-receive-{p.id}")

    assert f'id="picker-apto-input-recibir-{p.id}"' in modal_recibir
    assert f'id="picker-residentes-recibir-{p.id}"' in modal_recibir
    assert "picker-aviso-nombre" not in modal_recibir

    match = re.search(
        rf'id="residentes-unidad-recibir-{p.id}">(.*?)</script>', modal_recibir, re.S
    )
    assert match, "no se encontró el script de residentes por unidad en Recibir"
    residentes = json.loads(match.group(1))
    assert residentes["TORRE 1"]["101"] == ["JESUS VILLALOBOS"]


def test_icono_cambio_reciente_de_apartamento_en_la_lista(client):
    # Issue 165 (.scratch/pendientes-cliente): 🔄 junto a la dirección si el
    # destinatario dejó OTRA unidad hace menos de 30 días -- explica por qué
    # dos Paquetes suyos pueden traer direcciones distintas.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, mover_ocupante

    _login_staff(client)
    apto1 = resolver_apartamento(client.db, "TORRE 1", "101")
    apto2 = resolver_apartamento(client.db, "TORRE 2", "202")
    ocupante1 = agregar_ocupante(client.db, apto1, "Ana", telefono="3001234567")
    mover_ocupante(client.db, ocupante1, apto2)  # Ana deja apto1, se muda a apto2
    client.db.commit()

    _anunciar(client, tel="3001234567", nombre="Ana")

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert "🔄" in r.text
    assert "TORRE 1 · Apto 101" in r.text  # tooltip: la unidad que DEJÓ


def test_sin_cambio_reciente_no_muestra_el_icono(client):
    _login_staff(client)
    _anunciar(client, tel="3001234567", nombre="Ana")  # sin ningún historial de mudanza

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert "🔄" not in r.text


def test_icono_cambio_reciente_ignora_mudanzas_de_mas_de_30_dias(client):
    from datetime import datetime, timedelta, timezone

    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante, mover_ocupante

    _login_staff(client)
    apto1 = resolver_apartamento(client.db, "TORRE 1", "101")
    apto2 = resolver_apartamento(client.db, "TORRE 2", "202")
    ocupante1 = agregar_ocupante(client.db, apto1, "Ana", telefono="3001234567")
    mover_ocupante(client.db, ocupante1, apto2)
    client.db.commit()
    vieja = client.db.query(Ocupante).filter(Ocupante.apartamento_id == apto1.id).one()
    vieja.desvinculado_en = datetime.now(timezone.utc) - timedelta(days=45)
    client.db.commit()

    _anunciar(client, tel="3001234567", nombre="Ana")

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert "🔄" not in r.text


def test_icono_asignar_apartamento_en_anunciado_y_recibido_sin_unidad(client):
    # Issue 135, pedido explícito 2026-08-19: "Anunciado y Recibido" --
    # antes solo ANUNCIADO ofrecía el ícono, RECIBIDO se quedaba con el
    # 🏠 apagado (sin asignar) sin ninguna acción.
    staff = _login_staff(client)
    anunciado = _anunciar(client, tel="3001234567", nombre="Ana")  # sin unidad
    recibido = _anunciar(client, tel="3019999999", nombre="Beto")  # sin unidad
    dom_receive(client.db, recibido, staff)
    entregado = _anunciar(client, tel="3029999999", nombre="Cami")  # sin unidad
    dom_receive(client.db, entregado, staff)
    dom_deliver(client.db, entregado, staff)
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert f'data-open="modal-asignar-apto-{anunciado.id}"' in r.text
    assert f'data-open="modal-asignar-apto-{recibido.id}"' in r.text
    # ENTREGADO sin unidad se queda con el emoji de siempre (nada que ofrecer).
    assert f'data-open="modal-asignar-apto-{entregado.id}"' not in r.text
    assert r.text.count("🏠</button>") == 2  # anunciado + recibido
    # ENTREGADO sin unidad: mismo ícono, apagado (gris claro), sin acción --
    # ya no un emoji compuesto distinto (issue 151).
    # `grayscale` + `opacity-50` (no `text-*`) -- un emoji a color ignora el
    # color de texto CSS, a diferencia de un ícono SVG con
    # `fill="currentColor"` (bug real encontrado en vivo, conversación
    # 2026-08-21: con `text-slate-300` el 🏠 seguía viéndose a todo color).
    assert '<span class="grayscale opacity-50 text-lg leading-none" aria-label="Sin apartamento" title="Sin apartamento">🏠</span>' in r.text


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


def test_asignar_apartamento_exitoso_en_recibido(client):
    # Issue 135, pedido explícito 2026-08-19: mismo camino que ANUNCIADO,
    # ahora también disponible en RECIBIDO.
    from app.domain.apartamento_service import resolver_apartamento

    staff = _login_staff(client)
    resolver_apartamento(client.db, "TORRE 5", "501")
    client.db.commit()
    p = _anunciar(client, nombre="Ana")
    _recibir(client, staff, p)

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


def test_asignar_apartamento_registra_nuevo_residente_en_el_mismo_envio(client):
    """Issue 149 (.scratch/pendientes-cliente) -- mismo caso real que
    issue 148 en Recibir: asignar SOLO la unidad acá nunca creó ningún
    Ocupante (`corregir_apartamento` no toca el padrón). Ahora "+ Nuevo
    residente" va en el mismo POST, sin necesitar una segunda visita a
    Corregir destinatario."""
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante

    _login_staff(client)
    resolver_apartamento(client.db, "TORRE 5", "501")
    client.db.commit()
    p = _anunciar(client, nombre="Ana")
    assert p.snapshot_apartamento is None

    r = client.post(
        f"/paquetes/{p.id}/asignar-apartamento",
        data={
            "torre": "TORRE 5",
            "apartamento": "501",
            "nuevo_ocupante_nombre": "Lais Hernandez",
            "nuevo_ocupante_contacto": "3001112233",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.snapshot_torre == "TORRE 5"
    assert p2.snapshot_apartamento == "501"
    assert p2.recipient_name == "LAIS HERNANDEZ"

    apto = resolver_apartamento(client.db, "TORRE 5", "501")
    ocupante = (
        client.db.query(Ocupante)
        .filter(Ocupante.apartamento_id == apto.id, Ocupante.nombre == "LAIS HERNANDEZ")
        .one()
    )
    # A diferencia de Recibir (que dispara `promover_al_recibir` como parte
    # de la transición ANUNCIADO->RECIBIDO), "Asignar apartamento" no
    # transiciona nada -- el Ocupante queda pending, igual que cualquier
    # otro alta que no pase por recibir un paquete físico.
    assert ocupante.es_principal is False
    assert ocupante.confirmado_en is None


def test_asignar_apartamento_sin_nuevo_residente_autocompleta_al_anunciante(client):
    # Issue 189 (ronda 5): campo "+ Nuevo residente" vacío ya NO significa
    # "sin residente" cuando el destinatario es "para mí mismo" -- se
    # autocompleta con la identidad YA conocida del Anunciante (Ana), tanto
    # en una unidad vacía (este test) como en una poblada.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 5", "501")
    client.db.commit()
    p = _anunciar(client, nombre="Ana")

    r = client.post(
        f"/paquetes/{p.id}/asignar-apartamento",
        data={"torre": "TORRE 5", "apartamento": "501"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/paquetes"
    client.db.expire_all()
    ocupante = client.db.query(Ocupante).filter(Ocupante.apartamento_id == apto.id).one()
    assert ocupante.nombre == "ANA"
    assert ocupante.persona_id is not None
    # "Asignar apartamento" nunca llama a `receive()` -- la promoción
    # automática a Principal (`promover_al_recibir`, ticket 04) es cosa de
    # Recibir, no de esta acción; acá queda "pending" hasta confirmarse.
    assert ocupante.confirmado_en is None


def test_asignar_apartamento_sin_ser_yo_mismo_no_crea_ocupante(client):
    # Guard (issue 189 ronda 5): sin autocompletado posible (destinatario
    # NO es "para mí mismo"), campo vacío sigue sin crear ningún Ocupante --
    # mismo comportamiento de siempre para ese caso (issue 149).
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 5", "501")
    client.db.commit()
    p = announce(
        client.db,
        anunciante_telefono="3044444444",
        anunciante_nombre="Portero",
        destinatario=Destinatario.solo_nombre("Alguien Random"),
    )
    client.db.commit()

    r = client.post(
        f"/paquetes/{p.id}/asignar-apartamento",
        data={"torre": "TORRE 5", "apartamento": "501"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    client.db.expire_all()
    assert client.db.query(Ocupante).filter(Ocupante.apartamento_id == apto.id).count() == 0


def test_asignar_apartamento_ya_ocupante_de_otra_unidad_no_autocompleta_en_silencio(client):
    # Issue 189 (ronda 5): si el Anunciante YA es Ocupante activo de OTRA
    # unidad, el autocompletado no lo muda en silencio -- el camino
    # automático nunca marca "mover_de_otra_unidad", así que cae al mismo
    # rechazo de siempre y el staff debe resolverlo a mano (mismo criterio
    # que la entrada manual de "+ Nuevo residente").
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_staff(client)
    otra_unidad = resolver_apartamento(client.db, "TORRE 9", "901")
    agregar_ocupante(client.db, otra_unidad, "Rafa", telefono="3005554433")
    apto = resolver_apartamento(client.db, "TORRE 5", "501")
    agregar_ocupante(client.db, apto, "Angelica Arrazola", "3001112233")
    client.db.commit()
    p = announce(
        client.db,
        anunciante_telefono="3005554433",
        anunciante_nombre="Rafa",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    r = client.post(
        f"/paquetes/{p.id}/asignar-apartamento",
        data={"torre": "TORRE 5", "apartamento": "501"},
        follow_redirects=False,
    )
    # Mismo rechazo de siempre que ya usa la entrada manual de "+ Nuevo
    # residente" (`mensaje_ya_ocupante_activo`) -- `get_db` comitea al
    # cerrar el request aunque la respuesta sea 400 (no se lanzó ninguna
    # excepción), así que la unidad SÍ queda asignada (información real),
    # pero el destinatario sigue sin confirmar -- el ícono persistente
    # (rondas 1-4) sigue avisando, y el staff puede reintentar activando
    # la opción de mudarlo a mano.
    assert r.status_code == 400
    assert "activa la opción de mudarlo" in r.text
    client.db.expire_all()
    assert client.db.get(Paquete, p.id).snapshot_apartamento == "501"


def test_asignar_apartamento_con_nuevo_residente_no_redirige_a_corregir(client):
    # Guard: cuando SÍ se llenó "+ Nuevo residente" en el mismo envío, la
    # asociación real ya quedó completa acá -- no hace falta el segundo
    # paso, sigue yendo a la lista sola como antes. También sirve de guard
    # para el autocompletado (issue 189 ronda 5): Ana anuncia "para mí
    # misma", pero como el campo SÍ vino lleno (con datos distintos, Lais),
    # esos son los que ganan -- el autocompletado solo entra con el campo
    # vacío.
    from app.domain.apartamento_service import resolver_apartamento

    _login_staff(client)
    resolver_apartamento(client.db, "TORRE 5", "501")
    client.db.commit()
    p = _anunciar(client, nombre="Ana")

    r = client.post(
        f"/paquetes/{p.id}/asignar-apartamento",
        data={
            "torre": "TORRE 5",
            "apartamento": "501",
            "nuevo_ocupante_nombre": "Lais Hernandez",
            "nuevo_ocupante_contacto": "3001112233",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/paquetes"

    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.recipient_name == "LAIS HERNANDEZ"


def test_aviso_desconocido_en_query_no_renderiza_toast(client):
    # Issue 188: `aviso` en la URL es un código controlado, no texto libre --
    # cualquier valor que no sea exactamente "residente_pendiente" (typo,
    # manipulación manual de la URL, etc.) se ignora sin error y sin toast.
    _login_staff(client)
    r = client.get("/paquetes?aviso=algo-inventado")
    assert r.status_code == 200
    assert 'id="toast-aviso"' not in r.text


def test_asignar_apartamento_rechaza_si_ya_esta_entregado(client):
    from app.domain.apartamento_service import resolver_apartamento

    staff = _login_staff(client)
    resolver_apartamento(client.db, "TORRE 5", "501")
    client.db.commit()
    p = _anunciar(client, nombre="Ana")
    _recibir(client, staff, p)
    dom_deliver(client.db, p, staff)
    client.db.commit()

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


# --- Modal "Ver": teléfono+dirección en una línea, chip de duración
# (días+horas), "Actual" retirado del historial (conversación 2026-08-17,
# pedido explícito -- refinado el mismo día para incluir horas, ver
# docstring de `_duracion_transcurrida` en routes/packages.py).


def test_duracion_transcurrida_es_none_si_nunca_se_recibio():
    from app.web.routes.packages import _duracion_transcurrida

    p = Paquete()
    assert _duracion_transcurrida(p) is None


def test_duracion_transcurrida_bajo_24h_muestra_solo_horas():
    from datetime import datetime, timezone

    from app.web.routes.packages import _duracion_transcurrida

    p = Paquete()
    p.received_at = datetime(2026, 8, 10, 6, 0, tzinfo=timezone.utc)
    p.delivered_at = datetime(2026, 8, 10, 22, 0, tzinfo=timezone.utc)
    assert _duracion_transcurrida(p) == "16 horas"


def test_duracion_transcurrida_con_dias_y_horas_entre_recibido_y_entregado():
    from datetime import datetime, timezone

    from app.web.routes.packages import _duracion_transcurrida

    p = Paquete()
    p.received_at = datetime(2026, 8, 10, 6, 0, tzinfo=timezone.utc)
    p.delivered_at = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)  # 3*24 + 4 horas
    assert _duracion_transcurrida(p) == "3 días y 4 horas"


def test_duracion_transcurrida_con_dias_exactos_sin_horas_de_resto():
    from datetime import datetime, timezone

    from app.web.routes.packages import _duracion_transcurrida

    p = Paquete()
    p.received_at = datetime(2026, 8, 10, 6, 0, tzinfo=timezone.utc)
    p.cancelled_at = datetime(2026, 8, 12, 6, 0, tzinfo=timezone.utc)  # exactamente 2 días
    assert _duracion_transcurrida(p) == "2 días"


def test_duracion_transcurrida_singular_un_dia_y_una_hora():
    from datetime import datetime, timezone

    from app.web.routes.packages import _duracion_transcurrida

    p = Paquete()
    p.received_at = datetime(2026, 8, 10, 6, 0, tzinfo=timezone.utc)
    p.delivered_at = datetime(2026, 8, 11, 7, 0, tzinfo=timezone.utc)  # 1 día + 1 hora
    assert _duracion_transcurrida(p) == "1 día y 1 hora"


def test_duracion_transcurrida_cero_horas_si_se_cierra_casi_de_inmediato():
    from datetime import datetime, timezone

    from app.web.routes.packages import _duracion_transcurrida

    p = Paquete()
    p.received_at = datetime(2026, 8, 10, 6, 0, tzinfo=timezone.utc)
    p.delivered_at = datetime(2026, 8, 10, 6, 30, tzinfo=timezone.utc)  # 30 min, trunca a 0
    assert _duracion_transcurrida(p) == "0 horas"


def test_duracion_transcurrida_prioriza_delivered_at_sobre_cancelled_at():
    # Estado de datos que el dominio no produce en la práctica (un paquete no
    # puede estar Entregado Y Cancelado a la vez), pero la función no asume
    # unicidad -- deja explícito el orden de prioridad (mismo orden que
    # `_fecha_ultima_accion`: delivered antes que cancelled).
    from datetime import datetime, timezone

    from app.web.routes.packages import _duracion_transcurrida

    p = Paquete()
    p.received_at = datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc)
    p.delivered_at = datetime(2026, 8, 11, 15, 0, tzinfo=timezone.utc)
    p.cancelled_at = datetime(2026, 8, 20, 15, 0, tzinfo=timezone.utc)
    assert _duracion_transcurrida(p) == "1 día"


def test_duracion_transcurrida_cuenta_en_curso_si_sigue_recibido_sin_cerrar():
    from datetime import datetime, timedelta, timezone

    from app.web.routes.packages import _duracion_transcurrida

    p = Paquete()
    p.received_at = datetime.now(timezone.utc) - timedelta(hours=5)
    resultado = _duracion_transcurrida(p)
    assert resultado in ("4 horas", "5 horas")  # tolerante al segundo exacto de ejecución


def test_modal_ver_no_muestra_chip_de_duracion_en_anunciado(client):
    _login_staff(client)
    p = _anunciar(client, nombre="Ana")
    client.db.commit()

    r = client.get("/paquetes")
    modal_ver = _segmento_modal(r.text, f"modal-ver-{p.id}")
    assert "hora" not in modal_ver  # sin chip: nunca se recibió


def test_modal_ver_muestra_chip_de_duracion_junto_al_badge_en_recibido(client):
    staff = _login_staff(client)
    p = _anunciar(client, nombre="Ana")
    dom_receive(client.db, p, staff)
    client.db.commit()

    r = client.get("/paquetes")
    modal_ver = _segmento_modal(r.text, f"modal-ver-{p.id}")
    assert "0 horas" in modal_ver  # recibido ahora mismo -- 0 horas transcurridas
    idx_badge = modal_ver.index("Recibido")
    idx_chip = modal_ver.index("0 horas")
    assert idx_chip > idx_badge  # el chip va DESPUÉS del badge de estado


def test_chip_de_duracion_usa_el_mismo_color_que_el_badge_de_estado(client):
    # Conversación 2026-08-17, pedido explícito (opción A entre 2
    # presentadas): el chip de duración toma el MISMO rol de color que el
    # badge de Estado de al lado, en vez de quedar en gris plano.
    staff = _login_staff(client)
    p = _anunciar(client, nombre="Ana")
    dom_receive(client.db, p, staff)
    client.db.commit()

    r = client.get("/paquetes")
    modal_ver = _segmento_modal(r.text, f"modal-ver-{p.id}")
    idx_chip = modal_ver.index("0 horas")
    fragmento = modal_ver[max(0, idx_chip - 200) : idx_chip]  # el <span> que lo envuelve
    assert "bg-blue-100" in fragmento  # RECIBIDO = azul, igual que badge(p.estado)


def test_columna_cliente_codigo_de_acceso_enlaza_a_consultar(client):
    _login_staff(client)
    p = _anunciar(client, nombre="Ana")
    client.db.commit()

    r = client.get("/paquetes")
    assert r.status_code == 200
    assert f'href="/consultar?q={p.access_code}"' in r.text


def test_columna_cliente_codigo_de_acceso_tiene_fondo_por_estado(client):
    # Conversación 2026-08-17, pedido explícito posterior a [[107]]: el
    # código de acceso de la columna Cliente también lleva fondo
    # redondeado + color de Estado, mismo `estado_colores` que el chip de
    # duración de [[108]].
    _login_staff(client)
    p = _anunciar(client, nombre="Ana")  # ANUNCIADO = ámbar
    client.db.commit()

    r = client.get("/paquetes")
    idx_link = r.text.index(f'href="/consultar?q={p.access_code}"')
    fragmento = r.text[idx_link : idx_link + 300]
    assert "bg-amber-100" in fragmento
    assert "rounded-full" in fragmento


def test_modal_ver_telefono_y_direccion_comparten_linea_con_separador(client):
    from app.domain.apartamento_service import resolver_apartamento

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 7", "101")
    client.db.commit()
    p = announce(client.db, "3001234567", "Ana", Destinatario.yo_mismo(), apartamento=apto)
    client.db.commit()

    r = client.get("/paquetes")
    modal_ver = _segmento_modal(r.text, f"modal-ver-{p.id}")
    idx_telefono = modal_ver.index("+573001234567")
    idx_separador = modal_ver.index('aria-hidden="true">|<')
    idx_direccion = modal_ver.index("Torre 7 · Apt 101")
    assert idx_telefono < idx_separador < idx_direccion  # una sola línea, en ese orden
    # ya no se repite en la fila de badges (Estado + días), solo en esta línea
    assert modal_ver.count("Torre 7 · Apt 101") == 1


def test_historial_ya_no_muestra_actual_en_el_ultimo_paso(client):
    staff = _login_staff(client)
    p = _anunciar(client, nombre="Ana")
    dom_receive(client.db, p, staff)
    dom_deliver(client.db, p, staff)
    client.db.commit()

    r = client.get("/paquetes")
    modal_ver = _segmento_modal(r.text, f"modal-ver-{p.id}")
    assert "Actual" not in modal_ver


# --- Modal "Cancelar": motivos en lista, botón "Cancelar", sin "Regresar",
# "Otro" revela input (conversación 2026-08-17, pedido explícito). ---


def test_modal_cancelar_muestra_motivos_como_lista_vertical(client):
    _login_staff(client)
    p = _anunciar(client, nombre="Ana")
    client.db.commit()

    r = client.get("/paquetes")
    modal_cancelar = _segmento_modal(r.text, f"modal-cancel-{p.id}")
    assert 'role="radiogroup"' in modal_cancelar
    assert "space-y-2" in modal_cancelar  # apilado vertical, no fila envuelta
    assert "flex flex-wrap gap-2" not in modal_cancelar  # ya no es grupo_chips
    # El catálogo hoy solo tiene "Otro" (`.scratch/motivos-cancelacion-
    # catalogo`, reducido de 4 a 1 motivo genérico en vivo el 2026-09-03).
    assert "Otro" in modal_cancelar


def test_modal_cancelar_boton_dice_cancelar_y_no_tiene_regresar(client):
    _login_staff(client)
    p = _anunciar(client, nombre="Ana")
    client.db.commit()

    r = client.get("/paquetes")
    modal_cancelar = _segmento_modal(r.text, f"modal-cancel-{p.id}")
    assert ">Cancelar</button>" in modal_cancelar
    assert "Confirmar cancelación" not in modal_cancelar
    assert "Regresar" not in modal_cancelar


def test_modal_eliminar_conserva_boton_regresar(client):
    # `mostrar_volver=False` es SOLO del modal Cancelar -- Eliminar paquete
    # comparte el mismo macro (`modal_confirmacion`) y no cambia.
    _login_staff(client)
    p = _anunciar(client, nombre="Ana")
    client.db.commit()

    r = client.get("/paquetes")
    modal_eliminar = _segmento_modal(r.text, f"modal-eliminar-{p.id}")
    assert "Regresar" in modal_eliminar


def test_modal_cancelar_input_de_otro_esta_oculto_por_defecto(client):
    _login_staff(client)
    p = _anunciar(client, nombre="Ana")
    client.db.commit()

    r = client.get("/paquetes")
    modal_cancelar = _segmento_modal(r.text, f"modal-cancel-{p.id}")
    idx_wrap = modal_cancelar.index(f'id="cancelar-otro-wrap-{p.id}"')
    fragmento = modal_cancelar[idx_wrap : idx_wrap + 120]
    assert "hidden" in fragmento
    assert f'name="motivo_otro"' in modal_cancelar
