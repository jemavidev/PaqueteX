# -*- coding: utf-8 -*-
"""
Capa web — `/search` (ticket 01: buscar por access_code + timeline).

Vista PÚBLICA (sin sesión). Comportamiento observable por HTTP: el formulario, el
match exacto por access_code, el timeline armado desde los timestamps de transición
(sin exponer al operador), y "sin resultados" sin error.
"""

from app.domain.paquete_lifecycle import cancel, deliver, receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin

_PW = "Contrasena1"


def _anunciar(client, tel="3001234567", nombre="Ana"):
    p = announce(
        client.db,
        anunciante_telefono=tel,
        anunciante_nombre=nombre,
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()
    return p


def _staff(client):
    u = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    client.db.commit()
    return u


def test_get_search_sin_termino_muestra_el_formulario(client):
    r = client.get("/consultar")
    assert r.status_code == 200
    assert 'name="q"' in r.text


def test_search_no_requiere_sesion(client):
    # A diferencia de /packages, /search es pública.
    r = client.get("/consultar", follow_redirects=False)
    assert r.status_code == 200


def test_buscar_por_access_code_muestra_estado_anunciado(client):
    p = _anunciar(client, nombre="Ana")
    r = client.get("/consultar", params={"q": p.access_code})
    assert r.status_code == 200
    assert "Ana" in r.text
    assert "ANUNCIADO" in r.text
    assert "Anunciado" in r.text  # hito del timeline


def test_timeline_muestra_recibido_y_entregado_tras_transiciones(client):
    staff = _staff(client)
    p = _anunciar(client)
    receive(client.db, p, staff)
    deliver(client.db, p, staff)
    client.db.commit()

    r = client.get("/consultar", params={"q": p.access_code})
    assert r.status_code == 200
    assert "ENTREGADO" in r.text
    assert "Recibido" in r.text and "Entregado" in r.text
    # No expone al operador: el timeline no muestra el nombre del staff que actuó.
    assert staff.nombre not in r.text


def test_paquete_cancelado_muestra_el_motivo(client):
    staff = _staff(client)
    p = _anunciar(client)
    cancel(client.db, p, staff, "NO_RECLAMADO")
    client.db.commit()

    r = client.get("/consultar", params={"q": p.access_code})
    assert r.status_code == 200
    assert "CANCELADO" in r.text
    assert "reclamado" in r.text.lower()  # "No reclamado" (motivo formateado)


def test_termino_sin_coincidencia_da_sin_resultados_sin_error(client):
    r = client.get("/consultar", params={"q": "NO-EXISTE-999"})
    assert r.status_code == 200
    assert "no encontramos" in r.text.lower()


# --------------------------------------------------------------------------- #
# Buscar por teléfono (ticket 02)
# --------------------------------------------------------------------------- #
def test_buscar_por_telefono_lista_lo_anunciado_y_lo_destinado(client):
    ana = _anunciar(client, tel="3001234567", nombre="Ana")
    # Un segundo anuncio a nombre de Ana (destinataria registrada), anunciado por Beto.
    beto_pkg = announce(
        client.db,
        anunciante_telefono="3019999999",
        anunciante_nombre="Beto",
        destinatario=Destinatario.persona_registrada("3001234567"),
    )
    client.db.commit()

    r = client.get("/consultar", params={"q": "3001234567"})
    assert r.status_code == 200
    assert ana.access_code in r.text
    assert beto_pkg.access_code in r.text


def test_buscar_por_telefono_en_otro_formato_encuentra_lo_mismo(client):
    p = _anunciar(client, tel="3001234567", nombre="Ana")
    r = client.get("/consultar", params={"q": "+57 300 123 4567"})
    assert r.status_code == 200
    assert p.access_code in r.text


def test_telefono_sin_paquetes_da_sin_resultados(client):
    r = client.get("/consultar", params={"q": "3009999999"})
    assert r.status_code == 200
    assert "no encontramos" in r.text.lower()
