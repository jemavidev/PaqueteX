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
    for p in (anunciado, recibido, entregado):
        _tarjeta(html, p)


def test_la_tarjeta_muestra_codigo_nombre_y_apartamento_con_letra_grande(client):
    anunciado, _, _ = _preparar(client)

    tarjeta = _tarjeta(client.get("/paquetes", params={"estado": ""}).text, anunciado)

    assert re.search(rf'href="/consultar\?q={anunciado.access_code}"[^>]*text-base[^>]*>{anunciado.access_code}<|'
                     rf'class="[^"]*text-base[^"]*"[^>]*>{anunciado.access_code}<', tarjeta)
    assert re.search(r'data-open="modal-ver-[^"]+"[^>]*class="[^"]*text-lg[^"]*"', tarjeta) or \
        re.search(r'class="[^"]*text-lg[^"]*"[^>]*data-open="modal-ver-', tarjeta)
    assert "CATALINA PARRA" in tarjeta
    assert re.search(r'<p class="[^"]*text-sm[^"]*">.*Torre 4.*806.*Anunciado', tarjeta, re.S | re.I)  # CSS uppercase


def test_los_tres_botones_grandes_con_texto_segun_el_estado(client):
    anunciado, recibido, entregado = _preparar(client)
    html = client.get("/paquetes", params={"estado": ""}).text

    t_anunciado, t_recibido, t_entregado = (_tarjeta(html, p) for p in (anunciado, recibido, entregado))

    for t in (t_anunciado, t_recibido, t_entregado):
        assert ">WhatsApp<" in t.replace("</svg>", ">")
        assert "Llamar" in t
        assert t.count("h-12") >= 3  # 48 px, fáciles de tocar
    assert re.search(rf'data-open="modal-receive-{anunciado.id}"[^>]*>.*?Recibir', t_anunciado, re.S)
    assert re.search(rf'data-open="modal-deliver-{recibido.id}"[^>]*>.*?Entregar', t_recibido, re.S)
    assert "modal-receive-" not in t_entregado and "modal-deliver-" not in t_entregado
    assert "Entregado" in t_entregado
    assert "tel:+573001111111" in t_anunciado
