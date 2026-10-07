"""Issue 432 (.scratch/pendientes-cliente): en escritorio, Chrome solo abre la app instalada de WhatsApp Web si el clic
abre un contexto nuevo (`target="_blank"`) -- confirmado en vivo con el botón de /announce, el único que ya lo tenía.
Todo enlace `web.whatsapp.com` de la app lo lleva."""

import re

from app.domain.paquete_service import Destinatario, announce
from app.domain.persona_service import get_or_create_persona
from app.domain.staff_service import create_initial_admin

_PW = "Contrasena1"


def _login_staff(client):
    create_initial_admin(client.db, "staff@club.com", "Operador", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": "staff@club.com", "password": _PW})


def _enlaces_web_whatsapp(html):
    enlaces = re.findall(r'<a [^>]*href="https://web\.whatsapp\.com/[^"]*"[^>]*>', html)
    assert enlaces, "no hay ningún enlace de WhatsApp de escritorio"
    return enlaces


def _todos_en_pestana_nueva(html):
    for enlace in _enlaces_web_whatsapp(html):
        assert 'target="_blank"' in enlace and 'rel="noopener"' in enlace, enlace


def test_paquetes_whatsapp_de_escritorio_abre_en_pestana_nueva(client, monkeypatch):
    monkeypatch.setenv("WHATSAPP_SOPORTE_NUMERO", "573001112233")  # también el del footer de escritorio
    _login_staff(client)
    announce(client.db, anunciante_telefono="3001234567", anunciante_nombre="Ana", destinatario=Destinatario.yo_mismo())
    client.db.commit()

    html = client.get("/paquetes").text

    assert "web.whatsapp.com/send?phone=573001234567" in html
    _todos_en_pestana_nueva(html)


def test_residentes_whatsapp_de_escritorio_abre_en_pestana_nueva(client):
    _login_staff(client)
    get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    _todos_en_pestana_nueva(client.get("/residentes").text)
    _todos_en_pestana_nueva(client.get("/residentes", params={"vista": "agrupado"}).text)


def test_footer_de_escritorio_whatsapp_abre_en_pestana_nueva(client, monkeypatch):
    monkeypatch.setenv("WHATSAPP_SOPORTE_NUMERO", "573001112233")

    _todos_en_pestana_nueva(client.get("/anunciar").text)


def test_los_enlaces_de_celular_no_cambian(client):
    _login_staff(client)
    get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    # Issue 435: los de celular van directo a api.whatsapp.com/send/ (antes wa.me).
    enlaces = re.findall(r'<a [^>]*href="https://api\.whatsapp\.com/send/[^"]*"[^>]*>', client.get("/residentes").text)
    assert enlaces, "no hay enlaces de WhatsApp de celular"
    for enlace in enlaces:
        assert 'target="_blank"' not in enlace
