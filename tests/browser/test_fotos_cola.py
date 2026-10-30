# -*- coding: utf-8 -*-
"""
Seam de navegador real — "Recibir" no espera fotos: lo pendiente pasa a la cola del equipo y se sube solo después
(issue 389, `.scratch/pendientes-cliente`).
"""

import io
import os

import pytest

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

def _jpeg_grande(ancho=2400, alto=1800, orientacion_exif=None):
    """Un original "de cámara": ruido (lo que peor comprime), varios MB. Con `orientacion_exif=6` los píxeles quedan
    acostados y el EXIF dice "girar 90°", como guardan las fotos verticales muchos celulares."""
    imagen = Image.frombytes("RGB", (ancho, alto), os.urandom(ancho * alto * 3))
    buffer = io.BytesIO()
    if orientacion_exif:
        exif = Image.Exif()
        exif[0x0112] = orientacion_exif
        imagen.save(buffer, format="JPEG", quality=92, exif=exif.tobytes())
    else:
        imagen.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def _espiar_subidas(pagina):
    """Qué archivo va en el FormData de cada subida (nombre, tamaño y medidas de sus píxeles), visto desde la página."""
    pagina.evaluate("""() => {
        const original = window.fetch, decodificar = window.createImageBitmap.bind(window);
        window.__subidas = [];
        window.fetch = (url, opciones) => {
            const foto = opciones && opciones.body instanceof FormData ? opciones.body.get('foto') : null;
            if (foto) window.__subidas.push(decodificar(foto, {imageOrientation: 'none'}).then(bmp => {
                const s = {nombre: foto.name, tamano: foto.size, tipo: foto.type, ancho: bmp.width, alto: bmp.height};
                bmp.close();
                return s;
            }));
            return original(url, opciones);
        };
    }""")


def _subidas(pagina):
    return pagina.evaluate("() => Promise.all(window.__subidas)")


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

    _espiar_subidas(pagina)

    with pagina.expect_response(lambda r: r.request.method == "POST" and r.url.endswith(f"/paquetes/{p.id}/fotos")) as respuesta:
        pagina.set_input_files(f"#foto-input-recibir-{p.id}",
                               files=[{"name": "IMG_0001.JPEG", "mimeType": "image/jpeg", "buffer": original}])
    cuerpo = respuesta.value.request.sizes()["requestBodySize"]
    subida = _subidas(pagina)[0]

    assert cuerpo < len(original) / 2, (cuerpo, len(original))
    assert (subida["ancho"], subida["alto"]) == (1600, 1200)
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


@pytest.mark.parametrize(
    "ancho, alto, orientacion_exif, esperado",
    [
        (1800, 2400, None, (1200, 1600)),  # vertical de verdad
        (2400, 1800, 6, (1200, 1600)),  # píxeles acostados + EXIF "girar 90°": sale vertical, sin deformarse
    ],
)
def test_la_reduccion_conserva_la_orientacion_y_la_proporcion(app_viva, pagina, ancho, alto, orientacion_exif, esperado):
    p = preparar_recibir(app_viva, pagina)
    original = _jpeg_grande(ancho, alto, orientacion_exif)
    _espiar_subidas(pagina)

    with pagina.expect_response(lambda r: r.request.method == "POST" and r.url.endswith(f"/paquetes/{p.id}/fotos")):
        pagina.set_input_files(f"#foto-input-recibir-{p.id}",
                               files=[{"name": "vertical.jpg", "mimeType": "image/jpeg", "buffer": original}])
    subida = _subidas(pagina)[0]

    assert (subida["ancho"], subida["alto"]) == esperado
    assert subida["tamano"] < len(original) / 2


def test_las_fotos_se_comprimen_de_a_una(app_viva, pagina):
    """Poca memoria: 3 capturas seguidas no deben decodificarse a la vez (cada una ocupa decenas de MB)."""
    p = preparar_recibir(app_viva, pagina)
    # Cuenta los bitmaps "grandes" vivos (no las miniaturas de 128 px) desde que se piden hasta su `close()`.
    pagina.evaluate("""() => {
        const original = window.createImageBitmap.bind(window);
        window.__vivos = 0; window.__maxVivos = 0; window.__decodificadas = 0; window.__reducidas = 0;
        window.createImageBitmap = (fuente, opciones) => {
            if (opciones && opciones.resizeWidth === 128) return original(fuente, opciones);
            window.__vivos += 1; window.__decodificadas += 1;
            if (opciones && opciones.resizeWidth) window.__reducidas += 1;  // ya reducida, sin decodificar completa
            window.__maxVivos = Math.max(window.__maxVivos, window.__vivos);
            return original(fuente, opciones).then(bmp => {
                const cerrar = bmp.close.bind(bmp);
                bmp.close = () => { window.__vivos -= 1; cerrar(); };
                return bmp;
            }, err => { window.__vivos -= 1; throw err; });
        };
    }""")
    fotos = [{"name": f"f{i}.jpg", "mimeType": "image/jpeg", "buffer": _jpeg_grande()} for i in range(3)]

    pagina.set_input_files(f"#foto-input-recibir-{p.id}", files=fotos)
    pagina.wait_for_function("() => document.querySelectorAll('[aria-label=\"Ya subida\"]').length === 3", timeout=30000)

    assert pagina.evaluate("() => window.__decodificadas") >= 3
    assert pagina.evaluate("() => window.__maxVivos") == 1
    assert pagina.evaluate("() => window.__reducidas") == 3  # las 3 se decodificaron ya reducidas
