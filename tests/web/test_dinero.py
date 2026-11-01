# -*- coding: utf-8 -*-
"""
Capa web — vista "Dinero" (`/dinero`, pedido del cliente 2026-11-01): cualquier rol de staff, sin sesión a
`/ingresar`, filtros en la URL, fragmento de búsqueda en vivo, totales y el encabezado del cliente filtrado.
"""

import re

from app.domain.persona_service import get_or_create_persona
from app.domain.saldo_contra_entrega_service import registrar_movimiento_saldo
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario, Usuario

_PW = "Contrasena1"


def _login(client, rol=RolUsuario.OPERADOR):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    email = "admin@club.com"
    if rol == RolUsuario.OPERADOR:
        email = "op@club.com"
        create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})
    return client.db.query(Usuario).filter(Usuario.email == email).one()


def _movimientos(client, staff):
    ana = get_or_create_persona(client.db, "3001234567", "Ana")
    beto = get_or_create_persona(client.db, "3007654321", "Beto")
    registrar_movimiento_saldo(client.db, ana.id, 5000, staff)
    registrar_movimiento_saldo(client.db, beto.id, -2000, staff)
    client.db.commit()
    return ana, beto


def _totales(html):
    return " ".join(re.search(r"data-totales-dinero>(.*?)</p>", html, re.S).group(1).split())


def test_operador_ve_la_vista_con_en_caja(client):
    staff = _login(client)
    _movimientos(client, staff)

    r = client.get("/dinero")

    assert r.status_code == 200
    assert "<html" in r.text
    assert 'id="resultados-dinero"' in r.text
    totales = _totales(r.text)
    assert "2</strong> movimientos" in totales
    assert "En caja" in totales and "$3,000" in totales


def test_admin_tambien_la_ve(client):
    _login(client, RolUsuario.ADMIN)
    assert client.get("/dinero").status_code == 200


def test_sin_sesion_redirige_a_login(client):
    r = client.get("/dinero", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_enlace_en_la_navegacion_del_staff(client):
    _login(client)
    html = client.get("/paquetes").text
    assert 'href="/dinero"' in html


def test_filtros_en_la_url_y_neto_en_vez_de_en_caja(client):
    staff = _login(client)
    _movimientos(client, staff)

    r = client.get("/dinero", params={"tipo": "deposito"})

    totales = _totales(r.text)
    assert "1</strong> movimiento " in totales
    assert "Neto" in totales and "En caja" not in totales
    assert 'data-tipo-fila="ajuste"' not in r.text
    assert 'data-tipo-fila="deposito"' in r.text
    # La píldora del tipo elegido queda marcada y el valor viaja en el campo oculto del formulario.
    assert 'name="tipo" value="deposito" data-filtro' in r.text
    assert re.search(r'data-filtro-pildora="tipo" data-valor="deposito"[^>]*aria-pressed="true"', r.text)


def test_busqueda_en_vivo_responde_solo_el_fragmento(client):
    staff = _login(client)
    _movimientos(client, staff)

    r = client.get("/dinero", params={"q": "beto"}, headers={"X-Requested-With": "fetch"})

    assert r.status_code == 200
    assert "<html" not in r.text
    assert "BETO" in r.text and "ANA" not in r.text
    assert "data-totales-dinero" in r.text


def test_filtro_por_cliente_muestra_su_saldo_actual(client):
    staff = _login(client)
    _, beto = _movimientos(client, staff)

    html = client.get("/dinero").text
    # Tocar el nombre filtra por ese cliente.
    assert f'href="/dinero?cliente={beto.id}"' in html

    r = client.get("/dinero", params={"cliente": str(beto.id)})
    assert f'data-cliente-filtrado="{beto.id}"' in r.text
    assert re.search(r"data-saldo-cliente>debe \$2,000<", r.text)
    assert "ANA" not in r.text


def test_valores_invalidos_no_filtran(client):
    staff = _login(client)
    _movimientos(client, staff)

    r = client.get("/dinero", params={"tipo": "inventado", "desde": "ayer", "operador": "x", "cliente": "y"})

    assert r.status_code == 200
    assert "2</strong> movimientos" in _totales(r.text)


def test_paginacion_conserva_los_filtros(client):
    staff = _login(client)
    ana = get_or_create_persona(client.db, "3001234567", "Ana")
    for _ in range(25):
        registrar_movimiento_saldo(client.db, ana.id, 1000, staff)
    client.db.commit()

    r = client.get("/dinero", params={"tipo": "deposito", "q": "ana"})

    assert "/dinero?q=ana&amp;tipo=deposito&amp;pagina=2" in r.text


def test_las_paginas_de_saldo_enlazan_a_dinero(client):
    _login(client)
    for url in ("/residentes/saldos-contra-entrega", "/residentes/movimientos-saldo-contra-entrega"):
        assert 'href="/dinero"' in client.get(url).text
