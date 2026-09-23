# -*- coding: utf-8 -*-
"""
Seam de navegador real — "Recibir" no espera fotos: lo pendiente pasa a la cola del equipo y se sube solo después
(issue 389, `.scratch/pendientes-cliente`).
"""

import io

from PIL import Image

from app.domain.paquete import EstadoPaquete
from app.domain.paquete_foto import PaqueteFoto

from _ayudantes import abrir_menu_de_cuenta, iniciar_sesion_staff, paquete_en_bd, preparar_recibir


def _jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), color=(120, 90, 40)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _fotos(app_viva, p):
    app_viva.db.expire_all()
    return app_viva.db.query(PaqueteFoto).filter(PaqueteFoto.paquete_id == p.id).all()


def _cola(pagina):
    return pagina.evaluate("""() => new Promise(res => {
        const r = indexedDB.open('paquetex-fotos', 1);
        r.onupgradeneeded = () => r.result.createObjectStore('cola', {keyPath: 'id', autoIncrement: true});
        r.onsuccess = () => { const g = r.result.transaction('cola').objectStore('cola').getAll();
                              g.onsuccess = () => { res(g.result.length); r.result.close(); }; };
    })""")


def test_recibir_no_espera_una_foto_colgada_y_la_cola_la_sube_despues(app_viva, pagina):
    p = preparar_recibir(app_viva, pagina)
    colgadas = []
    # La subida progresiva de la foto nunca responde (red lenta o caída).
    pagina.route("**/paquetes/*/fotos", lambda ruta: colgadas.append(ruta))
    pagina.set_input_files(f"#foto-input-recibir-{p.id}",
                           files=[{"name": "etiqueta.jpg", "mimeType": "image/jpeg", "buffer": _jpeg()}])
    pagina.wait_for_function("() => document.body.innerText.includes('Subiendo')")

    with pagina.expect_navigation():
        pagina.locator(f"#modal-receive-{p.id} button[type=submit]").click()

    assert paquete_en_bd(app_viva, p.id).estado == EstadoPaquete.RECIBIDO  # recibido sin esperar la foto
    assert _fotos(app_viva, p) == []
    assert _cola(pagina) == 1  # la foto quedó en la cola del equipo
    abrir_menu_de_cuenta(pagina)
    aviso = pagina.locator("#site-header [data-fotos-pendientes]")
    aviso.wait_for(state="visible")
    assert aviso.locator("[data-fotos-pendientes-n]").inner_text() == "1"  # solo el número (issue 390, ronda 2)

    # Vuelve la conexión: lo que quedó colgado falla (como al caerse la red) y la cola reintenta enseguida.
    pagina.unroute("**/paquetes/*/fotos")
    for ruta in colgadas:
        try:
            ruta.abort()
        except Exception:
            pass  # la de la página anterior ya no existe
    pagina.evaluate("() => new Promise(r => setTimeout(r, 300))")
    pagina.evaluate("() => window.paqueteXColaFotos.procesar(true)")
    aviso.wait_for(state="hidden")

    assert len(_fotos(app_viva, p)) == 1
    assert _cola(pagina) == 0


def test_una_foto_ya_subida_viaja_como_url_y_la_cola_queda_vacia(app_viva, pagina):
    p = preparar_recibir(app_viva, pagina)
    pagina.set_input_files(f"#foto-input-recibir-{p.id}",
                           files=[{"name": "etiqueta.jpg", "mimeType": "image/jpeg", "buffer": _jpeg()}])
    pagina.wait_for_function("() => document.querySelector('[aria-label=\"Ya subida\"]')")

    with pagina.expect_navigation():
        pagina.locator(f"#modal-receive-{p.id} button[type=submit]").click()

    assert len(_fotos(app_viva, p)) == 1
    assert _cola(pagina) == 0


def test_sin_fotos_pendientes_el_aviso_no_se_ve_y_vive_debajo_de_lector(app_viva, pagina):
    """Issue 390: dentro del menú de cuenta, justo debajo de "Lector" -- y con 0 fotos, oculto de verdad (antes la
    clase `inline-flex` le ganaba a `hidden` y se veía "0 fotos por subir")."""
    iniciar_sesion_staff(pagina, app_viva)
    pagina.goto(f"{app_viva.url}/paquetes")
    abrir_menu_de_cuenta(pagina)

    assert not pagina.locator("#site-header [data-fotos-pendientes]").is_visible()
    orden = pagina.evaluate("""() => {
        const lector = document.querySelector('#site-header [data-modo-lector]');
        return lector.nextElementSibling && lector.nextElementSibling.hasAttribute('data-fotos-pendientes');
    }""")
    assert orden is True
