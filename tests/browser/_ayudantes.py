# -*- coding: utf-8 -*-
"""
Ayudantes del seam de navegador real (`conftest.py` de esta carpeta explica cómo correrlo).

Ninguno importa Playwright: reciben la `pagina` (una `Page` de Playwright) ya creada por el fixture,
así que importar este módulo es barato y seguro aun sin Playwright instalado (la suite por defecto
recoge estas pruebas para deseleccionarlas, no para ejecutarlas).
"""

from app.domain.paquete import Paquete
from app.domain.paquete_lifecycle import receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin
from app.domain.usuario import Usuario

PASSWORD_STAFF = "Contrasena1"
EMAIL_STAFF = "staff@club.com"


def iniciar_sesion_staff(pagina, app_viva, email=EMAIL_STAFF):
    """Crea un Operador/Admin en la BD de la prueba y deja `pagina` con su sesión iniciada.

    El login va por HTTP con el contexto del navegador (las cookies de la respuesta quedan en la
    misma sesión de la `pagina`): más rápido que llenar el formulario y no es lo que se prueba acá.
    """
    create_initial_admin(app_viva.db, email, "Staff", PASSWORD_STAFF)
    app_viva.db.commit()
    volver_a_iniciar_sesion(pagina, app_viva, email)


def anunciar_paquete(app_viva, tel="3001234567", nombre="Ana"):
    """Deja un Paquete `Anunciado` (destinatario: el propio Anunciante) y lo devuelve."""
    p = announce(
        app_viva.db,
        anunciante_telefono=tel,
        anunciante_nombre=nombre,
        destinatario=Destinatario.yo_mismo(),
    )
    app_viva.db.commit()
    return p


def paquete_en_bd(app_viva, paquete_id):
    """El Paquete tal como está AHORA en la BD (fuente de verdad de qué se envió de verdad)."""
    app_viva.db.expire_all()
    return app_viva.db.get(Paquete, paquete_id)


def abrir_modal_recibir(pagina, app_viva, paquete):
    """Va a /paquetes y abre el modal Recibir de `paquete` con un clic real sobre su botón."""
    modal_id = f"modal-receive-{paquete.id}"
    pagina.goto(f"{app_viva.url}/paquetes")
    pagina.locator(f'[data-open="{modal_id}"]:visible').first.click()
    pagina.wait_for_function("id => !document.getElementById(id).hidden", arg=modal_id)


def recibir_paquete_en_bd(app_viva, paquete, guia, email=EMAIL_STAFF):
    """Deja el Paquete `Recibido` con `guia` directo en el dominio (para probar Entregar sin pasar por Recibir)."""
    staff = app_viva.db.query(Usuario).filter(Usuario.email == email).one()
    receive(app_viva.db, paquete, staff, guia)
    app_viva.db.commit()


def abrir_modal_entregar(pagina, app_viva, paquete):
    """Va a /paquetes (Recibidos) y abre el modal Entregar de `paquete` con un clic real."""
    modal_id = f"modal-deliver-{paquete.id}"
    pagina.goto(f"{app_viva.url}/paquetes?estado=RECIBIDO")
    pagina.locator(f'[data-open="{modal_id}"]:visible').first.click()
    pagina.wait_for_function("id => !document.getElementById(id).hidden", arg=modal_id)


def sembrar_paquete_con_guia(app_viva, guia, tel="3105550000", nombre="Sofia"):
    """Otro Paquete (de otra persona) ya `Recibido` con `guia`: lo que el aviso de repetida debe encontrar."""
    otro = anunciar_paquete(app_viva, tel=tel, nombre=nombre)
    recibir_paquete_en_bd(app_viva, otro, guia)
    return otro


def volver_a_iniciar_sesion(pagina, app_viva, email=EMAIL_STAFF):
    """Inicia sesión con un Staff que YA existe en la BD de la prueba (p. ej. en otro contexto de navegador)."""
    respuesta = pagina.context.request.post(
        f"{app_viva.url}/ingresar",
        form={"email": email, "password": PASSWORD_STAFF},
        max_redirects=0,
    )
    assert respuesta.status == 303, f"el login de Staff no redirigió (status {respuesta.status})"


def abrir_menu_de_cuenta(pagina):
    """Abre el menú de cuenta si está cerrado (un clic en el `summary` lo alterna)."""
    if pagina.locator("#site-header .account-menu").get_attribute("open") is None:
        pagina.click("#site-header .account-menu > summary")


def alternar_modo_lector(pagina):
    """Pulsa "Este equipo tiene lector" en el menú de cuenta (activa o desactiva)."""
    abrir_menu_de_cuenta(pagina)
    pagina.locator("#site-header [data-modo-lector]").click()


def foco_en(pagina):
    """El `id` del elemento que tiene el foco ahora."""
    return pagina.evaluate("() => document.activeElement && document.activeElement.id")


def preparar_recibir(app_viva, pagina):
    """Login de Staff, un Paquete `Anunciado` y su modal Recibir abierto. Devuelve el Paquete."""
    iniciar_sesion_staff(pagina, app_viva)
    p = anunciar_paquete(app_viva)
    abrir_modal_recibir(pagina, app_viva, p)
    return p


def preparar_entregar(app_viva, pagina, guia="GUIA-9"):
    """Login de Staff, un Paquete `Recibido` con `guia` y su modal Entregar abierto. Devuelve el Paquete."""
    iniciar_sesion_staff(pagina, app_viva)
    p = anunciar_paquete(app_viva)
    recibir_paquete_en_bd(app_viva, p, guia)
    abrir_modal_entregar(pagina, app_viva, p)
    return p


def espiar_envios(pagina, sufijo="/recibir"):
    """Lista viva de los POST cuya URL termina en `sufijo` que el navegador envía (lo que salió de verdad)."""
    enviados = []
    pagina.on(
        "request",
        lambda r: enviados.append(r.url) if r.method == "POST" and r.url.endswith(sufijo) else None,
    )
    return enviados


# --- escaneo con cámara en el modal Recibir (los mismos localizadores en todas las pruebas de cámara) ---
def boton_escanear(pagina, paquete):
    return pagina.locator(f"#modal-receive-{paquete.id} .scan-btn")


def escanear(pagina, paquete):
    boton_escanear(pagina, paquete).click()


def video_de(pagina, paquete):
    return pagina.locator(f"#video-{paquete.id}")


def mensaje_de_escaneo(pagina, paquete):
    return pagina.locator(f"#modal-receive-{paquete.id} .scan-msg")


def esperar_sin_flujos_vivos(pagina):
    """Espera a que TODA pista de TODO flujo que abrió la cámara simulada quede `ended` (nada encendido)."""
    pagina.wait_for_function(
        "() => window.__camara.flujos.every(f => f.getTracks().every(t => t.readyState === 'ended'))"
    )
