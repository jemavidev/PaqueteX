# -*- coding: utf-8 -*-
"""
Seam de navegador real — grilla de Posición del modal Recibir (`.scratch/posicion-almacenamiento`, tickets 01-02).

Lo que solo un navegador decide: que un toque sobre una Posición SOLO la selecciona (no envía), y que el grupo
requerido impide enviar Recibir sin Posición.
"""

from app.domain.paquete import EstadoPaquete

from _ayudantes import abrir_modal_recibir, anunciar_paquete, espiar_envios, iniciar_sesion_staff, paquete_en_bd


def _boton_posicion(pagina, paquete, codigo):
    return pagina.locator(f'#modal-receive-{paquete.id} input[name="posicion"][value="{codigo}"] + span')


def test_tocar_una_posicion_solo_la_selecciona_y_recibir_la_guarda(app_viva, pagina):
    iniciar_sesion_staff(pagina, app_viva)
    p = anunciar_paquete(app_viva)
    abrir_modal_recibir(pagina, app_viva, p, posicion=None)
    enviados = espiar_envios(pagina)

    _boton_posicion(pagina, p, "62").click()
    _boton_posicion(pagina, p, "11").click()  # otro toque cambia la selección

    assert enviados == []
    assert paquete_en_bd(app_viva, p.id).estado == EstadoPaquete.ANUNCIADO

    with pagina.expect_navigation():
        pagina.click(f"#modal-receive-{p.id} button[type=submit]")

    recibido = paquete_en_bd(app_viva, p.id)
    assert recibido.estado == EstadoPaquete.RECIBIDO
    assert recibido.posicion == "11"


def test_recibir_sin_posicion_no_se_envia(app_viva, pagina):
    iniciar_sesion_staff(pagina, app_viva)
    p = anunciar_paquete(app_viva)
    abrir_modal_recibir(pagina, app_viva, p, posicion=None)
    enviados = espiar_envios(pagina)

    pagina.click(f"#modal-receive-{p.id} button[type=submit]")
    pagina.wait_for_timeout(300)

    assert enviados == []
    assert paquete_en_bd(app_viva, p.id).estado == EstadoPaquete.ANUNCIADO
