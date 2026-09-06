# -*- coding: utf-8 -*-
"""
Capa web — header/footer transversales (Grupo 9).

Ticket 01 (`.scratch/header-footer/issues/01-header-footer-publicos.md`):
comportamiento observable por HTTP para un visitante SIN ninguna sesión — el
header con marca + enlaces públicos + botones de login, el enlace de la
pantalla actual marcado como activo, y el footer móvil con los mismos
enlaces. Sin Tailwind ni Alpine.js (ADR-0004) — la app del rebuild es
clean-room, aislada del stack legacy.

Ticket 02 (`.scratch/header-footer/issues/02-nav-cliente-autenticado.md`):
con sesión de `Persona` (`persona_id`, vía `/otp`), el header muestra el
conjunto de enlaces de cliente en vez del público, y NO enlaces de staff.

Ticket 03 (`.scratch/header-footer/issues/03-nav-staff-rol-y-sesiones-coexistentes.md`):
con sesión de `Usuario` (`usuario_id`, vía `/ingresar`), el header muestra el
conjunto de enlaces de staff; Administración solo si el rol es ADMIN. Con
sesiones de cliente Y staff coexistiendo, se muestran ambos conjuntos juntos.
"""

from app.domain.otp_sender import DevOtpSender
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_lifecycle import receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario, Usuario
from app.web.otp import get_otp_sender

_CANON = "+573001234567"
_PW = "Contrasena1"


