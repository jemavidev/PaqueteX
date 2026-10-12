"""
Vista "Avisos especiales" (`.scratch/avisos-especiales`, tickets 03-07): el ADMIN ve, un cliente por fila, a quienes
tienen algo pendiente en Papyrus (Anunciados de los últimos 5 días calendario y todos los Recibidos) y les avisa por
SMS o WhatsApp que ciertos días no hay servicio.
"""

import re
from datetime import datetime, timedelta, timezone

from app.domain.paquete_lifecycle import cancel, deliver, receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario, Usuario
from app.domain.zona_horaria import ZONA_HORARIA_APP

_PW = "Contrasena1"
_URL = "/administracion/avisos-especiales"


def _login_admin(client, email="admin@club.com"):
    create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})
    return client.db.query(Usuario).filter(Usuario.email == email).one()


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _anunciar(client, tel="3001234567", nombre="Ana", hace_dias=0, apartamento=None):
    """Un Paquete ANUNCIADO por `tel` para sí mismo; `hace_dias` corre su fecha de anuncio a ese día calendario
    (hora de Colombia), al mediodía."""
    p = announce(
        client.db,
        anunciante_telefono=tel,
        anunciante_nombre=nombre,
        destinatario=Destinatario.yo_mismo(),
        apartamento=apartamento,
    )
    if hace_dias:
        dia = datetime.now(ZONA_HORARIA_APP).date() - timedelta(days=hace_dias)
        p.announced_at = datetime(dia.year, dia.month, dia.day, 12, tzinfo=ZONA_HORARIA_APP).astimezone(timezone.utc)
    client.db.commit()
    return p


def _filas(html):
    """El texto de cada fila de cliente (escritorio), en orden."""
    filas = re.findall(r"<tr[^>]*data-cliente-aviso[^>]*>(.*?)</tr>", html, re.S)
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", f)).strip() for f in filas]


# --------------------------------------------------------------------------- #
# Ticket 03 -- la lista de clientes
# --------------------------------------------------------------------------- #
def test_admin_ve_a_los_clientes_con_paquetes_recibidos(client):
    admin = _login_admin(client)
    p = _anunciar(client, tel="3001234567", nombre="Ana")
    receive(client.db, p, admin)
    client.db.commit()

    r = client.get(_URL)

    assert r.status_code == 200
    filas = _filas(r.text)
    assert len(filas) == 1
    assert "ANA" in filas[0] and p.access_code in filas[0]


