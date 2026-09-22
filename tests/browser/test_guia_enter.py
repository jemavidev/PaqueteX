# -*- coding: utf-8 -*-
"""
Seam de navegador real — una lectura SOLO llena la Guía; recibir lo confirma el Operador con el botón
(`.scratch/captura-guia-lector-camara`, ticket 03).

Un lector que actúa como teclado suele mandar un Enter al final de la lectura. El campo Guía vive
dentro del formulario de Recibir, así que ese Enter lo enviaba y el Paquete quedaba `Recibido` con
Normal/Bueno por defecto, sin fotos, sin Residente y sin pago contra entrega. Cada prueba mira dos
cosas: si salió un POST a /recibir (lo que el navegador envió) y qué hay en la BD.
"""

from app.domain.paquete import EstadoPaquete

from _ayudantes import (
    espiar_envios,
    paquete_en_bd,
    preparar_entregar,
    preparar_recibir,
)


def test_un_enter_tras_la_guia_no_recibe_el_paquete(app_viva, pagina):
    p = preparar_recibir(app_viva, pagina)
    enviados = espiar_envios(pagina)

    pagina.click(f"#guia-{p.id}")
    pagina.keyboard.type("abc-123")  # ráfaga de teclas, como un lector
    pagina.keyboard.press("Enter")  # el terminador que manda el lector
    pagina.wait_for_timeout(500)

    assert enviados == []
    assert paquete_en_bd(app_viva, p.id).estado == EstadoPaquete.ANUNCIADO
    assert pagina.input_value(f"#guia-{p.id}") == "ABC-123"
    assert pagina.locator(f"#modal-receive-{p.id}").is_visible()


def test_un_tab_tras_la_guia_conserva_el_valor_y_no_envia(app_viva, pagina):
    p = preparar_recibir(app_viva, pagina)
    enviados = espiar_envios(pagina)

    pagina.click(f"#guia-{p.id}")
    pagina.keyboard.type("abc-123")
    pagina.keyboard.press("Tab")
    pagina.wait_for_timeout(500)

    assert enviados == []
    assert paquete_en_bd(app_viva, p.id).estado == EstadoPaquete.ANUNCIADO
    assert pagina.input_value(f"#guia-{p.id}") == "ABC-123"


def test_recibir_despues_del_enter_recibe_con_la_guia_leida(app_viva, pagina):
    p = preparar_recibir(app_viva, pagina)

    pagina.click(f"#guia-{p.id}")
    pagina.keyboard.type("abc-123")
    pagina.keyboard.press("Enter")
    with pagina.expect_navigation():
        pagina.click(f"#modal-receive-{p.id} button[type=submit]")

    recibido = paquete_en_bd(app_viva, p.id)
    assert recibido.estado == EstadoPaquete.RECIBIDO
    assert recibido.guide_number == "ABC-123"


def test_un_envio_que_no_viene_del_boton_con_el_foco_en_guia_tampoco_recibe(app_viva, pagina):
    """Segundo nivel de la guardia: aunque el terminador llegue por un camino que no dispare la
    tecla Enter, un envío del formulario con el foco en Guía y sin gesto sobre el botón no recibe."""
    p = preparar_recibir(app_viva, pagina)
    enviados = espiar_envios(pagina)

    pagina.click(f"#guia-{p.id}")
    pagina.keyboard.type("abc-123")
    pagina.evaluate(
        "id => document.querySelector('#modal-receive-' + id + ' form').requestSubmit()", str(p.id)
    )
    pagina.wait_for_timeout(500)

    assert enviados == []
    assert paquete_en_bd(app_viva, p.id).estado == EstadoPaquete.ANUNCIADO


def test_el_boton_recibir_activado_con_el_teclado_si_recibe(app_viva, pagina):
    """La guardia no le quita al Operador el teclado: Enter con el foco en el botón "Recibir" recibe."""
    p = preparar_recibir(app_viva, pagina)

    pagina.click(f"#guia-{p.id}")
    pagina.keyboard.type("abc-123")
    pagina.focus(f"#modal-receive-{p.id} button[type=submit]")
    with pagina.expect_navigation():
        pagina.keyboard.press("Enter")

    assert paquete_en_bd(app_viva, p.id).estado == EstadoPaquete.RECIBIDO


def test_enter_en_otro_campo_del_modal_se_comporta_como_hoy(app_viva, pagina):
    """El ticket solo protege el campo Guía: Enter en el campo de Apartamento sigue enviando."""
    p = preparar_recibir(app_viva, pagina)

    pagina.click(f'[data-picker-apartamento="recibir-{p.id}"]')
    pagina.keyboard.type("302")
    with pagina.expect_request(
        lambda r: r.method == "POST" and r.url.endswith("/recibir")
    ):
        pagina.keyboard.press("Enter")


def test_enter_en_confirmar_guia_de_entregar_no_envia_nada(app_viva, pagina):
    """"Confirmar guía" vive FUERA del formulario de Entregar: la guardia no le hace falta y un
    Enter allí sigue sin entregar el paquete."""
    p = preparar_entregar(app_viva, pagina)
    entregas = []
    pagina.on(
        "request",
        lambda r: entregas.append(r.url) if r.method == "POST" and r.url.endswith("/entregar") else None,
    )

    pagina.click(f"#guia-confirmar-{p.id}")
    pagina.keyboard.type("guia-9")
    pagina.keyboard.press("Enter")
    pagina.wait_for_timeout(500)

    assert entregas == []
    assert paquete_en_bd(app_viva, p.id).estado == EstadoPaquete.RECIBIDO
    assert pagina.input_value(f"#guia-confirmar-{p.id}") == "GUIA-9"
