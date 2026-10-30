# -*- coding: utf-8 -*-
"""
Seam de navegador real — "Recibir" no espera fotos: lo pendiente pasa a la cola del equipo y se sube solo después
(issue 389, `.scratch/pendientes-cliente`).
"""

import io
import os

from PIL import Image

from app.domain.paquete import EstadoPaquete
from app.domain.paquete_foto import PaqueteFoto

from _ayudantes import (
    abrir_menu_de_cuenta,
    abrir_modal_recibir,
    anunciar_paquete,
    espiar_envios,
    iniciar_sesion_staff,
    paquete_en_bd,
    preparar_recibir,
)


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


# --- Fotos sin pérdida (migración 0072) ---

def _jpeg_grande():
    """Un original "de cámara": 2400x1800 de ruido (lo que peor comprime), varios MB."""
    ancho, alto = 2400, 1800
    imagen = Image.frombytes("RGB", (ancho, alto), os.urandom(ancho * alto * 3))
    buffer = io.BytesIO()
    imagen.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def test_una_entrada_de_mas_de_24_h_no_se_borra_y_el_banner_avisa(app_viva, pagina):
    """Antes la cola borraba en silencio lo de más de 24 h: así se perdieron las fotos de 4 paquetes."""
    iniciar_sesion_staff(pagina, app_viva)
    p = anunciar_paquete(app_viva)  # Anunciado: el servidor responde "reintentar", la entrada se queda
    pagina.goto(f"{app_viva.url}/paquetes")
    pagina.evaluate("""id => new Promise((res, rej) => {
        const r = indexedDB.open('paquetex-fotos', 1);
        r.onupgradeneeded = () => r.result.createObjectStore('cola', {keyPath: 'id', autoIncrement: true});
        r.onsuccess = () => {
            const tx = r.result.transaction('cola', 'readwrite');
            tx.objectStore('cola').add({paqueteId: id, archivo: new Blob(['x'], {type: 'image/jpeg'}), nombre: 'vieja.jpg',
                                        creado: Date.now() - 48 * 3600 * 1000, intentos: 0, siguiente: 0});
            tx.oncomplete = () => { r.result.close(); res(); };
            tx.onerror = () => rej(tx.error);
        };
    })""", str(p.id))

    pagina.evaluate("() => window.paqueteXColaFotos.procesar(true)")

    assert _cola(pagina) == 1
    banner = pagina.locator("[data-fotos-pendientes-banner]")
    banner.wait_for(state="visible")
    assert "1 foto de este equipo aún no se sube" in banner.inner_text()
    assert "No cierres esta página" in banner.inner_text()


def test_recibir_con_0_fotos_queda_bloqueado_hasta_marcar_la_casilla(app_viva, pagina):
    iniciar_sesion_staff(pagina, app_viva)
    p = anunciar_paquete(app_viva)
    abrir_modal_recibir(pagina, app_viva, p, sin_fotos=False)
    enviados = espiar_envios(pagina)
    boton = pagina.locator(f"#modal-receive-{p.id} button[type=submit]")

    boton.click()

    error = pagina.locator(f"#foto-error-recibir-{p.id}")
    error.wait_for(state="visible")
    assert "Recibir sin fotos" in error.inner_text()
    assert enviados == []
    assert paquete_en_bd(app_viva, p.id).estado == EstadoPaquete.ANUNCIADO
    assert boton.is_enabled()  # el bloqueo no deja el botón deshabilitado por el anti doble-envío

    pagina.locator(f"#foto-sin-fotos-check-recibir-{p.id}").check()
    with pagina.expect_navigation():
        boton.click()

    recibido = paquete_en_bd(app_viva, p.id)
    assert recibido.estado == EstadoPaquete.RECIBIDO
    assert recibido.recibido_sin_fotos is True


def test_la_foto_se_reduce_en_el_navegador_antes_de_subirse(app_viva, pagina):
    p = preparar_recibir(app_viva, pagina)
    original = _jpeg_grande()

    # Qué archivo va en el FormData de la subida (nombre y tamaño), visto desde la propia página.
    pagina.evaluate("""() => {
        const original = window.fetch;
        window.__subidas = [];
        window.fetch = (url, opciones) => {
            const foto = opciones && opciones.body instanceof FormData ? opciones.body.get('foto') : null;
            if (foto) window.__subidas.push({nombre: foto.name, tamano: foto.size, tipo: foto.type});
            return original(url, opciones);
        };
    }""")

    with pagina.expect_response(lambda r: r.request.method == "POST" and r.url.endswith(f"/paquetes/{p.id}/fotos")) as respuesta:
        pagina.set_input_files(f"#foto-input-recibir-{p.id}",
                               files=[{"name": "IMG_0001.JPEG", "mimeType": "image/jpeg", "buffer": original}])
    cuerpo = respuesta.value.request.sizes()["requestBodySize"]
    subida = pagina.evaluate("() => window.__subidas")[0]

    assert cuerpo < len(original) / 2, (cuerpo, len(original))
    assert subida["tamano"] < len(original) / 2
    assert subida["nombre"] == "IMG_0001.jpg"
    assert subida["tipo"] == "image/jpeg"
    pagina.wait_for_function("() => document.querySelector('[aria-label=\"Ya subida\"]')")
    # Con una foto, la casilla "Recibir sin fotos" sobra: se oculta y se desmarca.
    assert not pagina.locator(f"#foto-sin-fotos-check-recibir-{p.id}").is_checked()
    with pagina.expect_navigation():
        pagina.locator(f"#modal-receive-{p.id} button[type=submit]").click()
    assert len(_fotos(app_viva, p)) == 1
    assert paquete_en_bd(app_viva, p.id).recibido_sin_fotos is False


def test_recibir_con_la_foto_en_cola_la_anuncia_al_servidor(app_viva, pagina):
    p = preparar_recibir(app_viva, pagina)
    pagina.route("**/paquetes/*/fotos", lambda ruta: None)  # la subida progresiva nunca responde
    pagina.set_input_files(f"#foto-input-recibir-{p.id}",
                           files=[{"name": "etiqueta.jpg", "mimeType": "image/jpeg", "buffer": _jpeg()}])
    pagina.wait_for_function("() => document.body.innerText.includes('Subiendo')")

    with pagina.expect_navigation():
        pagina.locator(f"#modal-receive-{p.id} button[type=submit]").click()

    assert paquete_en_bd(app_viva, p.id).fotos_pendientes == 1
    pagina.locator("[data-fotos-pendientes-banner]").wait_for(state="visible")
    assert pagina.locator(f'[data-tarjeta-paquete="{p.id}"], tr').filter(has_text="Fotos pendientes (1)").count() >= 1