def test_sin_sesion_redirige_a_ingresar_y_operador_es_rechazado(client):
    r = client.get(_URL, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/ingresar")

    _login_operador(client)
    assert client.get(_URL).status_code == 403


def test_anunciados_de_los_ultimos_5_dias_calendario_entran_y_los_mas_viejos_no(client):
    _login_admin(client)
    _anunciar(client, tel="3001111111", nombre="Hoy")
    _anunciar(client, tel="3002222222", nombre="Hace cuatro", hace_dias=4)
    _anunciar(client, tel="3003333333", nombre="Hace cinco", hace_dias=5)

    texto = " ".join(_filas(client.get(_URL).text))

    assert "HOY" in texto and "HACE CUATRO" in texto
    assert "HACE CINCO" not in texto


def test_recibidos_viejos_entran_y_entregados_o_cancelados_nunca(client):
    admin = _login_admin(client)
    viejo = _anunciar(client, tel="3001111111", nombre="Recibido viejo", hace_dias=40)
    receive(client.db, viejo, admin)
    entregado = _anunciar(client, tel="3002222222", nombre="Entregado")
    receive(client.db, entregado, admin)
    deliver(client.db, entregado, admin)
    cancelado = _anunciar(client, tel="3003333333", nombre="Cancelado")
    cancel(client.db, cancelado, admin, motivo="Otro")
    client.db.commit()

    texto = " ".join(_filas(client.get(_URL).text))

    assert "RECIBIDO VIEJO" in texto
    assert "ENTREGADO" not in texto and "CANCELADO" not in texto


def test_un_cliente_con_varios_paquetes_es_una_sola_fila_con_todos_sus_codigos(client):
    admin = _login_admin(client)
    uno = _anunciar(client, tel="3001234567", nombre="Ana")
    dos = _anunciar(client, tel="3001234567", nombre="Ana")
    receive(client.db, dos, admin)
    client.db.commit()

    filas = _filas(client.get(_URL).text)

    assert len(filas) == 1
    assert uno.access_code in filas[0] and dos.access_code in filas[0]


def test_cada_codigo_enlaza_a_consultar_con_el_color_de_su_estado(client):
    _login_admin(client)
    p = _anunciar(client)

    html = client.get(_URL).text

    enlace = re.search(rf'<a href="/consultar\?q={p.access_code}"[^>]*>', html).group(0)
    assert "bg-amber-400" in enlace  # Anunciado


def test_sin_clientes_pendientes_muestra_un_estado_vacio(client):
    _login_admin(client)

    html = client.get(_URL).text

    assert "No hay clientes con paquetes pendientes." in html
    assert _filas(html) == []


def test_el_menu_de_cuenta_trae_avisos_especiales_solo_para_admin(client):
    _login_admin(client)
    assert f'href="{_URL}"' in client.get("/paquetes").text


def test_el_menu_de_cuenta_no_trae_avisos_especiales_al_operador(client):
    _login_operador(client)
    assert f'href="{_URL}"' not in client.get("/paquetes").text


def test_la_ventana_de_anunciados_empieza_a_medianoche_hora_colombia_de_hace_4_dias():
    from datetime import date

    from app.domain.aviso_especial_service import inicio_ventana_anunciados

    inicio = inicio_ventana_anunciados(date(2026, 10, 11))

    # Medianoche del 7 de octubre en Colombia (UTC-5) = 05:00 UTC.
    assert inicio == datetime(2026, 10, 7, 5, 0, tzinfo=timezone.utc)
    once_pm_del_6 = datetime(2026, 10, 6, 23, 0, tzinfo=ZONA_HORARIA_APP)  # 5 días calendario atrás: fuera
    assert once_pm_del_6 < inicio
    assert datetime(2026, 10, 7, 0, 0, tzinfo=ZONA_HORARIA_APP) >= inicio


# --------------------------------------------------------------------------- #
# Ticket 04 -- selector de mensaje y WhatsApp
# --------------------------------------------------------------------------- #
def _mensaje(client, nombre="Fin de semana", texto="Hola {nombre}: el sabado no hay servicio. Paquetes: {codigos}"):
    from app.domain.aviso_especial_service import crear_mensaje

    m = crear_mensaje(client.db, nombre, texto)
    client.db.commit()
    return m


def test_el_boton_de_whatsapp_abre_el_chat_con_el_mensaje_elegido_resuelto_para_ese_cliente(client):
    from urllib.parse import quote

    _login_admin(client)
    p = _anunciar(client, tel="3001234567", nombre="Ana")
    m = _mensaje(client)

    html = client.get(_URL, params={"mensaje": str(m.id)}).text

    texto = quote(f"Hola ANA: el sabado no hay servicio. Paquetes: {p.access_code}")
    assert f"https://api.whatsapp.com/send/?phone=573001234567&amp;type=phone_number&amp;text={texto}" in html
    assert f"https://web.whatsapp.com/send?phone=573001234567&amp;text={texto}" in html


def test_sin_eleccion_el_selector_usa_el_primer_mensaje_por_nombre_y_muestra_su_vista_previa(client):
    _login_admin(client)
    _anunciar(client)
    _mensaje(client, nombre="Puente festivo", texto="Lunes festivo sin servicio, {nombre}.")
    _mensaje(client, nombre="Fin de semana", texto="Sabado sin servicio, {nombre}.")

    html = client.get(_URL).text

    elegido = re.search(r"<option[^>]*selected[^>]*>([^<]*)</option>", html).group(1)
    assert elegido == "Fin de semana"
    previa = re.search(r"data-vista-previa-aviso>([^<]*)<", html).group(1)
    assert previa == "Sabado sin servicio, MARIA PEREZ."
    assert "Lunes+festivo" not in html and "Lunes%20festivo" not in html  # los botones usan el elegido


def test_un_mensaje_eliminado_o_un_id_invalido_cae_al_primero_vigente(client):
    from app.domain.aviso_especial_service import eliminar_mensaje

    _login_admin(client)
    _anunciar(client)
    borrado = _mensaje(client, nombre="Aaa viejo", texto="Viejo")
    _mensaje(client, nombre="Fin de semana", texto="Nuevo")
    eliminar_mensaje(client.db, borrado.id)
    client.db.commit()

    for valor in (str(borrado.id), "no-es-un-id"):
        html = client.get(_URL, params={"mensaje": valor}).text
        assert re.search(r"<option[^>]*selected[^>]*>([^<]*)</option>", html).group(1) == "Fin de semana"
        assert "Aaa viejo" not in html


def test_sin_mensajes_creados_avisa_con_enlace_a_notificaciones_y_apaga_el_whatsapp(client):
    _login_admin(client)
    _anunciar(client)

    html = client.get(_URL).text

    assert "Todavía no hay mensajes especiales." in html
    assert 'href="/administracion/notificaciones"' in html
    assert "api.whatsapp.com" not in html
    assert 'aria-label="WhatsApp: Elige un mensaje primero"' in html


def test_el_whatsapp_es_solo_un_enlace_sin_ruta_de_servidor_ni_registro(client):
    _login_admin(client)
    _anunciar(client)
    m = _mensaje(client)

    html = client.get(_URL, params={"mensaje": str(m.id)}).text

    whatsapp = re.findall(r'<a href="(https://(?:api|web)\.whatsapp\.com[^"]*)"', html)
    assert len(whatsapp) == 4  # celular + escritorio, en la tarjeta y en la fila
    assert all("target" in a for a in re.findall(r'<a href="https://(?:api|web)\.whatsapp\.com[^>]*>', html))


# --------------------------------------------------------------------------- #
# Ticket 05 -- enviar SMS y "Último SMS"
# --------------------------------------------------------------------------- #
_SMS = "/administracion/avisos-especiales/sms"


class _SenderFalso:
    """Registra lo enviado; `falla=True` simula que ningún proveedor lo entregó."""

    def __init__(self, falla=False, proveedor="AWS_SNS"):
        self.enviados, self.falla, self.proveedor = [], falla, proveedor

    def enviar(self, destino, mensaje):
        if self.falla:
            raise RuntimeError("proveedor caído")
        self.enviados.append((destino, mensaje))
        return self.proveedor


def _con_sender(client, monkeypatch, sender):
    from app.web import notifications
    from app.web.routes import avisos_especiales

    monkeypatch.setattr(avisos_especiales, "sms_configurado", lambda: True)
    client.app.dependency_overrides[notifications.get_notification_sender] = lambda: sender
    return sender


def _enviar(client, persona_id, mensaje_id, fetch=True):
    headers = {"X-Requested-With": "fetch"} if fetch else {}
    return client.post(
        _SMS, data={"persona_id": str(persona_id), "mensaje": str(mensaje_id)}, headers=headers,
        follow_redirects=False,
    )


def test_enviar_sms_manda_el_texto_resuelto_sin_tildes_y_la_fila_muestra_el_ultimo_sms(client, monkeypatch):
    _login_admin(client)
    p = _anunciar(client, tel="3001234567", nombre="Andrés")
    m = _mensaje(client, nombre="Fin de semana", texto="Hola {nombre}: el sábado no hay servicio. {codigos}")
    sender = _con_sender(client, monkeypatch, _SenderFalso())

    r = _enviar(client, p.announced_by_persona_id, m.id)

    assert r.status_code == 200
    assert sender.enviados == [("+573001234567", f"Hola ANDRES: el sabado no hay servicio. {p.access_code}")]
    fila = re.search(r"<tr[^>]*data-cliente-aviso[^>]*>.*?</tr>", r.text, re.S).group(0)
    assert "Último SMS: Fin de semana ·" in re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fila))


