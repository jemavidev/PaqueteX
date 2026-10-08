# -*- coding: utf-8 -*-
"""
`/paquetes` en móvil: cada paquete es una tarjeta de 2 niveles con botones grandes (issue 397, `.scratch/pendientes-cliente`).

Variante B del prototipo (rama `prototipo/paquetes-movil-2-lineas`), elegida por Jesús con letra más grande: arriba
código + nombre + "Torre · Apto · Estado · tiempo"; abajo 3 botones de 48 px con ícono y texto (WhatsApp, Llamar,
Recibir/Entregar). Solo en pantallas chicas (`sm:hidden`); la tabla de escritorio no cambia (`hidden sm:block`).
"""

import re

from app.domain.apartamento_service import resolver_apartamento
from app.domain.paquete_lifecycle import deliver, receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin
from app.domain.usuario import Usuario

_PW = "Contrasena1"


def _preparar(client):
    create_initial_admin(client.db, "staff@club.com", "Operador", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": "staff@club.com", "password": _PW})
    staff = client.db.query(Usuario).one()
    apto = resolver_apartamento(client.db, "TORRE 4", "806")
    anunciado = announce(client.db, anunciante_telefono="3001111111", anunciante_nombre="Catalina Parra",
                         destinatario=Destinatario.yo_mismo(), apartamento=apto)
    recibido = announce(client.db, anunciante_telefono="3002222222", anunciante_nombre="Jesus Villalobos",
                        destinatario=Destinatario.yo_mismo())
    entregado = announce(client.db, anunciante_telefono="3003333333", anunciante_nombre="Juan Perez",
                         destinatario=Destinatario.yo_mismo())
    receive(client.db, recibido, staff)
    receive(client.db, entregado, staff)
    deliver(client.db, entregado, staff)
    client.db.commit()
    return anunciado, recibido, entregado


def _tarjeta(html, p):
    m = re.search(rf'<div[^>]*data-tarjeta-paquete="{p.id}"[^>]*>(.*?)<!-- /tarjeta -->', html, re.S)
    assert m, f"no hay tarjeta móvil para {p.access_code}"
    return m.group(0)


def test_cada_paquete_tiene_su_tarjeta_movil_y_la_tabla_queda_solo_en_escritorio(client):
    anunciado, recibido, entregado = _preparar(client)

    html = client.get("/paquetes", params={"estado": ""}).text

    assert re.search(r'<div class="sm:hidden[^"]*"[^>]*data-paquetes-movil', html)
    assert re.search(r'<div class="hidden sm:block[^"]*"[^>]*>\s*<table', html)
    for p in (anunciado, recibido):
        _tarjeta(html, p)
    # Issue 436: la vista por defecto ya no trae ENTREGADO -- se ve con su filtro de Estado.
    _tarjeta(client.get("/paquetes", params={"estado": "ENTREGADO"}).text, entregado)


def test_la_tarjeta_muestra_codigo_nombre_y_apartamento_con_letra_grande(client):
    anunciado, _, _ = _preparar(client)

    tarjeta = _tarjeta(client.get("/paquetes", params={"estado": ""}).text, anunciado)

    assert re.search(rf'href="/consultar\?q={anunciado.access_code}"[^>]*text-base[^>]*>{anunciado.access_code}<|'
                     rf'class="[^"]*text-base[^"]*"[^>]*>{anunciado.access_code}<', tarjeta)
    assert re.search(r'data-open="modal-ver-[^"]+"[^>]*class="[^"]*text-lg[^"]*"', tarjeta) or \
        re.search(r'class="[^"]*text-lg[^"]*"[^>]*data-open="modal-ver-', tarjeta)
    assert "CATALINA PARRA" in tarjeta
    assert re.search(r'<p class="[^"]*text-sm[^"]*">.*Torre 4.*806.*Anunciado', tarjeta, re.S | re.I)  # CSS uppercase


def test_los_tres_botones_grandes_solo_con_icono_segun_el_estado(client):
    anunciado, recibido, entregado = _preparar(client)
    html = client.get("/paquetes", params={"estado": ""}).text

    t_anunciado, t_recibido = (_tarjeta(html, p) for p in (anunciado, recibido))
    # Issue 436: la vista por defecto ya no trae ENTREGADO -- se ve con su filtro de Estado.
    t_entregado = _tarjeta(client.get("/paquetes", params={"estado": "ENTREGADO"}).text, entregado)

    for t in (t_anunciado, t_recibido, t_entregado):
        assert 'aria-label="WhatsApp' in t
        assert 'aria-label="Llamar' in t
        assert t.count("h-12") >= 3  # 48 px, fáciles de tocar
        # Issue 401: solo ícono -- ningún nombre visible dentro de los botones.
        assert not re.search(r"</svg>\s*(WhatsApp|Llamar|Recibir|Entregar|Entregado)\s*<", t)
    assert re.search(rf'data-open="modal-receive-{anunciado.id}"[^>]*aria-label="Recibir', t_anunciado)
    assert re.search(rf'data-open="modal-deliver-{recibido.id}"[^>]*aria-label="Entregar', t_recibido)
    assert "modal-receive-" not in t_entregado and "modal-deliver-" not in t_entregado
    assert 'aria-label="Entregado"' in t_entregado
    assert "tel:+573001111111" in t_anunciado


def test_cuatro_botones_con_el_codigo_y_asignar_apartamento_junto_a_la_posicion(client):
    # Issue 441 (.scratch/pendientes-cliente): abajo WhatsApp · Llamar · Código · Recibir/Entregar (el código se
    # reubica: ya no está arriba); "Asignar apartamento" solo sin apartamento y en Anunciado/Recibido.
    anunciado, recibido, entregado = _preparar(client)
    html = client.get("/paquetes").text
    t_anunciado, t_recibido = _tarjeta(html, anunciado), _tarjeta(html, recibido)

    for t, p in ((t_anunciado, anunciado), (t_recibido, recibido)):
        assert 'grid grid-cols-4' in t
        fila = t[t.index('grid grid-cols-4'):]
        orden = [fila.index('aria-label="WhatsApp'), fila.index('aria-label="Llamar'),
                 fila.index(f'href="/consultar?q={p.access_code}"'), fila.index('data-open="modal-')]
        assert orden == sorted(orden)
        assert t.count(f'href="/consultar?q={p.access_code}"') == 1  # reubicado, no duplicado

    assert 'modal-asignar-apto-' not in t_anunciado  # ya tiene apartamento (TORRE 4 · 806)
    assert f'data-open="modal-asignar-apto-{recibido.id}"' in t_recibido  # sin apartamento, Recibido
    t_entregado = _tarjeta(client.get("/paquetes", params={"estado": "ENTREGADO"}).text, entregado)
    assert 'modal-asignar-apto-' not in t_entregado and "🏠" not in t_entregado
