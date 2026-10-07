"""Issue 433 (.scratch/pendientes-cliente): los mensajes de WhatsApp se editan desde /administracion/notificaciones --
el botón de /paquetes usa la pestaña WhatsApp del estado del paquete, y hay una fila "Solicitud de autorización" para
el de /announce. Formato enriquecido (negrillas, emojis, saltos de línea) de punta a punta."""

import html
import re
from urllib.parse import quote

from app.domain.notificacion_service import guardar_plantilla, texto_solicitud_autorizacion
from app.domain.paquete import EstadoPaquete
from app.domain.paquete_service import Destinatario, announce
from app.domain.preferencia_notificacion import CanalNotificacion
from app.domain.staff_service import create_initial_admin

_PW = "Contrasena1"


def _login_admin(client):
    create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": "admin@club.com", "password": _PW})


def _modal_solicitud(texto):
    i = texto.rindex(">SOLICITAR AUTORIZACION<")
    inicio = texto.rindex('<div id="modal-notif-', 0, i)
    fin = texto.find('<div id="modal-', inicio + 1)
    return texto[inicio : fin if fin != -1 else len(texto)]


def test_el_boton_whatsapp_de_paquetes_usa_la_pestana_whatsapp_editada(client):
    _login_admin(client)
    p = announce(client.db, anunciante_telefono="3001234567", anunciante_nombre="Ana",
                 destinatario=Destinatario.yo_mismo())
    guardar_plantilla(client.db, EstadoPaquete.ANUNCIADO, None, "📦 *Hola {recipient_name}*\nCódigo: {access_code}",
                      canal=CanalNotificacion.WHATSAPP)
    client.db.commit()

    r = client.get("/paquetes")

    esperado = quote(f"📦 *Hola ANA*\nCódigo: {p.access_code}", safe="")
    assert f'href="https://api.whatsapp.com/send/?phone=573001234567&amp;type=phone_number&amp;text={esperado}"' in r.text
    assert f'href="https://web.whatsapp.com/send?phone=573001234567&amp;text={esperado}"' in r.text


def test_notificaciones_tiene_la_fila_solicitud_de_autorizacion_solo_con_whatsapp(client):
    _login_admin(client)

    r = client.get("/administracion/notificaciones")

    assert r.status_code == 200
    modal = _modal_solicitud(r.text)
    assert 'data-canal="WHATSAPP"' in modal
    assert 'data-canal="SMS"' not in modal and 'data-canal="EMAIL"' not in modal
    assert "¡Buen dia veci!" in html.unescape(modal)  # el texto vigente, listo para editar


def test_guardar_la_solicitud_con_emojis_y_saltos_la_usa_announce(client):
    _login_admin(client)

    r = client.post("/administracion/notificaciones", data={
        "evento": "PEDIR_AUTORIZACION", "motivo": "", "canal": "WHATSAPP",
        "texto": "¡Hola veci! 👋\r\n*¿Nos autoriza recibirlo?*",
    })

    assert r.status_code == 200
    client.db.expire_all()
    assert texto_solicitud_autorizacion(client.db) == "¡Hola veci! 👋\n*¿Nos autoriza recibirlo?*"


def test_la_solicitud_solo_admite_el_canal_whatsapp(client):
    _login_admin(client)

    r = client.post("/administracion/notificaciones", data={
        "evento": "PEDIR_AUTORIZACION", "motivo": "", "canal": "SMS", "texto": "Hola",
    })

    assert r.status_code == 400


def test_whatsapp_rechaza_variable_desconocida_y_texto_de_mas_de_500(client):
    _login_admin(client)

    r = client.post("/administracion/notificaciones", data={
        "evento": "RECIBIDO", "motivo": "", "canal": "WHATSAPP", "texto": "Hola {nombre}",
    })
    assert r.status_code == 400 and "{nombre}" in html.unescape(r.text)

    r = client.post("/administracion/notificaciones", data={
        "evento": "RECIBIDO", "motivo": "", "canal": "WHATSAPP", "texto": "x" * 501,
    })
    assert r.status_code == 400 and "500" in r.text


def test_la_pestana_whatsapp_trae_editor_de_formato_y_vista_previa(client):
    _login_admin(client)
    guardar_plantilla(client.db, EstadoPaquete.RECIBIDO, None, "*Hola* _veci_ ~no~\nlínea 2",
                      canal=CanalNotificacion.WHATSAPP)
    client.db.commit()

    r = client.get("/administracion/notificaciones")

    # Botones de formato, todas las variables y la vista previa (negrilla/cursiva/tachado y saltos ya aplicados).
    assert 'data-formato-whatsapp="*"' in r.text and 'data-formato-whatsapp="_"' in r.text
    assert "{link}" in r.text and "{estado}" in r.text
    assert re.search(r"<strong>Hola</strong> <em>veci</em> <s>no</s><br>línea 2", r.text)