def _registros_sms(client):
    from app.domain.registro_sms import RegistroSms

    client.db.expire_all()
    return client.db.query(RegistroSms).all()


def _envios(client):
    from app.domain.mensaje_especial import EnvioAvisoEspecial

    client.db.expire_all()
    return client.db.query(EnvioAvisoEspecial).all()


def test_un_envio_exitoso_queda_registrado_como_aviso_especial_para_el_dashboard(client, monkeypatch):
    admin = _login_admin(client)
    p = _anunciar(client)
    m = _mensaje(client)
    _con_sender(client, monkeypatch, _SenderFalso(proveedor="AWS_SNS"))

    _enviar(client, p.announced_by_persona_id, m.id)

    [registro] = _registros_sms(client)
    assert (registro.tipo.value, registro.exitoso, registro.proveedor) == ("AVISO_ESPECIAL", True, "AWS_SNS")
    assert registro.paquete_id is None and registro.evento is None
    [envio] = _envios(client)
    assert (envio.persona_id, envio.nombre_mensaje, envio.usuario_id) == (p.announced_by_persona_id, "Fin de semana", admin.id)


def test_si_el_sms_falla_la_fila_avisa_en_rojo_y_no_cuenta_como_ultimo_sms(client, monkeypatch):
    _login_admin(client)
    p = _anunciar(client)
    m = _mensaje(client)
    _con_sender(client, monkeypatch, _SenderFalso(falla=True))

    r = _enviar(client, p.announced_by_persona_id, m.id)

    assert r.status_code == 200
    assert "No se pudo enviar el SMS. Intenta de nuevo." in r.text
    assert "Sin avisos enviados" in r.text
    assert _envios(client) == []
    [registro] = _registros_sms(client)
    assert (registro.tipo.value, registro.exitoso, registro.proveedor) == ("AVISO_ESPECIAL", False, None)


