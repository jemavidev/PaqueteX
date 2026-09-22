# -*- coding: utf-8 -*-
"""
Seam de navegador real — aviso de guía repetida al recibir
(`.scratch/captura-guia-lector-camara`, ticket 08).

Un envío de varias cajas comparte guía, y una lectura repetida por error también: el aviso ayuda a
distinguirlos, pero NUNCA bloquea (la Guía es una referencia, no una llave).
"""

from app.domain.paquete import EstadoPaquete

from _ayudantes import (
    paquete_en_bd,
    preparar_recibir,
    sembrar_paquete_con_guia,
)


def _aviso(pagina, p):
    return pagina.locator(f"#modal-receive-{p.id} .guia-repetida-msg")


def test_al_escribir_una_guia_que_ya_existe_aparece_el_aviso_y_no_bloquea_recibir(app_viva, pagina):
    p = preparar_recibir(app_viva, pagina)
    sembrar_paquete_con_guia(app_viva, "DUP-1")

    pagina.click(f"#guia-{p.id}")
    pagina.keyboard.type("dup-1")
    _aviso(pagina, p).wait_for(state="visible")

    texto = _aviso(pagina, p).inner_text()
    assert "Ya hay 1 paquete con esta guía" in texto
    assert "recibido" in texto.lower()
    # Nunca bloquea: el botón sigue habilitado y recibir funciona, con la misma guía en los dos paquetes.
    boton = pagina.locator(f"#modal-receive-{p.id} button[type=submit]")
    assert boton.is_enabled()
    with pagina.expect_navigation():
        boton.click()
    recibido = paquete_en_bd(app_viva, p.id)
    assert recibido.estado == EstadoPaquete.RECIBIDO
    assert recibido.guide_number == "DUP-1"


def test_el_aviso_desaparece_si_la_guia_cambia_a_una_que_no_existe(app_viva, pagina):
    p = preparar_recibir(app_viva, pagina)
    sembrar_paquete_con_guia(app_viva, "DUP-1")

    pagina.click(f"#guia-{p.id}")
    pagina.keyboard.type("dup-1")
    _aviso(pagina, p).wait_for(state="visible")

    pagina.keyboard.type("9")  # DUP-19: ya no existe
    _aviso(pagina, p).wait_for(state="hidden")


def test_con_la_guia_vacia_no_hay_aviso(app_viva, pagina):
    p = preparar_recibir(app_viva, pagina)
    sembrar_paquete_con_guia(app_viva, "DUP-1")

    pagina.click(f"#guia-{p.id}")
    pagina.keyboard.type("dup-1")
    _aviso(pagina, p).wait_for(state="visible")

    pagina.fill(f"#guia-{p.id}", "")
    _aviso(pagina, p).wait_for(state="hidden")


def test_una_guia_que_no_existe_no_muestra_aviso(app_viva, pagina):
    p = preparar_recibir(app_viva, pagina)
    sembrar_paquete_con_guia(app_viva, "DUP-1")

    pagina.click(f"#guia-{p.id}")
    pagina.keyboard.type("nueva-77")
    pagina.wait_for_timeout(1000)  # más que la pausa de la consulta

    assert _aviso(pagina, p).is_hidden()


def test_la_lectura_de_la_camara_tambien_dispara_el_aviso(app_viva, pagina, camara):
    p = preparar_recibir(app_viva, pagina)
    sembrar_paquete_con_guia(app_viva, "DUP-1")
    camara.con_video("DUP-1")

    pagina.click(f"#modal-receive-{p.id} .scan-btn")
    _aviso(pagina, p).wait_for(state="visible", timeout=15_000)

    assert "Ya hay 1 paquete con esta guía" in _aviso(pagina, p).inner_text()


def test_si_la_consulta_falla_se_oculta_el_aviso_anterior(app_viva, pagina):
    """Revisión del ticket 08: una sesión vencida hace que la consulta devuelva la página de ingreso (HTML) en
    vez de JSON. El aviso de la guía ANTERIOR no puede quedarse en pantalla como si fuera de la actual."""
    p = preparar_recibir(app_viva, pagina)
    sembrar_paquete_con_guia(app_viva, "DUP-1")

    pagina.click(f"#guia-{p.id}")
    pagina.keyboard.type("dup-1")
    _aviso(pagina, p).wait_for(state="visible")

    pagina.route(
        "**/paquetes/guia-repetida**",
        lambda ruta: ruta.fulfill(status=200, content_type="text/html", body="<html>ingreso</html>"),
    )
    pagina.keyboard.type("9")  # otra guía: la consulta ahora falla

    _aviso(pagina, p).wait_for(state="hidden")