def _login_staff_admin(client, email="admin@club.com"):
    create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _login_staff_operador(client, email="opera@club.com"):
    admin = create_initial_admin(client.db, "admin-seed@club.com", "AdminSeed", _PW)
    create_staff(client.db, admin, email, "Opera", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _login_cliente(client, telefono="3001234567"):
    # Corrección en vivo 2026-08-02: pedir OTP exige que el teléfono sea
    # elegible (tenga un Paquete Recibido) -- mismo fixture que
    # test_customer_verify.py, este archivo se había quedado desactualizado
    # (rompía en CI desde entonces, ver .scratch/pendientes-cliente).
    ya_elegible = (
        client.db.query(Paquete)
        .filter(
            Paquete.estado == EstadoPaquete.RECIBIDO,
            (Paquete.announced_by_phone == _CANON) | (Paquete.recipient_phone == _CANON),
        )
        .first()
        is not None
    )
    if not ya_elegible:
        staff = Usuario(nombre="ActorElegibilidad", rol=RolUsuario.OPERADOR)
        client.db.add(staff)
        client.db.flush()
        p = announce(
            client.db,
            anunciante_telefono=telefono,
            anunciante_nombre="Cliente de prueba",
            destinatario=Destinatario.yo_mismo(),
        )
        receive(client.db, p, staff)
        client.db.commit()

    sender = DevOtpSender()
    client.app.dependency_overrides[get_otp_sender] = lambda: sender
    client.post("/otp/solicitar", data={"telefono": telefono})
    codigo = sender.enviados[_CANON]
    client.post("/otp/verificar", data={"telefono": telefono, "codigo": codigo})


def test_visitante_publico_ve_header_con_marca_enlaces_y_boton_login(client):
    r = client.get("/anunciar")
    assert r.status_code == 200
    html = r.text

    assert "PAQUETEX" in html
    assert 'href="/anunciar"' in html
    assert 'href="/consultar"' in html
    # Grupo 10 (Ronda 2): un solo botón de login unificado, ya no dos.
    assert 'href="/entrar"' in html
    assert 'href="/otp"' not in html
    assert 'href="/ingresar"' not in html


def test_visitante_publico_no_ve_ningun_enlace_de_cliente_ni_de_staff(client):
    r = client.get("/anunciar")
    html = r.text

    assert 'href="/mis-datos"' not in html
    assert 'href="/paquetes"' not in html
    assert 'href="/announce"' not in html
    assert 'href="/residentes"' not in html
    assert 'href="/administracion/personal"' not in html
    assert 'href="/administracion/notificaciones"' not in html


def _etiqueta_ancla(html: str, href: str, desde: int = 0) -> str:
    """Extrae el `<a ...>` completo que apunta a `href` (hasta el `>` de cierre),
    buscando a partir de `desde` (para saltarse el link de marca, que también
    apunta a `/anunciar`)."""
    inicio = html.index(f'href="{href}"', desde)
    fin = html.index(">", inicio)
    return html[max(0, inicio - 10) : fin + 1]


def test_enlace_de_la_pantalla_actual_queda_marcado_como_activo(client):
    r = client.get("/anunciar")
    html = r.text
    desde_nav = html.index('class="site-nav"')
    assert "aria-current" in _etiqueta_ancla(html, "/anunciar", desde_nav)
    assert "aria-current" not in _etiqueta_ancla(html, "/consultar", desde_nav)

    r2 = client.get("/consultar")
    html2 = r2.text
    desde_nav2 = html2.index('class="site-nav"')
    assert "aria-current" not in _etiqueta_ancla(html2, "/anunciar", desde_nav2)
    assert "aria-current" in _etiqueta_ancla(html2, "/consultar", desde_nav2)


def test_footer_movil_repite_los_enlaces_publicos(client):
    r = client.get("/consultar")
    html = r.text
    assert "site-footer-mobile" in html
    footer_idx = html.index('<footer class="site-footer-mobile">')
    footer_html = html[footer_idx:]
    assert 'href="/anunciar"' in footer_html
    assert 'href="/consultar"' in footer_html
    assert 'href="/ayuda"' in footer_html


def test_sin_cdn_ni_alpine_y_tailwind_solo_como_css_local(client):
    # La invariante real de este guardián siempre fue "sin dependencias de
    # runtime externas". Desde el design system (2026-07-29), Tailwind SI existe
    # pero exclusivamente como CSS compilado y auto-hosteado
    # (static/css/tailwind.css, generado con la CLI y commiteado) — el Play CDN
    # (<script src="cdn.tailwindcss.com">) sigue prohibido, igual que Alpine.
    r = client.get("/anunciar")
    html = r.text.lower()
    assert "alpine" not in html
    assert "x-data" not in html
    assert "cdn." not in html
    sin_link_local = html.replace("/static/css/tailwind.css", "")
    assert "tailwind" not in sin_link_local


def test_pantalla_publica_conserva_su_contenido_propio(client):
    r = client.get("/anunciar")
    html = r.text
    # Nombre es condicional (.scratch/anunciar-atajo-telefono-conocido) --
    # una carga limpia arranca solo con Teléfono + Términos.
    assert 'name="telefono"' in html
    assert 'name="acepta_tyc"' in html


# --------------------------------------------------------------------------- #
# Ticket 02 — nav de cliente autenticado
# --------------------------------------------------------------------------- #
def test_cliente_logueado_ve_su_conjunto_de_enlaces_y_boton_de_salida(client):
    _login_cliente(client)
    r = client.get("/mis-datos")
    html = r.text

    assert 'href="/anunciar"' in html
    assert 'href="/consultar"' in html
    assert 'href="/mis-datos"' in html
    assert 'action="/salir-todo"' in html
    assert "Cerrar sesión" in html


def test_cliente_logueado_no_ve_el_header_publico_ni_enlaces_de_staff(client):
    _login_cliente(client)
    r = client.get("/mis-datos")
    html = r.text

    assert 'href="/otp"' not in html
    assert 'href="/ingresar"' not in html
    assert 'href="/paquetes"' not in html
    assert 'href="/announce"' not in html
    assert 'href="/residentes"' not in html
    assert 'href="/administracion/personal"' not in html
    assert 'href="/administracion/notificaciones"' not in html


def test_cliente_logueado_enlace_activo_en_mis_datos(client):
    _login_cliente(client)
    r = client.get("/mis-datos")
    html = r.text
    desde_nav = html.index('class="site-nav"')
    assert "aria-current" in _etiqueta_ancla(html, "/mis-datos", desde_nav)
    assert "aria-current" not in _etiqueta_ancla(html, "/anunciar", desde_nav)


def test_footer_movil_publico_mantiene_consultar_y_ayuda(client):
    """El footer móvil PÚBLICO (sin sesión) sigue siendo
    Anunciar/Consultar/Ayuda/Whatsapp -- solo el del cliente logueado
    cambió (ver el test de abajo), ya que Mis paquetes/Mis datos no
    existen para un visitante sin sesión."""
    r = client.get("/anunciar")
    html = r.text
    footer_idx = html.index('<footer class="site-footer-mobile">')
    footer_html = html[footer_idx:]
    assert 'href="/anunciar"' in footer_html
    assert 'href="/consultar"' in footer_html
    assert 'href="/ayuda"' in footer_html


def test_footer_movil_del_cliente_muestra_mis_paquetes_y_mis_datos(client, monkeypatch):
    """Pedido del cliente (.scratch/pendientes-cliente): el footer móvil de
    un cliente logueado por OTP deja de repetir el público
    (Anunciar/Consultar/Ayuda/Whatsapp) -- pasa a Anunciar/Mis paquetes/
    Mis datos/Whatsapp. Consultar y Ayuda quedan fuera de ESTE footer
    (Consultar sigue en `.site-nav` de escritorio)."""
    monkeypatch.setenv("WHATSAPP_SOPORTE_NUMERO", "573001112233")
    _login_cliente(client)
    r = client.get("/mis-datos")
    html = r.text
    # Acotado a .footer-nav-mobile específicamente -- .footer-nav-desktop
    # (más abajo en el mismo <footer>, CSS-oculto en mobile) sí repite
    # /ayuda, así que un bound genérico hasta </footer> daría un falso
    # negativo en la aserción de ausencia.
    nav_idx = html.index('class="footer-nav-mobile"')
    footer_html = html[nav_idx : html.index("</nav>", nav_idx)]
    assert 'href="/anunciar"' in footer_html
    assert 'href="/mis-paquetes"' in footer_html
    assert 'href="/mis-datos"' in footer_html
    assert 'href="https://wa.me/573001112233"' in footer_html
    assert 'href="/consultar"' not in footer_html
    assert 'href="/ayuda"' not in footer_html


# --------------------------------------------------------------------------- #
# Header: "Mis paquetes"/"Mis datos" en el menú de cuenta + 4ta opción en el
# nav de escritorio (pedido del cliente, .scratch/pendientes-cliente).
# --------------------------------------------------------------------------- #
def test_menu_de_cuenta_del_cliente_incluye_mis_paquetes_y_mis_datos(client):
    """El menú de cuenta (avatar/dropdown, `.account-menu`) es la única vía
    a "Mis paquetes" alcanzable desde mobile -- se ve igual en mobile y
    desktop, a diferencia de `.site-nav` (oculto en mobile)."""
    _login_cliente(client)
    r = client.get("/mis-datos")
    html = r.text
    panel_idx = html.index('class="account-menu-panel"')
    panel_html = html[panel_idx : html.index("</details>", panel_idx)]
    assert 'href="/mis-paquetes"' in panel_html
    assert 'href="/mis-datos"' in panel_html
    # "Mis paquetes" antes de "Mis datos" -- orden pedido por el cliente.
    assert panel_html.index('href="/mis-paquetes"') < panel_html.index('href="/mis-datos"')


def test_menu_de_cuenta_del_cliente_incluye_consultar_y_ayuda_antes_de_salir(client):
    """Pedido del cliente (.scratch/pendientes-cliente): Consultar/Ayuda
    salieron del footer móvil del cliente (issue 61) -- vuelven a estar
    alcanzables desde mobile acá, debajo de Mis paquetes/Mis datos y
    arriba de "Cerrar sesión"."""
    _login_cliente(client)
    r = client.get("/mis-datos")
    html = r.text
    panel_idx = html.index('class="account-menu-panel"')
    panel_html = html[panel_idx : html.index("</details>", panel_idx)]
    assert 'href="/consultar"' in panel_html
    assert 'href="/ayuda"' in panel_html
    orden = [
        panel_html.index('href="/mis-paquetes"'),
        panel_html.index('href="/mis-datos"'),
        panel_html.index('href="/consultar"'),
        panel_html.index('href="/ayuda"'),
        panel_html.index("Cerrar sesión"),
    ]
    assert orden == sorted(orden)


def test_nav_de_escritorio_del_cliente_tiene_4_opciones(client):
    """`.site-nav` (oculto en mobile) pasa de 3 a 4 opciones -- "Mis datos"
    se agrega para no depender de abrir el menú de cuenta en desktop."""
    _login_cliente(client)
    r = client.get("/mis-datos")
    html = r.text
    desde_nav = html.index('class="site-nav"')
    nav_html = html[desde_nav : html.index("</nav>", desde_nav)]
    assert 'href="/anunciar"' in nav_html
    assert 'href="/consultar"' in nav_html
    assert 'href="/mis-paquetes"' in nav_html
    assert 'href="/mis-datos"' in nav_html


def test_cliente_logueado_ve_su_nav_en_cualquier_pantalla_que_alcance_su_sesion(client):
    """El header es global vía base.html — no debe depender de qué plantilla
    específica se esté renderizando (ver ticket 02: 'en /mis-datos y en
    cualquier otra pantalla que la sesión de cliente alcance')."""
    _login_cliente(client)
    r = client.get("/anunciar")
    html = r.text

    assert 'href="/mis-datos"' in html
    assert 'action="/salir-todo"' in html
    assert 'href="/otp"' not in html
    assert 'href="/ingresar"' not in html


# --------------------------------------------------------------------------- #
# Ticket 03 — nav de staff con rol (OPERADOR/ADMIN) y sesiones coexistentes
# --------------------------------------------------------------------------- #
def test_staff_operador_ve_su_conjunto_de_enlaces_sin_administracion(client):
    _login_staff_operador(client)
    r = client.get("/mi-sesion")
    html = r.text
    desde_nav = html.index('class="site-nav"')
    nav_html = html[desde_nav : html.index("</nav>", desde_nav)]

    assert 'href="/paquetes"' in nav_html
    assert 'href="/residentes"' in nav_html
    assert ">Residentes<" in nav_html  # revierte Grupo 10/Ronda 2 (Residentes->Clientes), issue 146
    assert 'href="/consultar"' in nav_html
    # "Declarar unidad" sale del nav de escritorio (Grupo 10, Ronda 2) --
    # queda solo en el footer móvil hasta que exista el botón dedicado.
    assert 'href="/announce"' not in nav_html
    assert 'action="/salir-todo"' in html
    assert "Cerrar sesión" in html

    assert 'href="/administracion/personal"' not in html
    assert 'href="/administracion/notificaciones"' not in html
    assert 'href="/mis-datos"' not in html
    assert 'href="/entrar"' not in html


def test_staff_admin_ve_ademas_los_enlaces_de_administracion(client):
    _login_staff_admin(client)
    r = client.get("/mi-sesion")
    html = r.text

    assert 'href="/paquetes"' in html
    assert 'href="/administracion/personal"' in html
    assert 'href="/administracion/notificaciones"' in html


def test_staff_admin_no_duplica_notificaciones_ni_proveedores_en_el_tab_del_header(client):
    # Seguimiento a issue 190: "Notificaciones" (y luego "Proveedores")
    # se habían promovido a `.site-nav` además de vivir en el menú de
    # cuenta -- issue 295 revierte esa duplicación, quedan solo en el menú.
    _login_staff_admin(client)
    r = client.get("/mi-sesion")
    html = r.text
    desde_nav = html.index('class="site-nav"')
    nav_html = html[desde_nav : html.index("</nav>", desde_nav)]
    assert 'href="/administracion/notificaciones"' not in nav_html
    assert 'href="/administracion/proveedores"' not in nav_html
    assert 'href="/administracion/notificaciones"' in html
    assert 'href="/administracion/proveedores"' in html


def test_require_admin_sigue_siendo_la_puerta_real_para_operador(client):
    """El menú no debe insinuar acceso que no existe: `require_admin` sigue
    siendo la única fuente de autorización, no el rol guardado en sesión."""
    _login_staff_operador(client)
    r = client.get("/administracion/personal", follow_redirects=False)
    assert r.status_code == 403


def test_enlace_activo_de_staff_en_paquetes(client):
    _login_staff_operador(client)
    r = client.get("/paquetes")
    html = r.text
    desde_nav = html.index('class="site-nav"')
    assert "aria-current" in _etiqueta_ancla(html, "/paquetes", desde_nav)
    assert "aria-current" not in _etiqueta_ancla(html, "/residentes", desde_nav)


def test_footer_movil_de_staff_repite_sus_enlaces(client):
    _login_staff_operador(client)
    r = client.get("/paquetes")
    html = r.text
    footer_idx = html.index('<footer class="site-footer-mobile">')
    footer_html = html[footer_idx:]
    assert 'href="/paquetes"' in footer_html


def test_header_tiene_id_unico_para_aislar_su_css_de_cada_pantalla(client):
    """Varias pantallas (`/announce`, `/administracion/notificaciones`,
    `/residentes/{id}`) definen su propio botón con `button[type=submit]`,
    misma especificidad que un selector de clase -- sin el id del header
    como ancla, esas pantallas le imponen su propio estilo al botón de
    "Cerrar sesión" del header (visto en vivo, corregido). Este test ancla
    el mecanismo del que depende el aislamiento: si alguien quita el id o
    des-escala el selector de vuelta a uno sin `#site-header`, esto debe
    fallar antes de llegar a producción. El botón de logout vive hoy dentro
    del menú de cuenta (`.account-menu-item`), no de `.site-actions` (Grupo
    "header producción", que movió Cerrar sesión al dropdown del nav)."""
    r = client.get("/anunciar")
    html = r.text
    assert 'id="site-header"' in html
    assert "#site-header button.account-menu-item" in html


def test_sesiones_coexistentes_muestran_ambos_conjuntos_de_enlaces(client):
    _login_cliente(client)
    _login_staff_operador(client)

    r = client.get("/mi-sesion")
    html = r.text

    # Cliente (ticket 02) + staff (este ticket) se muestran juntos.
    assert 'href="/mis-datos"' in html
    assert 'href="/paquetes"' in html
    assert 'href="/residentes"' in html
    # Grupo 10 (Ronda 2): un solo botón de logout, no uno por sesión.
    assert html.count('action="/salir-todo"') == 1


def test_visitante_sin_sesion_sigue_viendo_solo_el_header_publico(client):
    r = client.get("/anunciar")
    html = r.text
    assert 'href="/entrar"' in html
    assert 'href="/mis-datos"' not in html
    assert 'action="/salir-todo"' not in html


# --------------------------------------------------------------------------- #
# Footer móvil de staff + WhatsApp condicional (Grupo 10, Ronda 2)
# --------------------------------------------------------------------------- #
def test_footer_movil_de_staff_incluye_anunciar_y_clientes(client):
    _login_staff_operador(client)
    r = client.get("/paquetes")
    html = r.text
    footer_idx = html.index('<footer class="site-footer-mobile">')
    footer_html = html[footer_idx:]
    assert 'href="/announce"' in footer_html
    assert 'href="/consultar"' in footer_html
    assert 'href="/paquetes"' in footer_html
    assert 'href="/residentes"' in footer_html


def test_whatsapp_no_aparece_sin_variable_de_entorno_configurada(client, monkeypatch):
    monkeypatch.delenv("WHATSAPP_SOPORTE_NUMERO", raising=False)
    r = client.get("/anunciar")
    assert "whatsapp.com" not in r.text


def test_whatsapp_aparece_con_variable_de_entorno_configurada(client, monkeypatch):
    monkeypatch.setenv("WHATSAPP_SOPORTE_NUMERO", "573001112233")
    r = client.get("/anunciar")
    assert 'href="https://wa.me/573001112233"' in r.text


def test_whatsapp_footer_desktop_usa_web_whatsapp_com(client, monkeypatch):
    # Issue 305 (.scratch/pendientes-cliente): `.footer-nav-desktop` (CSS-
    # oculto en mobile, ver `.footer-nav-mobile`/`.footer-nav-desktop` en
    # base.html) usa `web.whatsapp.com` -- captura la PWA de Chrome
    # instalada en escritorio, algo que `wa.me` (el de `.footer-nav-
    # mobile`, sin cambios) nunca hace ahí. Ver `.scratch/whatsapp-deep-
    # link/investigacion-oficial.md` para el porqué de los 2 dominios.
    monkeypatch.setenv("WHATSAPP_SOPORTE_NUMERO", "573001112233")
    r = client.get("/anunciar")
    html = r.text
    nav_idx = html.index('class="footer-nav-desktop"')
    footer_desktop_html = html[nav_idx : html.index("</nav>", nav_idx)]
    assert 'href="https://web.whatsapp.com/send?phone=573001112233"' in footer_desktop_html
    assert "wa.me" not in footer_desktop_html
