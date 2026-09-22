# -*- coding: utf-8 -*-
"""
Seam de navegador real — los fallos del escaneo con cámara se ven
(`.scratch/captura-guia-lector-camara`, ticket 05).

Hoy, si el permiso se niega o no hay cámara, queda un cuadro de video negro visible sin mensaje; si el
script del lector no carga, el botón no hace nada. Cada fallo debe mostrar un mensaje claro junto al
botón, ocultar el video y dejar el campo Guía editable.
"""

from app.domain.paquete import EstadoPaquete

from _ayudantes import (
    abrir_modal_recibir,
    anunciar_paquete,
    escanear,
    iniciar_sesion_staff,
    mensaje_de_escaneo,
    paquete_en_bd,
    preparar_entregar,
    preparar_recibir,
    video_de,
)


def _fallo(app_viva, pagina, camara, nombre):
    """Abre Recibir, hace que la cámara rechace con `nombre`, pulsa "Escanear" y devuelve (p, mensaje)."""
    p = preparar_recibir(app_viva, pagina)
    camara.con_error(nombre)
    escanear(pagina, p)
    mensaje_de_escaneo(pagina, p).wait_for(state="visible")
    return p, mensaje_de_escaneo(pagina, p).inner_text()


def _campo_guia_sigue_editable(pagina, p):
    pagina.fill(f"#guia-{p.id}", "")
    pagina.click(f"#guia-{p.id}")
    pagina.keyboard.type("abc-123")
    return pagina.input_value(f"#guia-{p.id}") == "ABC-123"


def test_permiso_de_camara_negado_muestra_un_mensaje_y_oculta_el_video(app_viva, pagina, camara):
    p, mensaje = _fallo(app_viva, pagina, camara, "NotAllowedError")

    assert "permiso" in mensaje.lower()
    assert video_de(pagina, p).is_hidden()
    assert _campo_guia_sigue_editable(pagina, p)


def test_sin_camara_muestra_un_mensaje_distinto_al_del_permiso(app_viva, pagina, camara):
    p, mensaje = _fallo(app_viva, pagina, camara, "NotFoundError")

    assert "no se encontró" in mensaje.lower()
    assert "permiso" not in mensaje.lower()
    assert video_de(pagina, p).is_hidden()
    assert _campo_guia_sigue_editable(pagina, p)


def test_camara_en_uso_muestra_su_propio_mensaje(app_viva, pagina, camara):
    p, mensaje = _fallo(app_viva, pagina, camara, "NotReadableError")

    assert "otra aplicación" in mensaje.lower()
    assert video_de(pagina, p).is_hidden()
    assert _campo_guia_sigue_editable(pagina, p)


def test_cualquier_otro_error_al_iniciar_muestra_un_mensaje_generico(app_viva, pagina, camara):
    p, mensaje = _fallo(app_viva, pagina, camara, "NotSupportedError")

    assert "no se pudo iniciar la cámara" in mensaje.lower()
    assert video_de(pagina, p).is_hidden()
    assert _campo_guia_sigue_editable(pagina, p)


def test_los_cuatro_mensajes_de_fallo_son_distintos(app_viva, pagina, camara):
    mensajes = set()
    p = preparar_recibir(app_viva, pagina)
    for nombre in ("NotAllowedError", "NotFoundError", "NotReadableError", "NotSupportedError"):
        camara.con_error(nombre)
        escanear(pagina, p)
        pagina.wait_for_function(
            "id => { const m = document.querySelector('#modal-receive-' + id + ' .scan-msg'); return !m.hidden && m.textContent; }",
            arg=str(p.id),
        )
        mensajes.add(mensaje_de_escaneo(pagina, p).inner_text())
    assert len(mensajes) == 4


def test_tras_un_fallo_recibir_funciona_con_la_guia_tecleada_a_mano(app_viva, pagina, camara):
    p, _ = _fallo(app_viva, pagina, camara, "NotAllowedError")

    assert _campo_guia_sigue_editable(pagina, p)
    with pagina.expect_navigation():
        pagina.click(f"#modal-receive-{p.id} button[type=submit]")
    recibido = paquete_en_bd(app_viva, p.id)
    assert recibido.estado == EstadoPaquete.RECIBIDO
    assert recibido.guide_number == "ABC-123"


def test_si_el_script_del_lector_no_carga_se_avisa(app_viva, pagina):
    iniciar_sesion_staff(pagina, app_viva)
    p = anunciar_paquete(app_viva)
    # Red caída / recurso bloqueado: ni la precarga ni el clic pueden traer el lector de códigos.
    pagina.route("**/static/vendor/zxing.min.js", lambda ruta: ruta.abort())
    abrir_modal_recibir(pagina, app_viva, p)

    escanear(pagina, p)
    mensaje_de_escaneo(pagina, p).wait_for(state="visible")

    assert "no se pudo cargar el escáner" in mensaje_de_escaneo(pagina, p).inner_text().lower()
    assert video_de(pagina, p).is_hidden()
    assert _campo_guia_sigue_editable(pagina, p)


def test_sin_soporte_de_camara_el_mensaje_existente_sigue_igual(app_viva, pagina):
    iniciar_sesion_staff(pagina, app_viva)
    p = anunciar_paquete(app_viva)
    # Contexto sin HTTPS: `navigator.mediaDevices` no existe.
    pagina.add_init_script(
        "Object.defineProperty(navigator, 'mediaDevices', { value: undefined, configurable: true })"
    )
    abrir_modal_recibir(pagina, app_viva, p)

    escanear(pagina, p)

    assert mensaje_de_escaneo(pagina, p).inner_text() == "Cámara no disponible; escribe la guía a mano."


def test_un_mensaje_de_fallo_anterior_se_limpia_cuando_el_siguiente_intento_arranca(
    app_viva, pagina, camara
):
    p, _ = _fallo(app_viva, pagina, camara, "NotAllowedError")
    assert mensaje_de_escaneo(pagina, p).is_visible()

    camara.con_video()  # el Operador habilitó el permiso: el siguiente intento arranca bien
    escanear(pagina, p)
    video_de(pagina, p).wait_for(state="visible")

    assert mensaje_de_escaneo(pagina, p).is_hidden()


def test_en_confirmar_guia_de_entregar_los_fallos_tambien_se_ven(app_viva, pagina, camara):
    p = preparar_entregar(app_viva, pagina)
    camara.con_error("NotAllowedError")

    pagina.click(f"#modal-deliver-{p.id} .scan-btn")
    mensaje = pagina.locator(f"#modal-deliver-{p.id} .scan-msg")
    mensaje.wait_for(state="visible")

    assert "permiso" in mensaje.inner_text().lower()
    assert pagina.locator(f"#video-confirmar-{p.id}").is_hidden()
