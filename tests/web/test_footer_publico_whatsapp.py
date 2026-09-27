# -*- coding: utf-8 -*-
"""
Capa web — footer público con sus 4 íconos en todas las vistas (issue 424, `.scratch/pendientes-cliente`).

Anunciar, Consultar, Ayuda y WhatsApp, sin sesión y con el equipo bloqueado por PIN. El número de WhatsApp es el
configurado en Administración → Conjunto (antes solo `/ayuda` lo leía; el resto caía a la variable de entorno).
"""

import re

import pytest

from app.domain import operador_dispositivo_service as ods
from app.domain.configuracion_conjunto_service import actualizar_datos_operativos
from app.domain.staff_service import create_initial_admin

pytestmark = pytest.mark.pin_manual

_PW = "Contrasena1"
_NUMERO = "573001112233"


def _etiquetas_footer_movil(html):
    footer = html[html.index("<footer"):html.index("</footer>")]
    navs = re.findall(r'<nav class="footer-nav-mobile".*?</nav>', footer, re.S)
    assert len(navs) == 1
    return re.findall(r"<span>([^<]+)</span>", navs[0])


@pytest.fixture()
def admin(client, monkeypatch):
    monkeypatch.delenv("WHATSAPP_SOPORTE_NUMERO", raising=False)
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    ods.definir_pin(client.db, admin, "1000")
    actualizar_datos_operativos(
        client.db, horario_lunes_viernes="", horario_sabados="", horario_domingos="", numero_whatsapp=_NUMERO, actor=admin
    )
    client.db.commit()
    return admin


@pytest.mark.parametrize("ruta", ["/anunciar", "/consultar", "/ayuda", "/entrar", "/ingresar", "/otp", "/terminos"])
def test_toda_vista_publica_muestra_los_4_iconos_con_el_whatsapp_configurado(client, admin, ruta):
    html = client.get(ruta).text
    assert _etiquetas_footer_movil(html) == ["Anunciar", "Consultar", "Ayuda", "Whatsapp"]
    assert f"https://wa.me/{_NUMERO}" in html


def test_con_el_equipo_bloqueado_el_footer_es_el_publico(client, admin):
    client.post("/ingresar", data={"email": "admin@club.com", "password": _PW})
    client.post("/bloquear")
    html = client.get("/bloqueo").text
    assert _etiquetas_footer_movil(html) == ["Anunciar", "Consultar", "Ayuda", "Whatsapp"]


def test_sin_numero_configurado_cae_a_la_variable_de_entorno(client, monkeypatch):
    monkeypatch.setenv("WHATSAPP_SOPORTE_NUMERO", "573009998877")
    assert "https://wa.me/573009998877" in client.get("/anunciar").text