def test_reenviar_no_se_bloquea_y_el_ultimo_sms_es_el_mas_reciente_de_cualquier_mensaje(client, monkeypatch):
    _login_admin(client)
    p = _anunciar(client)
    fin_de_semana = _mensaje(client, nombre="Fin de semana", texto="Sabado")
    festivo = _mensaje(client, nombre="Festivo", texto="Lunes")
    sender = _con_sender(client, monkeypatch, _SenderFalso())

    _enviar(client, p.announced_by_persona_id, fin_de_semana.id)
    _enviar(client, p.announced_by_persona_id, fin_de_semana.id)
    _enviar(client, p.announced_by_persona_id, festivo.id)

    assert [t for _, t in sender.enviados] == ["Sabado", "Sabado", "Lunes"]
    fila = _filas(client.get(_URL, params={"mensaje": str(fin_de_semana.id)}).text)[0]
    assert "Último SMS: Festivo ·" in fila


def test_un_mensaje_eliminado_despues_de_enviarse_conserva_su_nombre_en_el_ultimo_sms(client, monkeypatch):
    from app.domain.aviso_especial_service import eliminar_mensaje

    _login_admin(client)
    p = _anunciar(client)
    m = _mensaje(client, nombre="Cierre inventario", texto="Cerrado")
    _mensaje(client, nombre="Fin de semana", texto="Sabado")
    _con_sender(client, monkeypatch, _SenderFalso())
    _enviar(client, p.announced_by_persona_id, m.id)
    eliminar_mensaje(client.db, m.id)
    client.db.commit()

    assert "Último SMS: Cierre inventario ·" in _filas(client.get(_URL).text)[0]


def test_enviar_con_un_mensaje_eliminado_responde_error_sin_enviar(client, monkeypatch):
    from app.domain.aviso_especial_service import eliminar_mensaje

    _login_admin(client)
    p = _anunciar(client)
    m = _mensaje(client)
    eliminar_mensaje(client.db, m.id)
    client.db.commit()
    sender = _con_sender(client, monkeypatch, _SenderFalso())

    r = _enviar(client, p.announced_by_persona_id, m.id)

    assert r.status_code == 400
    assert "Ese mensaje ya no existe. Elige otro." in r.text
    assert sender.enviados == [] and _registros_sms(client) == []


