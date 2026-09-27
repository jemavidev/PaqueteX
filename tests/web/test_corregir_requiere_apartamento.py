# -*- coding: utf-8 -*-
"""
Capa web — "Corregir destinatario" solo con apartamento asignado (issue 419, `.scratch/pendientes-cliente`).

Sin apartamento no hay entre quién elegir: primero se asigna (modal "Asignar apartamento") y después se corrige. El lápiz
"Modificar" (fila y "Ver") sale apagado, el modal no existe y la ruta rechaza la corrección.
"""

import re

from app.domain.apartamento_service import resolver_apartamento
from app.domain.paquete import Paquete
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin

_PW = "Contrasena1"
_AVISO = "Asigna un apartamento primero"


def _login_staff(client):
    create_initial_admin(client.db, "staff@club.com", "Staff", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": "staff@club.com", "password": _PW})


def _anunciar(client, tel, nombre, apartamento=None):
    p = announce(client.db, anunciante_telefono=tel, anunciante_nombre=nombre,
                 destinatario=Destinatario.yo_mismo(), apartamento=apartamento)
    client.db.commit()
    return p


def _lapices(html, p):
    """Los botones/íconos "Modificar" de ese paquete (fila de Acciones y modal Ver)."""
    return re.findall(rf'<(?:button|span)[^>]*(?:Modificar destinatario de {p.recipient_name}|{_AVISO})[^>]*>', html)


def test_sin_apartamento_el_lapiz_esta_apagado_y_no_hay_modal_corregir(client):
    _login_staff(client)
    sin = _anunciar(client, "3001111111", "Ana")

    html = client.get("/paquetes", params={"estado": ""}).text

    assert f'data-open="modal-correct-{sin.id}"' not in html
    assert f'id="modal-correct-{sin.id}"' not in html
    assert html.count(f'title="{_AVISO}"') >= 2  # fila (Acciones) + modal Ver
    assert "Sin apartamento asignado -- asignar apartamento primero" not in html


def test_con_apartamento_el_lapiz_abre_corregir(client):
    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    con = _anunciar(client, "3002222222", "Beto", apartamento=apto)

    html = client.get("/paquetes", params={"estado": ""}).text

    assert html.count(f'data-open="modal-correct-{con.id}"') == 2  # fila + Ver
    assert f'id="modal-correct-{con.id}"' in html


def test_abrir_corregir_por_enlace_directo_sin_apartamento_no_abre_nada(client):
    _login_staff(client)
    sin = _anunciar(client, "3001111111", "Ana")

    html = client.get("/paquetes", params={"corregir": str(sin.id)}).text

    assert f'id="modal-correct-{sin.id}"' not in html


def test_corregir_un_paquete_sin_apartamento_se_rechaza_sin_cambios(client):
    _login_staff(client)
    sin = _anunciar(client, "3001111111", "Ana")

    r = client.post(f"/paquetes/{sin.id}/corregir", data={"recipient_name": "OTRA PERSONA"}, follow_redirects=False)

    assert r.status_code == 400
    assert f"{_AVISO}." in r.text
    client.db.expire_all()
    assert client.db.get(Paquete, sin.id).recipient_name == "ANA"


def test_el_boton_corregir_usa_el_icono_de_persona_con_intercambio(client):
    """Issue 420: persona + flechas ⇄ en vez del lápiz, en la fila y en "Ver" (activo y apagado)."""
    from app.web.icons import ICONOS_NAV

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    con = _anunciar(client, "3002222222", "Beto", apartamento=apto)
    sin = _anunciar(client, "3001111111", "Ana")

    html = client.get("/paquetes", params={"estado": ""}).text

    icono = ICONOS_NAV["cambiar_destinatario"]
    botones_con = re.findall(rf'<button[^>]*data-open="modal-correct-{con.id}"[^>]*>\s*<svg[^>]*>(.*?)</svg>', html, re.S)
    assert len(botones_con) == 2 and all(icono in b for b in botones_con)
    apagados_sin = re.findall(rf'<span[^>]*title="{_AVISO}"[^>]*>\s*<svg[^>]*>(.*?)</svg>', html, re.S)
    assert len(apagados_sin) >= 2 and all(icono in b for b in apagados_sin)
    assert "M13.586 3.586" not in "".join(botones_con + apagados_sin)  # ya no es el lápiz


def test_en_ver_el_icono_apagado_solo_se_ve_en_escritorio(client):
    """Issue 421: en móvil, sin apartamento, el ícono apagado de Corregir no se muestra dentro de "Ver"."""
    _login_staff(client)
    sin = _anunciar(client, "3001111111", "Ana")

    html = client.get("/paquetes", params={"estado": ""}).text

    apagados = re.findall(rf'<span class="([^"]*)"[^>]*title="{_AVISO}"', html)
    assert apagados and all("hidden sm:inline-flex" in clases for clases in apagados)
