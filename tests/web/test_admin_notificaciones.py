# -*- coding: utf-8 -*-
"""
Capa web — `/administracion/notificaciones` (Grupo 8, ticket 02).

Comportamiento observable por HTTP: gate require_admin (mismo patrón que
`/administracion/personal`); sin plantilla previa, el campo muestra el texto
por defecto; guardar persiste la plantilla personalizada.
"""

from app.domain.email_sender import ConsoleEmailSender
from app.domain.notification_sender import ConsoleNotificationSender
from app.domain.notificacion_service import obtener_asunto_actual, obtener_texto_actual
from app.domain.paquete import EstadoPaquete
from app.domain.preferencia_notificacion import CanalNotificacion
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario
from app.web.notifications import get_notification_sender
from app.web.password_reset import get_email_sender

_PW = "Contrasena1"


def _login_admin(client, email="admin@club.com"):
    create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def test_sin_sesion_redirige_a_login(client):
    r = client.get("/administracion/notificaciones", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_operador_es_rechazado_403(client):
    _login_operador(client)
    r = client.get("/administracion/notificaciones")
    assert r.status_code == 403


def test_admin_ve_las_plantillas_con_el_texto_por_defecto(client):
    _login_admin(client)
    r = client.get("/administracion/notificaciones")
    assert r.status_code == 200
    assert "esta {estado}" in r.text  # default de RECIBIDO, sin override (issue 222)


def test_guardar_persiste_la_plantilla_personalizada(client):
    _login_admin(client)
    r = client.post(
        "/administracion/notificaciones",
        data={
            "evento": "RECIBIDO",
            "motivo": "",
            "texto": "Hola {recipient_name}, ya llegó tu encomienda.",
        },
    )
    assert r.status_code == 200
    assert "ya llegó tu encomienda" in r.text

    client.db.expire_all()
    texto = obtener_texto_actual(client.db, EstadoPaquete.RECIBIDO)
    assert texto == "Hola {recipient_name}, ya llegó tu encomienda."


def test_guardar_con_motivo_solo_afecta_ese_motivo(client):
    _login_admin(client)
    client.post(
        "/administracion/notificaciones",
        data={
            "evento": "CANCELADO",
            "motivo": "NO_RECLAMADO",
            "texto": "Tu paquete {recipient_name} no fue reclamado a tiempo.",
        },
    )

    client.db.expire_all()
    texto_no_reclamado = obtener_texto_actual(
        client.db, EstadoPaquete.CANCELADO, "NO_RECLAMADO"
    )
    texto_otro = obtener_texto_actual(client.db, EstadoPaquete.CANCELADO, "OTRO")

    assert "no fue reclamado a tiempo" in texto_no_reclamado
    assert "no fue reclamado a tiempo" not in texto_otro


def test_texto_vacio_rechaza(client):
    _login_admin(client)
    r = client.post(
        "/administracion/notificaciones",
        data={"evento": "RECIBIDO", "motivo": "", "texto": "   "},
    )
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# issue 202 (.scratch/pendientes-cliente): ANUNCIADO deja de distinguir
# Cliente/Staff (Grupo 19, Ronda 2, revertido) -- el aviso siempre llega al
# mismo destinatario final sin importar quién anunció, así que sobraba
# tener dos plantillas separadas. Ahora se comporta igual que RECIBIDO/
# ENTREGADO: una sola fila, sin motivo.
# --------------------------------------------------------------------------- #
def test_admin_ve_una_sola_fila_de_anunciado_sin_motivo(client):
    _login_admin(client)
    r = client.get("/administracion/notificaciones")
    assert r.status_code == 200
    assert ">ANUNCIADO<" in r.text
    assert "ANUNCIADO · Cliente" not in r.text
    assert "ANUNCIADO · Staff" not in r.text


def test_notificar_anunciado_usa_la_misma_plantilla_sin_importar_quien_anuncio(client):
    from app.domain.notification_sender import ConsoleNotificationSender
    from app.domain.notificacion_service import notificar_evento
    from app.domain.paquete_service import Destinatario, announce
    from app.domain.usuario import Usuario

    _login_admin(client)
    admin = client.db.query(Usuario).one()

    p_cliente = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    p_staff = announce(
        client.db,
        anunciante_telefono="3009999999",
        anunciante_nombre="Beto",
        destinatario=Destinatario.yo_mismo(),
        staff_actor=admin,
    )
    client.db.commit()

    sender = ConsoleNotificationSender()
    notificar_evento(client.db, p_cliente, EstadoPaquete.ANUNCIADO, sender)
    notificar_evento(client.db, p_staff, EstadoPaquete.ANUNCIADO, sender)

    assert len(sender.enviados) == 2
    assert "Anunciado" in sender.enviados[0][1]
    assert "Anunciado" in sender.enviados[1][1]  # mismo texto


# --------------------------------------------------------------------------- #
# `.scratch/plantillas-notificacion-multicanal`, ticket 02 — pestañas
# SMS/Email/WhatsApp por evento.
# --------------------------------------------------------------------------- #
def test_pantalla_muestra_3_pestanas_por_cada_una_de_las_7_filas(client):
    # 7 filas: ANUNCIADO + RECIBIDO + ENTREGADO + CANCELADO x4 (un
    # MotivoCancelacion cada una) -- ANUNCIADO dejó de distinguir
    # Cliente/Staff en issue 202 (.scratch/pendientes-cliente).
    _login_admin(client)
    r = client.get("/administracion/notificaciones")
    assert r.status_code == 200
    for canal in ("SMS", "EMAIL", "WHATSAPP"):
        assert r.text.count(f'data-canal="{canal}"') == 7


def test_guardar_email_no_afecta_el_sms_del_mismo_evento(client):
    _login_admin(client)
    r = client.post(
        "/administracion/notificaciones",
        data={
            "evento": "RECIBIDO",
            "motivo": "",
            "canal": "EMAIL",
            "asunto": "Tu paquete llegó a portería",
            "texto": "Cuerpo de correo personalizado.",
        },
    )
    assert r.status_code == 200

    client.db.expire_all()
    texto_email = obtener_texto_actual(
        client.db, EstadoPaquete.RECIBIDO, canal=CanalNotificacion.EMAIL
    )
    asunto_email = obtener_asunto_actual(client.db, EstadoPaquete.RECIBIDO)
    texto_sms = obtener_texto_actual(client.db, EstadoPaquete.RECIBIDO)  # default canal=SMS

    assert texto_email == "Cuerpo de correo personalizado."
    assert asunto_email == "Tu paquete llegó a portería"
    assert "esta {estado}" in texto_sms  # sigue siendo el default de SMS, sin tocar (issue 222)


def test_asunto_vacio_en_email_rechaza_sin_borrar_el_existente(client):
    _login_admin(client)
    client.post(
        "/administracion/notificaciones",
        data={
            "evento": "RECIBIDO",
            "motivo": "",
            "canal": "EMAIL",
            "asunto": "Asunto original",
            "texto": "Cuerpo original.",
        },
    )

    r = client.post(
        "/administracion/notificaciones",
        data={
            "evento": "RECIBIDO",
            "motivo": "",
            "canal": "EMAIL",
            "asunto": "   ",
            "texto": "Cuerpo nuevo.",
        },
    )
    assert r.status_code == 400

    client.db.expire_all()
    assert obtener_asunto_actual(client.db, EstadoPaquete.RECIBIDO) == "Asunto original"


def test_canal_invalido_rechaza(client):
    _login_admin(client)
    r = client.post(
        "/administracion/notificaciones",
        data={"evento": "RECIBIDO", "motivo": "", "canal": "FAX", "texto": "texto"},
    )
    assert r.status_code == 400


def test_pestana_email_tiene_asunto_y_no_la_lista_de_variables(client):
    _login_admin(client)
    r = client.get("/administracion/notificaciones")
    assert r.text.count('aria-label="Asunto"') == 7
    # "Variables disponibles" solo se muestra en SMS/WhatsApp -- 2 de los 3
    # canales, en cada una de las 7 filas.
    assert r.text.count("Variables disponibles") == 14


# --------------------------------------------------------------------------- #
# .scratch/plantillas-notificacion-multicanal / pendientes-cliente issue 200
# -- layout de acordeón (elegido tras prototipar 3 alternativas en vivo).
# Reemplazado por issue 203 (.scratch/pendientes-cliente): cada fila abre en
# su propio modal en vez de acordeón -- la lista principal queda como
# botones compactos, sin nada expandido en la página.
# --------------------------------------------------------------------------- #
def _tag_modal_de(html_text, titulo):
    """El `<div id="modal-notif-N" ...>` cuyo `<h2>` de título contiene
    `titulo` (ej. 'RECIBIDO' o 'CANCELADO · No reclamado'). El mismo texto
    aparece antes en el botón de la lista compacta -- se toma la ÚLTIMA
    ocurrencia, que es el `<h2>` del modal (el template emite la lista de
    botones primero y los modales después, igual que admin/staff.html)."""
    i = html_text.rindex(f">{titulo}<")
    inicio = html_text.rindex('<div id="modal-notif-', 0, i)
    fin = html_text.index(">", inicio)
    return html_text[inicio : fin + 1]


def test_todas_las_filas_cerradas_por_defecto(client):
    _login_admin(client)
    r = client.get("/administracion/notificaciones")
    assert "hidden" in _tag_modal_de(r.text, "ANUNCIADO")
    assert "hidden" in _tag_modal_de(r.text, "RECIBIDO")


def test_error_en_fila_abre_su_propio_modal(client):
    _login_admin(client)
    r = client.post(
        "/administracion/notificaciones",
        data={"evento": "RECIBIDO", "motivo": "", "canal": "SMS", "texto": "   "},
    )
    assert r.status_code == 400
    assert "hidden" not in _tag_modal_de(r.text, "RECIBIDO")
    # issue 203: solo el modal con el error propio se abre, ningún otro.
    assert "hidden" in _tag_modal_de(r.text, "ANUNCIADO")


def test_guardar_abre_su_propio_modal(client):
    _login_admin(client)
    r = client.post(
        "/administracion/notificaciones",
        data={"evento": "RECIBIDO", "motivo": "", "canal": "SMS", "texto": "Ya llegó."},
    )
    assert r.status_code == 200
    assert "hidden" not in _tag_modal_de(r.text, "RECIBIDO")


# --------------------------------------------------------------------------- #
# .scratch/notificaciones-enviar-prueba, ticket 02 -- enviar mensaje de
# prueba real por SMS/Email desde /administracion/notificaciones.
# --------------------------------------------------------------------------- #
def _forzar_sms_configurado(monkeypatch):
    from app.domain import sns_sender

    monkeypatch.setattr(sns_sender, "sns_habilitado", lambda: True)


def _forzar_email_configurado(monkeypatch):
    from app.domain import smtp_email_sender

    monkeypatch.setattr(smtp_email_sender, "configurado", lambda: True)


def test_probar_sms_envia_la_plantilla_ya_guardada_con_variables_resueltas(client, monkeypatch):
    _login_admin(client)
    _forzar_sms_configurado(monkeypatch)
    sender = ConsoleNotificationSender()
    client.app.dependency_overrides[get_notification_sender] = lambda: sender

    r = client.post(
        "/administracion/notificaciones/probar",
        data={"evento": "RECIBIDO", "motivo": "", "canal": "SMS", "destino": "3001234567"},
    )
    assert r.status_code == 200

    assert len(sender.enviados) == 1
    destino, texto = sender.enviados[0]
    # Normalizado a E.164 antes de llegar al sender (2026-09-01, diagnóstico
    # en vivo): AWS SNS acepta un `PhoneNumber` sin indicativo de país y
    # devuelve 200 igual, pero el mensaje nunca llega -- ver `normalizar_telefono()`.
    assert destino == "+573001234567"
    assert "Juan Pérez" in texto
    assert "{recipient_name}" not in texto


def test_probar_email_envia_con_asunto_y_cuerpo_de_marca(client, monkeypatch):
    _login_admin(client)
    _forzar_email_configurado(monkeypatch)
    sender = ConsoleEmailSender()
    client.app.dependency_overrides[get_email_sender] = lambda: sender

    r = client.post(
        "/administracion/notificaciones/probar",
        data={"evento": "RECIBIDO", "motivo": "", "canal": "EMAIL", "destino": "admin@test.com"},
    )
    assert r.status_code == 200

    assert len(sender.enviados) == 1
    destino, asunto, cuerpo, cuerpo_html = sender.enviados[0]
    assert destino == "admin@test.com"
    assert asunto  # el default de Email para RECIBIDO
    assert "papyrus-logo.png" in cuerpo_html  # mismo layout de marca del preview retirado


def test_probar_whatsapp_rechaza_sin_llamar_a_ningun_sender(client):
    _login_admin(client)
    sender = ConsoleNotificationSender()
    client.app.dependency_overrides[get_notification_sender] = lambda: sender

    r = client.post(
        "/administracion/notificaciones/probar",
        data={"evento": "RECIBIDO", "motivo": "", "canal": "WHATSAPP", "destino": "3001234567"},
    )
    assert r.status_code == 400
    assert sender.enviados == []
    # issue 03 (.scratch/notificaciones-enviar-prueba): mensaje explícito de
    # "canal no configurado", no un genérico "canal inválido" -- WHATSAPP ES
    # un canal válido, solo que sin proveedor todavía.
    assert "WHATSAPP no está configurado todavía." in r.text


def test_probar_destino_vacio_rechaza_sin_enviar(client, monkeypatch):
    _login_admin(client)
    _forzar_sms_configurado(monkeypatch)
    sender = ConsoleNotificationSender()
    client.app.dependency_overrides[get_notification_sender] = lambda: sender

    r = client.post(
        "/administracion/notificaciones/probar",
        data={"evento": "RECIBIDO", "motivo": "", "canal": "SMS", "destino": "   "},
    )
    assert r.status_code == 400
    assert sender.enviados == []


def test_probar_evento_invalido_rechaza(client):
    _login_admin(client)
    r = client.post(
        "/administracion/notificaciones/probar",
        data={"evento": "NOEXISTE", "motivo": "", "canal": "SMS", "destino": "3001234567"},
    )
    assert r.status_code == 400


def test_probar_canal_invalido_rechaza(client):
    _login_admin(client)
    r = client.post(
        "/administracion/notificaciones/probar",
        data={"evento": "RECIBIDO", "motivo": "", "canal": "FAX", "destino": "3001234567"},
    )
    assert r.status_code == 400


def test_probar_falla_del_proveedor_se_muestra_como_error_no_se_traga(client, monkeypatch):
    _login_admin(client)
    _forzar_sms_configurado(monkeypatch)

    class _SenderQueFalla:
        def enviar(self, destino, mensaje):
            raise RuntimeError("proveedor caído")

    client.app.dependency_overrides[get_notification_sender] = lambda: _SenderQueFalla()

    r = client.post(
        "/administracion/notificaciones/probar",
        data={"evento": "RECIBIDO", "motivo": "", "canal": "SMS", "destino": "3001234567"},
    )
    assert r.status_code == 400
    assert "no se pudo enviar" in r.text.lower()


def test_probar_operador_recibe_403(client):
    _login_operador(client)
    r = client.post(
        "/administracion/notificaciones/probar",
        data={"evento": "RECIBIDO", "motivo": "", "canal": "SMS", "destino": "3001234567"},
    )
    assert r.status_code == 403


def test_probar_sin_sesion_redirige_a_login(client):
    r = client.post(
        "/administracion/notificaciones/probar",
        data={"evento": "RECIBIDO", "motivo": "", "canal": "SMS", "destino": "3001234567"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_probar_no_afecta_el_texto_guardado_ni_el_historial(client, monkeypatch):
    from app.domain.plantilla_notificacion_historial import PlantillaNotificacionHistorial

    _login_admin(client)
    _forzar_sms_configurado(monkeypatch)
    client.app.dependency_overrides[get_notification_sender] = lambda: ConsoleNotificationSender()

    texto_antes = obtener_texto_actual(client.db, EstadoPaquete.RECIBIDO)
    client.post(
        "/administracion/notificaciones/probar",
        data={"evento": "RECIBIDO", "motivo": "", "canal": "SMS", "destino": "3001234567"},
    )

    client.db.expire_all()
    assert obtener_texto_actual(client.db, EstadoPaquete.RECIBIDO) == texto_antes
    assert client.db.query(PlantillaNotificacionHistorial).count() == 0


def test_probar_error_abre_su_propio_modal(client, monkeypatch):
    _login_admin(client)
    _forzar_sms_configurado(monkeypatch)
    client.app.dependency_overrides[get_notification_sender] = lambda: ConsoleNotificationSender()

    r = client.post(
        "/administracion/notificaciones/probar",
        data={"evento": "RECIBIDO", "motivo": "", "canal": "SMS", "destino": "   "},
    )
    assert r.status_code == 400
    assert "hidden" not in _tag_modal_de(r.text, "RECIBIDO")


def test_probar_exito_abre_su_propio_modal(client, monkeypatch):
    _login_admin(client)
    _forzar_sms_configurado(monkeypatch)
    client.app.dependency_overrides[get_notification_sender] = lambda: ConsoleNotificationSender()

    r = client.post(
        "/administracion/notificaciones/probar",
        data={"evento": "RECIBIDO", "motivo": "", "canal": "SMS", "destino": "3001234567"},
    )
    assert r.status_code == 200
    assert "hidden" not in _tag_modal_de(r.text, "RECIBIDO")


def test_boton_deshabilitado_cuando_ningun_proveedor_esta_configurado(client):
    # Entorno de test: sin credenciales de ningún proveedor -- SMS y Email
    # aparecen deshabilitados en las 7 filas (mismo patrón que reutilizará
    # el botón de WhatsApp en el ticket 03).
    _login_admin(client)
    r = client.get("/administracion/notificaciones")
    assert r.text.count("SMS no está configurado todavía.") == 7
    assert r.text.count("Email no está configurado todavía.") == 7


def test_boton_habilitado_cuando_el_canal_esta_configurado(client, monkeypatch):
    _forzar_sms_configurado(monkeypatch)
    _login_admin(client)
    r = client.get("/administracion/notificaciones")
    assert "SMS no está configurado todavía." not in r.text
    # Email sigue sin proveedor -- solo SMS se forzó.
    assert r.text.count("Email no está configurado todavía.") == 7


def test_destino_preellenado_con_telefono_y_email_del_admin(client):
    from app.domain.staff_service import editar_mi_perfil
    from app.domain.usuario import Usuario

    _login_admin(client)
    admin = client.db.query(Usuario).filter_by(email="admin@club.com").one()
    editar_mi_perfil(client.db, admin, admin.nombre, telefono="3001112222")
    client.db.commit()

    r = client.get("/administracion/notificaciones")
    assert 'value="3001112222"' in r.text
    assert 'value="admin@club.com"' in r.text


# --------------------------------------------------------------------------- #
# .scratch/notificaciones-enviar-prueba, ticket 03 -- botón de "Enviar
# prueba" de WhatsApp, siempre visible pero deshabilitado (sin proveedor
# todavía). El rechazo del servidor y "ningún sender recibe nada" ya quedan
# cubiertos por `test_probar_whatsapp_rechaza_sin_llamar_a_ningun_sender`
# (ticket 02) -- acá solo falta la UI de esa pestaña.
# --------------------------------------------------------------------------- #
def test_whatsapp_muestra_boton_de_prueba_deshabilitado_con_nota(client):
    _login_admin(client)
    r = client.get("/administracion/notificaciones")
    # 7 filas × 3 canales (SMS/Email/WhatsApp) ahora tienen su propio campo
    # de destino -- antes del ticket 03 solo SMS/Email lo tenían (14).
    assert r.text.count('name="destino"') == 21
    assert r.text.count("WhatsApp no está configurado todavía.") == 7


def test_whatsapp_destino_preellenado_con_el_whatsapp_del_admin(client):
    from app.domain.staff_service import editar_mi_perfil
    from app.domain.usuario import Usuario

    _login_admin(client)
    admin = client.db.query(Usuario).filter_by(email="admin@club.com").one()
    editar_mi_perfil(client.db, admin, admin.nombre, whatsapp="3005556666")
    client.db.commit()

    r = client.get("/administracion/notificaciones")
    assert 'value="3005556666"' in r.text


def test_guardar_texto_de_whatsapp_sigue_funcionando_con_el_boton_de_prueba_deshabilitado(client):
    _login_admin(client)
    r = client.post(
        "/administracion/notificaciones",
        data={
            "evento": "RECIBIDO",
            "motivo": "",
            "canal": "WHATSAPP",
            "texto": "Ya llegó tu paquete por WhatsApp.",
        },
    )
    assert r.status_code == 200

    client.db.expire_all()
    assert (
        obtener_texto_actual(client.db, EstadoPaquete.RECIBIDO, canal=CanalNotificacion.WHATSAPP)
        == "Ya llegó tu paquete por WhatsApp."
    )