def test_enviar_a_quien_ya_no_tiene_paquetes_pendientes_responde_error(client, monkeypatch):
    admin = _login_admin(client)
    p = _anunciar(client)
    receive(client.db, p, admin)
    deliver(client.db, p, admin)
    client.db.commit()
    m = _mensaje(client)
    sender = _con_sender(client, monkeypatch, _SenderFalso())

    r = _enviar(client, p.announced_by_persona_id, m.id)

    assert r.status_code == 400
    assert "Ese cliente ya no tiene paquetes pendientes." in r.text
    assert sender.enviados == []


def test_sin_js_el_envio_redirige_a_la_vista_con_el_resultado(client, monkeypatch):
    _login_admin(client)
    p = _anunciar(client)
    m = _mensaje(client)
    _con_sender(client, monkeypatch, _SenderFalso())

    r = _enviar(client, p.announced_by_persona_id, m.id, fetch=False)

    assert r.status_code == 303
    assert r.headers["location"] == f"{_URL}?mensaje={m.id}&resultado=enviado"
    assert "SMS enviado." in client.get(r.headers["location"]).text


def test_sin_proveedor_de_sms_configurado_no_intenta_enviar(client, monkeypatch):
    from app.web.routes import avisos_especiales

    _login_admin(client)
    p = _anunciar(client)
    m = _mensaje(client)
    sender = _con_sender(client, monkeypatch, _SenderFalso())
    monkeypatch.setattr(avisos_especiales, "sms_configurado", lambda: False)

    r = _enviar(client, p.announced_by_persona_id, m.id)

    assert r.status_code == 400
    assert "El SMS no está configurado todavía" in r.text
    assert sender.enviados == []


def test_el_operador_no_puede_enviar_avisos(client, monkeypatch):
    _login_operador(client)
    p = _anunciar(client)
    m = _mensaje(client)
    sender = _con_sender(client, monkeypatch, _SenderFalso())

    assert _enviar(client, p.announced_by_persona_id, m.id).status_code == 403
    assert sender.enviados == []


# --------------------------------------------------------------------------- #
# Ticket 06 -- reglas de quién puede recibir SMS
# --------------------------------------------------------------------------- #
def _fila_html(html, persona_id):
    return re.search(rf'<tr[^>]*data-cliente-aviso="{persona_id}"[^>]*>.*?</tr>', html, re.S).group(0)


def _sms_apagado(fila_html):
    """El motivo del botón SMS apagado, o `None` si el botón está disponible."""
    if 'data-enviar-sms' in fila_html:
        return None
    return re.search(r'data-sms-apagado title="([^"]*)"', fila_html).group(1)


def test_un_cliente_de_baja_aparece_con_el_sms_apagado_y_no_se_le_puede_enviar(client, monkeypatch):
    from app.domain.persona import Persona
    from app.domain.persona_service import dar_de_baja_administrativa

    _login_admin(client)
    p = _anunciar(client)
    dar_de_baja_administrativa(client.db, client.db.get(Persona, p.announced_by_persona_id))
    client.db.commit()
    m = _mensaje(client)
    sender = _con_sender(client, monkeypatch, _SenderFalso())

    fila = _fila_html(client.get(_URL, params={"mensaje": str(m.id)}).text, p.announced_by_persona_id)

    assert _sms_apagado(fila) == "De baja"
    assert "web.whatsapp.com" in fila  # el WhatsApp sigue disponible
    r = _enviar(client, p.announced_by_persona_id, m.id)
    assert r.status_code == 400 and "De baja" in r.text
    assert sender.enviados == []


def test_un_cliente_solo_whatsapp_aparece_con_sin_telefono_y_su_whatsapp_disponible(client, monkeypatch):
    _login_admin(client)
    p = announce(client.db, anunciante_whatsapp="ana.whats", anunciante_nombre="Ana", destinatario=Destinatario.yo_mismo())
    client.db.commit()
    m = _mensaje(client)
    sender = _con_sender(client, monkeypatch, _SenderFalso())

    fila = _fila_html(client.get(_URL, params={"mensaje": str(m.id)}).text, p.announced_by_persona_id)

    assert _sms_apagado(fila) == "Sin teléfono"
    assert "username=ana.whats" in fila
    assert _enviar(client, p.announced_by_persona_id, m.id).status_code == 400
    assert sender.enviados == []


