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
    assert "Ana" in r.text
    assert "ANUNCIADO" in r.text
    # sin columna de Código: el access_code no se muestra en la lista.
    assert p.access_code not in r.text


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
