# -*- coding: utf-8 -*-
"""
Seam de navegador real — capa de Bloqueo por inactividad (`.scratch/pin-operador-dispositivo`, ticket 04).

Reloj del navegador controlado (`page.clock`): se adelanta el tiempo sin esperar de verdad. Inactividad configurada al
mínimo (60 s). La misma persona que desbloquea sigue donde iba (formulario a medias intacto); otra persona recibe la
vista recargada limpia.
"""

from app.domain import operador_dispositivo_service as ods
from app.domain.configuracion_conjunto_service import actualizar_seguridad_sesion
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

from _ayudantes import PASSWORD_STAFF, abrir_modal_recibir, anunciar_paquete, volver_a_iniciar_sesion


def _sembrar(app_viva):
    admin = create_initial_admin(app_viva.db, "admin@club.com", "Admin", PASSWORD_STAFF)
    otro = create_staff(app_viva.db, admin, "otro@club.com", "Otro", PASSWORD_STAFF, RolUsuario.OPERADOR)
    ods.definir_pin(app_viva.db, admin, "1357")
    ods.definir_pin(app_viva.db, otro, "2468")
    actualizar_seguridad_sesion(app_viva.db, segundos_inactividad=60, dias_registro_dispositivo=15, actor=admin)
    app_viva.db.commit()


def _capa(pagina):
    return pagina.locator("#capa-bloqueo")


def _digitar(pagina, pin):
    for digito in pin:
        pagina.locator(f'#capa-bloqueo-form [data-digito="{digito}"]').click()


def _preparar(app_viva, pagina, *emails):
    _sembrar(app_viva)
    for email in emails or ("admin@club.com",):
        volver_a_iniciar_sesion(pagina, app_viva, email)
    pagina.clock.install()


def test_la_capa_aparece_al_vencer_la_inactividad(app_viva, pagina):
    _preparar(app_viva, pagina)
    pagina.goto(f"{app_viva.url}/paquetes")
    assert _capa(pagina).is_hidden()
    pagina.clock.fast_forward("00:59")
    assert _capa(pagina).is_hidden()
    pagina.clock.fast_forward("00:02")
    _capa(pagina).wait_for(state="visible")


def test_las_teclas_y_los_toques_posponen_el_bloqueo(app_viva, pagina):
    _preparar(app_viva, pagina)
    pagina.goto(f"{app_viva.url}/paquetes")
    pagina.clock.fast_forward("00:50")
    pagina.keyboard.press("Shift")
    pagina.clock.fast_forward("00:50")
    pagina.mouse.click(5, 300)
    pagina.clock.fast_forward("00:50")
    assert _capa(pagina).is_hidden()
    pagina.clock.fast_forward("00:15")
    _capa(pagina).wait_for(state="visible")


def test_la_misma_persona_conserva_el_formulario_a_medias(app_viva, pagina):
    _preparar(app_viva, pagina)
    paquete = anunciar_paquete(app_viva)
    abrir_modal_recibir(pagina, app_viva, paquete)
    campo_guia = pagina.locator(f"#modal-receive-{paquete.id} input[name=guide_number]")
    campo_guia.fill("GUIA-A-MEDIAS")

    pagina.clock.fast_forward("01:05")
    _capa(pagina).wait_for(state="visible")
    _digitar(pagina, "1357")
    _capa(pagina).wait_for(state="hidden")

    assert pagina.locator(f"#modal-receive-{paquete.id}").is_visible()
    assert campo_guia.input_value() == "GUIA-A-MEDIAS"


def test_otra_persona_recibe_la_vista_limpia(app_viva, pagina):
    _preparar(app_viva, pagina, "otro@club.com", "admin@club.com")  # ambos registrados; activo: admin
    paquete = anunciar_paquete(app_viva)
    abrir_modal_recibir(pagina, app_viva, paquete)
    pagina.locator(f"#modal-receive-{paquete.id} input[name=guide_number]").fill("GUIA-DEL-ADMIN")

    pagina.clock.fast_forward("01:05")
    _capa(pagina).wait_for(state="visible")
    with pagina.expect_navigation():
        _digitar(pagina, "2468")

    assert pagina.locator(f"#modal-receive-{paquete.id}").is_hidden()
    assert _capa(pagina).is_hidden()


def test_un_pin_equivocado_deja_la_capa_con_el_mensaje(app_viva, pagina):
    _preparar(app_viva, pagina)
    pagina.goto(f"{app_viva.url}/paquetes")
    pagina.clock.fast_forward("01:05")
    _capa(pagina).wait_for(state="visible")
    _digitar(pagina, "0000")
    pagina.wait_for_function(
        "() => document.querySelector('#capa-bloqueo-form [data-teclado-pin-error]').textContent.includes('PIN incorrecto')"
    )
    assert _capa(pagina).is_visible()


def test_la_cola_de_fotos_sigue_subiendo_con_la_capa_puesta(app_viva, pagina):
    """Ticket 05: con la capa de bloqueo visible, una foto que quedó en la cola del equipo termina de subir."""
    import io
    import time

    from PIL import Image

    from app.domain.paquete import EstadoPaquete
    from app.domain.paquete_foto import PaqueteFoto

    from _ayudantes import paquete_en_bd

    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), color=(120, 90, 40)).save(buffer, format="JPEG")

    _preparar(app_viva, pagina)
    paquete = anunciar_paquete(app_viva)
    abrir_modal_recibir(pagina, app_viva, paquete)
    colgadas = []
    pagina.route("**/paquetes/*/fotos", lambda ruta: colgadas.append(ruta))  # la subida progresiva no responde
    pagina.set_input_files(f"#foto-input-recibir-{paquete.id}",
                           files=[{"name": "etiqueta.jpg", "mimeType": "image/jpeg", "buffer": buffer.getvalue()}])
    pagina.wait_for_function("() => document.body.innerText.includes('Subiendo')")
    with pagina.expect_navigation():
        pagina.locator(f"#modal-receive-{paquete.id} button[type=submit]").click()
    assert paquete_en_bd(app_viva, paquete.id).estado == EstadoPaquete.RECIBIDO

    pagina.clock.fast_forward("01:05")
    _capa(pagina).wait_for(state="visible")

    pagina.unroute("**/paquetes/*/fotos")
    for ruta in colgadas:
        try:
            ruta.abort()
        except Exception:
            pass
    pagina.evaluate("() => window.paqueteXColaFotos.procesar(true)")

    limite = time.time() + 10
    while time.time() < limite:
        app_viva.db.expire_all()
        if app_viva.db.query(PaqueteFoto).filter(PaqueteFoto.paquete_id == paquete.id).count() == 1:
            break
        time.sleep(0.2)
    else:
        raise AssertionError("la foto de la cola no se subió con el equipo bloqueado")
    assert _capa(pagina).is_visible()
