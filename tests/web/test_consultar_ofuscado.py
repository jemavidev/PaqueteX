# -*- coding: utf-8 -*-
"""
`/consultar`: un paquete Entregado o Cancelado hace más de 15 días se muestra ofuscado a quien no es staff
(issue 387, `.scratch/pendientes-cliente`).

Sin sesión, `/consultar` mostraba siempre el nombre completo, el teléfono completo, el apartamento, las fotos, la guía
y los nombres del staff -- también de todo el historial. Pedido de Jesús: los Anunciados y Recibidos se consultan
siempre igual; los Entregados/Cancelados, pasados 15 días en ese estado, se siguen encontrando pero ofuscados. El
staff ve todo siempre. La lógica de códigos y de búsqueda no cambia.
"""

from datetime import datetime, timedelta, timezone

from app.domain.paquete_foto_service import agregar_foto_desde_url
from app.domain.paquete_lifecycle import cancel, deliver, receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin

_PW = "Contrasena1"
_FOTO = "/static/fotos-recibidas/foto-de-prueba.jpg"
_NOTA = "Por privacidad"


def _staff(client):
    u = create_initial_admin(client.db, "admin@club.com", "Portero Juan", _PW)
    client.db.commit()
    return u


def _paquete(client, staff):
    """Recibido con guía y foto, para una destinataria con nombre y teléfono reconocibles."""
    p = announce(client.db, anunciante_telefono="3005551234", anunciante_nombre="Catalina Parra",
                 destinatario=Destinatario.yo_mismo())
    receive(client.db, p, staff, "GUIA-777")
    agregar_foto_desde_url(client.db, p, _FOTO)
    client.db.commit()
    return p


def _entregado_hace(client, staff, dias):
    p = _paquete(client, staff)
    deliver(client.db, p, staff)
    p.delivered_at = datetime.now(timezone.utc) - timedelta(days=dias)
    client.db.commit()
    return p


def _consultar(client, p):
    return client.get("/consultar", params={"q": p.access_code}).text


def _assert_ofuscado(html):
    assert _NOTA in html
    assert "CATALINA PARRA" not in html
    assert "C. P." in html
    assert "3005551234" not in html and "+573005551234" not in html
    assert "1234" in html  # últimos 4 dígitos, para que el dueño lo reconozca
    assert "GUIA-777" not in html
    assert _FOTO not in html
    assert "Portero Juan" not in html


def _assert_completo(html):
    assert _NOTA not in html
    assert "CATALINA PARRA" in html
    assert "+573005551234" in html
    assert "GUIA-777" in html
    assert _FOTO in html


def test_entregado_hace_mas_de_15_dias_sin_sesion_se_muestra_ofuscado(client):
    p = _entregado_hace(client, _staff(client), 16)

    html = _consultar(client, p)

    _assert_ofuscado(html)
    assert "Entregado" in html  # el estado sí se ve


def test_entregado_hace_menos_de_15_dias_sin_sesion_se_muestra_completo(client):
    p = _entregado_hace(client, _staff(client), 10)

    _assert_completo(_consultar(client, p))


def test_cancelado_hace_mas_de_15_dias_sin_sesion_se_muestra_ofuscado(client):
    staff = _staff(client)
    p = _paquete(client, staff)
    cancel(client.db, p, staff, "Anuncio erróneo")
    p.cancelled_at = datetime.now(timezone.utc) - timedelta(days=20)
    client.db.commit()

    _assert_ofuscado(_consultar(client, p))


def test_recibido_hace_mucho_sin_sesion_se_sigue_mostrando_completo(client):
    """Anunciados y Recibidos se consultan siempre igual, sin importar cuánto tiempo lleven."""
    p = _paquete(client, _staff(client))
    p.received_at = datetime.now(timezone.utc) - timedelta(days=40)
    client.db.commit()

    _assert_completo(_consultar(client, p))


def test_el_staff_ve_completo_un_entregado_viejo(client):
    staff = _staff(client)
    p = _entregado_hace(client, staff, 90)
    client.post("/ingresar", data={"email": "admin@club.com", "password": _PW})

    _assert_completo(_consultar(client, p))