def test_sms_apagado_si_desactivo_el_sms_para_el_estado_de_todos_sus_paquetes(client, monkeypatch):
    from app.domain.paquete import EstadoPaquete
    from app.domain.preferencia_notificacion import CanalNotificacion
    from app.domain.preferencia_notificacion_service import guardar_preferencia

    admin = _login_admin(client)
    anunciado = _anunciar(client)
    recibido = _anunciar(client)
    receive(client.db, recibido, admin)
    pid = anunciado.announced_by_persona_id
    guardar_preferencia(client.db, pid, CanalNotificacion.SMS, EstadoPaquete.RECIBIDO, False)
    client.db.commit()
    m = _mensaje(client)
    _con_sender(client, monkeypatch, _SenderFalso())

    # Con SMS activo para Anunciado (uno de sus paquetes), el botón sigue disponible.
    assert _sms_apagado(_fila_html(client.get(_URL).text, pid)) is None

    guardar_preferencia(client.db, pid, CanalNotificacion.SMS, EstadoPaquete.ANUNCIADO, False)
    client.db.commit()
    assert _sms_apagado(_fila_html(client.get(_URL).text, pid)) == "Notificaciones SMS desactivadas"
    assert _enviar(client, pid, m.id).status_code == 400


def test_la_baja_tiene_prioridad_sobre_los_demas_motivos(client):
    from app.domain.aviso_especial_service import ClienteAviso, motivo_sin_sms
    from app.domain.persona import Persona
    from datetime import datetime as dt

    persona = Persona(nombre="X", telefono=None, baja_administrativa_en=dt.now(timezone.utc))

    assert motivo_sin_sms(client.db, ClienteAviso(persona)) == "De baja"


# --------------------------------------------------------------------------- #
# Ticket 07 -- búsqueda, filtro, conteos y paginación
# --------------------------------------------------------------------------- #
def _nombres(html):
    return [f.split(" ")[0] for f in _filas(html)]


def test_buscar_por_nombre_sin_tildes_ni_mayusculas_telefono_o_codigo(client):
    _login_admin(client)
    _anunciar(client, tel="3001111111", nombre="Andrés")
    beto = _anunciar(client, tel="3002222222", nombre="Beto")
    _anunciar(client, tel="3003333333", nombre="Carla")

    assert _nombres(client.get(_URL, params={"q": "andres"}).text) == ["ANDRÉS"]
    assert _nombres(client.get(_URL, params={"q": "300 222"}).text) == ["BETO"]
    assert _nombres(client.get(_URL, params={"q": beto.access_code.lower()}).text) == ["BETO"]
    assert _nombres(client.get(_URL, params={"q": "nadie"}).text) == []


def test_buscar_por_torre_y_apartamento(client):
    from app.domain.apartamento_service import resolver_apartamento

    _login_admin(client)
    _anunciar(client, tel="3001111111", nombre="Ana", apartamento=resolver_apartamento(client.db, "TORRE 2", "706"))
    _anunciar(client, tel="3002222222", nombre="Beto", apartamento=resolver_apartamento(client.db, "TORRE 4", "806"))
    client.db.commit()

    assert _nombres(client.get(_URL, params={"q": "706"}).text) == ["ANA"]
    assert _nombres(client.get(_URL, params={"q": "torre 4"}).text) == ["BETO"]


def test_filtro_por_estado_con_conteos_y_un_cliente_con_los_dos_estados_sale_en_ambos(client):
    admin = _login_admin(client)
    _anunciar(client, tel="3001111111", nombre="Solo anunciado")
    recibido = _anunciar(client, tel="3002222222", nombre="Solo recibido")
    receive(client.db, recibido, admin)
    _anunciar(client, tel="3003333333", nombre="Ambos")
    ambos = _anunciar(client, tel="3003333333", nombre="Ambos")
    receive(client.db, ambos, admin)
    client.db.commit()

    html = client.get(_URL).text
    assert re.search(r"data-conteo-estado=\"\"[^>]*>[^<]*3", html)
    assert re.search(r"data-conteo-estado=\"ANUNCIADO\"[^>]*>[^<]*2", html)
    assert re.search(r"data-conteo-estado=\"RECIBIDO\"[^>]*>[^<]*2", html)
    assert sorted(_nombres(client.get(_URL, params={"estado": "ANUNCIADO"}).text)) == ["AMBOS", "SOLO"]
    recibidos = _filas(client.get(_URL, params={"estado": "RECIBIDO"}).text)
    assert sorted(f.split(" ")[0] for f in recibidos) == ["AMBOS", "SOLO"]
    assert any("SOLO RECIBIDO" in f for f in recibidos)


