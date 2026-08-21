# -*- coding: utf-8 -*-
"""
Capa web — `/consultar` (Grupo 2 de ajustes-post-referencia-funcional).

Vista PÚBLICA (sin sesión). Comportamiento observable por HTTP: el formulario,
el match exacto por `access_code` o `guide_number`, el timeline armado desde
los timestamps de transición, y "sin resultados" sin error. La búsqueda por
teléfono se ELIMINÓ a propósito (por seguridad — el `access_code` solo lo
conoce quien anunció) y ya no se re-testea.

Grupo 11 de la Ronda 2 (`ajustes-post-referencia-funcional/REQUERIMIENTOS.md`)
revirtió la decisión original de ocultar el actor: cada hito del timeline
ahora SÍ muestra quién lo hizo. Solo el nombre, sin "(cliente)"/"(staff)"
(pedido del cliente, .scratch/pendientes-cliente): el verbo del hito
(Anunció/Recibió/Entregó/Canceló) ya deja claro el rol.
"""

from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_lifecycle import cancel, deliver, receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin

_PW = "Contrasena1"


def _anunciar(client, tel="3001234567", nombre="Ana"):
    p = announce(
        client.db,
        anunciante_telefono=tel,
        anunciante_nombre=nombre,
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()
    return p


def _staff(client):
    u = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    client.db.commit()
    return u


def _login_staff(client, staff):
    r = client.post("/ingresar", data={"email": staff.email, "password": _PW})
    assert r.status_code == 200
    return staff


def test_get_search_sin_termino_muestra_el_formulario(client):
    r = client.get("/consultar")
    assert r.status_code == 200
    assert 'name="q"' in r.text


def test_search_no_requiere_sesion(client):
    # A diferencia de /packages, /search es pública.
    r = client.get("/consultar", follow_redirects=False)
    assert r.status_code == 200


def test_buscar_por_access_code_muestra_estado_anunciado(client):
    p = _anunciar(client, nombre="Ana")
    r = client.get("/consultar", params={"q": p.access_code})
    assert r.status_code == 200
    assert "ANA" in r.text
    assert "Anunciado" in r.text  # badge de estado + hito del timeline


def test_torre_del_snapshot_no_duplica_el_prefijo_torre(client):
    # Issue 152, reportado en vivo (conversación 2026-08-21): `snapshot_torre`
    # ya guarda el label completo del catálogo (ej. "TORRE 10", ver
    # `components/_inputs.html`), así que "Torre " + snapshot_torre crudo
    # queda "Torre TORRE 10" -- el filtro `torre_sin_prefijo` lo sanea.
    p = _anunciar(client, nombre="Ana")
    p.snapshot_conjunto, p.snapshot_torre, p.snapshot_apartamento = (
        "EL CLUB",
        "TORRE 10",
        "101",
    )
    client.db.commit()

    r = client.get("/consultar", params={"q": p.access_code})
    assert r.status_code == 200
    assert "EL CLUB · Torre 10 · Apto 101" in r.text
    assert "Torre TORRE 10" not in r.text


def test_timeline_muestra_recibido_y_entregado_tras_transiciones(client):
    staff = _staff(client)
    p = _anunciar(client)
    receive(client.db, p, staff)
    deliver(client.db, p, staff)
    client.db.commit()

    r = client.get("/consultar", params={"q": p.access_code})
    assert r.status_code == 200
    assert "Entregado" in r.text
    assert "Recibido" in r.text and "Entregado" in r.text


def test_timeline_no_muestra_el_sufijo_actual(client):
    # Issue 134, pedido explícito: "esa palabra ' • Actual' no deberia
    # aparecer en ningun estado" -- ya se había quitado del modal "Ver"
    # de /paquetes (issue 106), pero /consultar seguía mostrándolo en el
    # paso vigente del timeline (ej. "Entregado • Actual").
    staff = _staff(client)
    p = _anunciar(client)
    receive(client.db, p, staff)
    deliver(client.db, p, staff)
    client.db.commit()

    r = client.get("/consultar", params={"q": p.access_code})
    assert r.status_code == 200
    assert "Actual" not in r.text
    # Grupo 11 (Ronda 2): el timeline SÍ muestra quién hizo cada transición.
    assert staff.nombre in r.text


def test_paquete_cancelado_muestra_el_motivo(client):
    staff = _staff(client)
    p = _anunciar(client)
    cancel(client.db, p, staff, "NO_RECLAMADO")
    client.db.commit()

    r = client.get("/consultar", params={"q": p.access_code})
    assert r.status_code == 200
    assert "Cancelado" in r.text
    assert "reclamado" in r.text.lower()  # "No reclamado" (motivo formateado)


def test_sin_telefono_alguno_muestra_nd_no_none(client):
    # Anunciante solo-WhatsApp (ADR-0007, sin Teléfono) anunciando para sí
    # mismo: `recipient_phone` y `announced_by_phone` quedan ambos None --
    # la fila Teléfono debe mostrar "N/D", nunca el literal "None"
    # (.scratch/pendientes-cliente issue 138).
    p = announce(
        client.db,
        anunciante_whatsapp="ana.whats",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    r = client.get("/consultar", params={"q": p.access_code})
    assert r.status_code == 200
    assert "None" not in r.text
    assert "N/D" in r.text


def test_termino_sin_coincidencia_da_sin_resultados_sin_error(client):
    r = client.get("/consultar", params={"q": "NO-EXISTE-999"})
    assert r.status_code == 200
    assert "no encontramos" in r.text.lower()


# --------------------------------------------------------------------------- #
# Foco condicional (versión móvil, `.scratch/pendientes-cliente`): autofocus
# SOLO en la carga limpia del formulario -- ya consultado (con o sin
# resultado), no debe reactivar el teclado y tapar lo que aparece debajo.
# --------------------------------------------------------------------------- #
def test_get_search_sin_termino_tiene_autofocus(client):
    r = client.get("/consultar")
    assert "autofocus" in r.text


def test_consultar_con_resultado_no_tiene_autofocus(client):
    p = _anunciar(client, nombre="Ana")
    r = client.get("/consultar", params={"q": p.access_code})
    assert "autofocus" not in r.text


def test_consultar_sin_resultado_no_tiene_autofocus(client):
    r = client.get("/consultar", params={"q": "NO-EXISTE-999"})
    assert "autofocus" not in r.text


# --------------------------------------------------------------------------- #
# Buscar por guía (Grupo 2) — el otro campo válido, además de access_code.
# --------------------------------------------------------------------------- #
def test_buscar_por_guia_encuentra_el_paquete(client):
    staff = _staff(client)
    p = _anunciar(client, nombre="Ana")
    receive(client.db, p, staff, "GUIA-XYZ-001")
    client.db.commit()

    r = client.get("/consultar", params={"q": "GUIA-XYZ-001"})
    assert r.status_code == 200
    assert "ANA" in r.text


def test_buscar_por_telefono_ya_no_encuentra_nada(client):
    # La búsqueda por teléfono se eliminó a propósito (por seguridad).
    _anunciar(client, tel="3001234567", nombre="Ana")
    client.db.commit()

    r = client.get("/consultar", params={"q": "3001234567"})
    assert r.status_code == 200
    assert "no encontramos" in r.text.lower()


def test_muestra_telefono_y_enlace_para_actualizar_si_falta_apartamento(client):
    p = _anunciar(client, tel="3001234567", nombre="Ana")
    r = client.get("/consultar", params={"q": p.access_code})
    assert r.status_code == 200
    assert "+573001234567" in r.text
    assert 'href="/otp"' in r.text


def test_timeline_muestra_tipo_condicion_y_foto(client):
    from app.domain.foto_storage import LocalFotoStorage
    from app.domain.paquete_foto_service import agregar_foto
    from app.domain.paquete import CondicionPaquete, TipoPaquete
    import tempfile
    from pathlib import Path

    staff = _staff(client)
    p = _anunciar(client)
    receive(
        client.db, p, staff,
        package_type=TipoPaquete.EXTRA_DIMENSIONADO,
        package_condition=CondicionPaquete.ABIERTO,
    )
    storage = LocalFotoStorage(Path(tempfile.mkdtemp()))
    agregar_foto(client.db, p, storage, "recibo.jpg", b"contenido")
    client.db.commit()

    r = client.get("/consultar", params={"q": p.access_code})
    assert r.status_code == 200
    assert "Extra dimensionado" in r.text
    assert "Abierto" in r.text
    assert "foto-paquete" in r.text


# --------------------------------------------------------------------------- #
# Grupo 11 (Ronda 2) — auditoría de actor visible en el timeline.
# --------------------------------------------------------------------------- #
def test_paquete_anunciado_por_el_propio_cliente_muestra_su_nombre_sin_etiqueta(client):
    p = _anunciar(client, nombre="Ana Torres")
    r = client.get("/consultar", params={"q": p.access_code})
    assert r.status_code == 200
    assert "ANA TORRES" in r.text
    assert "(cliente)" not in r.text and "(staff)" not in r.text


def test_paquete_anunciado_por_staff_muestra_su_nombre_sin_etiqueta(client):
    staff = _staff(client)
    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
        staff_actor=staff,
    )
    client.db.commit()

    r = client.get("/consultar", params={"q": p.access_code})
    assert r.status_code == 200
    assert staff.nombre in r.text
    assert "(cliente)" not in r.text and "(staff)" not in r.text


def test_timeline_muestra_el_actor_de_cada_transicion_por_separado(client):
    staff = _staff(client)
    p = _anunciar(client, nombre="Ana")
    receive(client.db, p, staff)
    deliver(client.db, p, staff)
    client.db.commit()

    r = client.get("/consultar", params={"q": p.access_code})
    html = r.text
    # El badge de estado (arriba de la página) también puede decir "Entregado"
    # -- se busca desde el arranque del timeline para no confundirlo con eso.
    idx_timeline = html.index('<ol class="relative">')
    idx_recibido = html.index("Recibido", idx_timeline)
    idx_entregado = html.index("Entregado", idx_timeline)
    # El staff aparece asociado a Recibido y Entregado (mismo actor en ambos
    # en este test, pero cada hito debe llevar su propia etiqueta).
    assert staff.nombre in html[idx_recibido:idx_entregado]
    assert staff.nombre in html[idx_entregado:]


# --------------------------------------------------------------------------- #
# Botón "Entregar" para staff (issue 124) — visible solo con sesión de staff
# y paquete en RECIBIDO; el POST reusa `/paquetes/{id}/entregar` pero vuelve
# a esta vista (con el mismo término) en vez de a `/paquetes`.
# --------------------------------------------------------------------------- #
def test_boton_entregar_no_aparece_sin_sesion_de_staff(client):
    staff = _staff(client)
    p = _anunciar(client)
    receive(client.db, p, staff)
    client.db.commit()

    r = client.get("/consultar", params={"q": p.access_code})
    assert r.status_code == 200
    assert 'data-open="modal-entregar-consultar"' not in r.text


def test_boton_entregar_no_aparece_si_el_paquete_no_esta_recibido(client):
    staff = _staff(client)
    _login_staff(client, staff)
    p = _anunciar(client)  # sigue ANUNCIADO

    r = client.get("/consultar", params={"q": p.access_code})
    assert r.status_code == 200
    assert 'data-open="modal-entregar-consultar"' not in r.text


def test_boton_entregar_aparece_con_sesion_de_staff_y_paquete_recibido(client):
    staff = _staff(client)
    _login_staff(client, staff)
    p = _anunciar(client)
    receive(client.db, p, staff)
    client.db.commit()

    r = client.get("/consultar", params={"q": p.access_code})
    assert r.status_code == 200
    assert 'data-open="modal-entregar-consultar"' in r.text
    assert f'action="/paquetes/{p.id}/entregar"' in r.text
    assert 'name="origen" value="consultar"' in r.text


def test_boton_entregar_sin_guia_no_muestra_confirmar_guia(client):
    staff = _staff(client)
    _login_staff(client, staff)
    p = _anunciar(client)
    receive(client.db, p, staff)  # sin guide_number
    client.db.commit()

    r = client.get("/consultar", params={"q": p.access_code})
    assert r.status_code == 200
    assert "Confirmar guía" not in r.text
    assert 'class="scan-btn' not in r.text


def test_boton_entregar_con_guia_muestra_confirmar_guia_y_escaneo(client):
    staff = _staff(client)
    _login_staff(client, staff)
    p = _anunciar(client)
    receive(client.db, p, staff, "GUIA-XYZ-001")
    client.db.commit()

    r = client.get("/consultar", params={"q": p.access_code})
    assert r.status_code == 200
    assert "Confirmar guía" in r.text
    assert 'data-guia-esperada="GUIA-XYZ-001"' in r.text
    assert 'class="scan-btn' in r.text
    assert "guia-check-msg" in r.text


def test_entregar_desde_consultar_redirige_de_vuelta_con_el_mismo_termino(client):
    staff = _staff(client)
    _login_staff(client, staff)
    p = _anunciar(client)
    receive(client.db, p, staff)
    client.db.commit()

    r = client.post(
        f"/paquetes/{p.id}/entregar",
        data={"origen": "consultar", "q": p.access_code},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/consultar?q={p.access_code}"

    client.db.expire_all()
    assert client.db.get(Paquete, p.id).estado == EstadoPaquete.ENTREGADO
