# -*- coding: utf-8 -*-
"""
Seam de navegador real — campo Guía del modal Recibir de /paquetes
(`.scratch/captura-guia-lector-camara`, ticket 02).

Estas dos pruebas fijan lo que YA hace el sistema hoy y demuestran que el seam observa de
verdad qué se envió: un teclado real, un modal real y la BD como fuente de verdad.
"""

from app.domain.paquete import EstadoPaquete

from _ayudantes import (
    abrir_modal_recibir,
    anunciar_paquete,
    iniciar_sesion_staff,
    paquete_en_bd,
)


def test_la_guia_se_escribe_en_mayusculas_al_teclear(app_viva, pagina):
    iniciar_sesion_staff(pagina, app_viva)
    p = anunciar_paquete(app_viva)
    abrir_modal_recibir(pagina, app_viva, p)

    pagina.click(f"#guia-{p.id}")
    pagina.keyboard.type("abc-123xyz")

    assert pagina.input_value(f"#guia-{p.id}") == "ABC-123XYZ"
    # Teclear no envía nada: el seam distingue "escrito" de "recibido".
    assert paquete_en_bd(app_viva, p.id).estado == EstadoPaquete.ANUNCIADO


def test_pulsar_recibir_con_una_guia_recibe_el_paquete_y_la_guarda(app_viva, pagina):
    iniciar_sesion_staff(pagina, app_viva)
    p = anunciar_paquete(app_viva)
    abrir_modal_recibir(pagina, app_viva, p)

    pagina.click(f"#guia-{p.id}")
    pagina.keyboard.type("abc-123")
    with pagina.expect_navigation():
        pagina.click(f"#modal-receive-{p.id} button[type=submit]")

    recibido = paquete_en_bd(app_viva, p.id)
    assert recibido.estado == EstadoPaquete.RECIBIDO
    assert recibido.guide_number == "ABC-123"