def test_la_busqueda_en_vivo_devuelve_solo_los_resultados_y_conserva_el_mensaje(client):
    _login_admin(client)
    _anunciar(client, tel="3001111111", nombre="Ana")
    m = _mensaje(client, nombre="Festivo", texto="Lunes {nombre}")

    r = client.get(_URL, params={"q": "ana", "mensaje": str(m.id)}, headers={"X-Requested-With": "fetch"})

    assert "<html" not in r.text  # solo el fragmento
    assert _nombres(r.text) == ["ANA"]
    assert "Lunes%20ANA" in r.text  # los botones siguen usando el mensaje elegido


def test_la_barra_conserva_el_mensaje_elegido_y_la_paginacion_lleva_filtros_y_mensaje(client, monkeypatch):
    from app.web.routes import avisos_especiales

    monkeypatch.setattr(avisos_especiales, "_POR_PAGINA", 2)
    _login_admin(client)
    for i in range(5):
        _anunciar(client, tel=f"30011111{i}0", nombre=f"Cliente {i}")
    m = _mensaje(client)

    html = client.get(_URL, params={"mensaje": str(m.id), "q": "cliente"}).text

    assert f'name="mensaje" value="{m.id}"' in html  # campo oculto de la barra de búsqueda
    assert len(_filas(html)) == 2
    assert f"mensaje={m.id}" in re.search(r'href="([^"]*pagina=2[^"]*)"', html).group(1)
    assert "q=cliente" in re.search(r'href="([^"]*pagina=2[^"]*)"', html).group(1)
    assert len(_filas(client.get(_URL, params={"mensaje": str(m.id), "q": "cliente", "pagina": 3}).text)) == 1


# --------------------------------------------------------------------------- #
# Revisión de código
# --------------------------------------------------------------------------- #
def test_la_url_no_puede_poner_texto_libre_en_pantalla(client):
    _login_admin(client)

    html = client.get(_URL, params={"error": "Tu cuenta fue suspendida", "resultado": "Llama al 300"}).text

    assert "Tu cuenta fue suspendida" not in html and "Llama al 300" not in html


def test_una_pagina_que_no_es_un_numero_muestra_la_primera(client):
    _login_admin(client)
    _anunciar(client)

    r = client.get(_URL, params={"pagina": "abc"})

    assert r.status_code == 200 and len(_filas(r.text)) == 1


def test_sin_js_un_error_vuelve_a_la_vista_con_un_codigo_y_no_con_el_texto(client, monkeypatch):
    from app.web.routes import avisos_especiales

    _login_admin(client)
    p = _anunciar(client)
    m = _mensaje(client)
    _con_sender(client, monkeypatch, _SenderFalso())
    monkeypatch.setattr(avisos_especiales, "sms_configurado", lambda: False)

    r = _enviar(client, p.announced_by_persona_id, m.id, fetch=False)

    assert r.headers["location"] == f"{_URL}?mensaje={m.id}&resultado=no-configurado"
    assert "El SMS no está configurado todavía" in client.get(r.headers["location"]).text


def test_los_valores_por_defecto_de_sms_no_apagan_el_aviso_especial(client):
    # Decisión de Jesús (2026-10-11): por costo, el SMS de Recibido viene apagado POR DEFECTO; eso no debe bloquear el
    # aviso especial -- solo un apagado explícito del cliente lo bloquea.
    admin = _login_admin(client)
    p = _anunciar(client)
    receive(client.db, p, admin)  # solo Recibido, sin ninguna preferencia guardada
    client.db.commit()
    _mensaje(client)

    assert _sms_apagado(_fila_html(client.get(_URL).text, p.announced_by_persona_id)) is None
