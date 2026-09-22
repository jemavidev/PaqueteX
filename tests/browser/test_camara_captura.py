# -*- coding: utf-8 -*-
"""
Seam de navegador real — mejor captura con la cámara
(`.scratch/captura-guia-lector-camara`, ticket 07).

Sin restricciones, Chrome entrega 640x480 (poco para un código lineal pequeño) y el bucle de lectura
reintenta sin pausa mientras no hay código a la vista (CPU y batería de sobra). Estas pruebas miran lo
que el escáner le pide al navegador, la linterna, el ritmo de los intentos y la regla de los 50
caracteres para lo que lee la cámara. Sin restringir formatos: se lee QR además de códigos lineales.
"""

import time

from _ayudantes import (
    escanear,
    esperar_sin_flujos_vivos,
    mensaje_de_escaneo,
    preparar_entregar,
    preparar_recibir,
    video_de,
)


def _linterna(pagina, p):
    return pagina.locator(f"#modal-receive-{p.id} .scan-torch")


def _campo(pagina, p):
    return pagina.input_value(f"#guia-{p.id}")


def test_pide_la_camara_trasera_con_resolucion_ideal_de_1280x720(app_viva, pagina, camara):
    p = preparar_recibir(app_viva, pagina)

    escanear(pagina, p)
    video_de(pagina, p).wait_for(state="visible")

    restricciones = camara.estado()["restricciones"][0]["video"]
    assert restricciones["facingMode"] == "environment"
    # `ideal`, no `exact`: una cámara que no da 1280x720 igual arranca (con lo más cercano).
    assert restricciones["width"] == {"ideal": 1280}
    assert restricciones["height"] == {"ideal": 720}


def test_la_linterna_no_aparece_si_la_camara_no_la_reporta(app_viva, pagina, camara):
    p = preparar_recibir(app_viva, pagina)

    escanear(pagina, p)
    video_de(pagina, p).wait_for(state="visible")
    pagina.wait_for_timeout(500)

    assert _linterna(pagina, p).count() == 0


def test_con_linterna_disponible_el_boton_la_enciende_la_apaga_y_desaparece_al_terminar(
    app_viva, pagina, camara
):
    p = preparar_recibir(app_viva, pagina)
    camara.con_video(linterna=True)

    escanear(pagina, p)
    _linterna(pagina, p).wait_for(state="visible")

    _linterna(pagina, p).click()
    pagina.wait_for_function("() => window.__camara.aplicadas.length === 1")
    assert camara.estado()["aplicadas"][-1] == {"advanced": [{"torch": True}]}

    _linterna(pagina, p).click()
    pagina.wait_for_function("() => window.__camara.aplicadas.length === 2")
    assert camara.estado()["aplicadas"][-1] == {"advanced": [{"torch": False}]}

    # Encendida otra vez y luego "Detener": el botón desaparece y la linterna queda apagada.
    _linterna(pagina, p).click()
    pagina.wait_for_function("() => window.__camara.aplicadas.length === 3")
    pagina.click(f"#modal-receive-{p.id} .scan-stop")
    esperar_sin_flujos_vivos(pagina)

    assert _linterna(pagina, p).count() == 0
    assert camara.estado()["aplicadas"][-1] == {"advanced": [{"torch": False}]}


def test_los_reintentos_de_decodificacion_estan_acotados_sin_un_codigo_a_la_vista(
    app_viva, pagina, camara
):
    """Cada intento de decodificar lee el fotograma con `getImageData`: se cuentan durante 2 s con la cámara
    apuntando a nada. Sin pausa entre intentos son cientos; con ella, un puñado."""
    pagina.add_init_script(
        """
        window.__intentos = 0;
        const original = CanvasRenderingContext2D.prototype.getImageData;
        CanvasRenderingContext2D.prototype.getImageData = function (...args) {
          window.__intentos++;
          return original.apply(this, args);
        };
        """
    )
    p = preparar_recibir(app_viva, pagina)

    escanear(pagina, p)
    pagina.wait_for_function("() => window.__intentos > 0")
    pagina.evaluate("() => { window.__intentos = 0; }")
    pagina.wait_for_timeout(2000)
    intentos = pagina.evaluate("() => window.__intentos")

    assert 1 <= intentos <= 30, f"{intentos} intentos en 2 s (esperado: acotado, máx. ~15 por segundo)"


def test_con_un_codigo_a_la_vista_la_lectura_sigue_siendo_rapida(app_viva, pagina, camara):
    p = preparar_recibir(app_viva, pagina)
    camara.con_video("gu-77")

    inicio = time.monotonic()
    escanear(pagina, p)
    pagina.wait_for_function(
        "id => document.getElementById('guia-' + id).value === 'gu-77'", arg=str(p.id), timeout=15_000
    )

    assert time.monotonic() - inicio < 6  # a la par de un escáner, no de una cámara lenta
    assert _campo(pagina, p) == "gu-77"  # tal como se leyó: la normalización es del servidor al guardar


def test_una_lectura_de_exactamente_50_se_escribe_y_una_de_51_no(app_viva, pagina, camara):
    p = preparar_recibir(app_viva, pagina)

    camara.con_video("a" * 50)
    escanear(pagina, p)
    pagina.wait_for_function(
        "id => document.getElementById('guia-' + id).value.length === 50", arg=str(p.id), timeout=15_000
    )
    assert mensaje_de_escaneo(pagina, p).is_hidden()

    pagina.fill(f"#guia-{p.id}", "")
    camara.con_video("b" * 51)
    escanear(pagina, p)
    mensaje_de_escaneo(pagina, p).wait_for(state="visible", timeout=15_000)

    assert _campo(pagina, p) == ""  # no se escribió
    assert "51" in mensaje_de_escaneo(pagina, p).inner_text()
    assert "50" in mensaje_de_escaneo(pagina, p).inner_text()
    esperar_sin_flujos_vivos(pagina)  # la cámara se apagó como con cualquier lectura
    assert video_de(pagina, p).is_hidden()
    assert pagina.locator(f"#modal-receive-{p.id} .scan-btn").is_enabled()


def test_en_confirmar_guia_de_entregar_una_lectura_de_mas_de_50_tampoco_se_escribe(
    app_viva, pagina, camara
):
    p = preparar_entregar(app_viva, pagina)
    camara.con_video("c" * 60)

    pagina.click(f"#modal-deliver-{p.id} .scan-btn")
    mensaje = pagina.locator(f"#modal-deliver-{p.id} .scan-msg")
    mensaje.wait_for(state="visible", timeout=15_000)

    assert pagina.input_value(f"#guia-confirmar-{p.id}") == ""
    assert "60" in mensaje.inner_text()
    esperar_sin_flujos_vivos(pagina)


def test_la_regla_de_50_cuenta_como_el_servidor_tras_pasar_a_mayusculas(app_viva, pagina, camara):
    """Revisión del ticket 07: el servidor cuenta la guía DESPUÉS de pasarla a mayúsculas ("ß" pasa a "SS"),
    la cámara escribe lo leído tal cual. 26 "ß" son 26 caracteres crudos pero 52 normalizados: se rechaza."""
    p = preparar_recibir(app_viva, pagina)
    camara.con_video("ß" * 26)

    escanear(pagina, p)
    mensaje_de_escaneo(pagina, p).wait_for(state="visible", timeout=15_000)

    assert _campo(pagina, p) == ""  # no se escribió
    assert "52" in mensaje_de_escaneo(pagina, p).inner_text()
