"""
Mensajes especiales en `/administracion/notificaciones` (`.scratch/avisos-especiales`, tickets 01-02): el ADMIN crea,
edita y elimina los avisos que después manda desde la vista "Avisos especiales" (fines de semana, festivos...).
"""

import re

from app.domain.mensaje_especial import MensajeEspecial, MensajeEspecialHistorial
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

_PW = "Contrasena1"
_URL = "/administracion/notificaciones"
_CREAR = "/administracion/notificaciones/mensajes-especiales"


def _login_admin(client, email="admin@club.com"):
    admin = create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})
    return admin


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _seccion(html):
    """El HTML de la sección "Mensajes especiales" (la lista, sin los modales)."""
    m = re.search(r"<div[^>]*data-mensajes-especiales[^>]*>(.*?)</div>", html, re.S)
    assert m, "falta la sección de mensajes especiales"
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1)))


def _crear(client, nombre="Fin de semana", texto="Hola {nombre}: este sabado no hay servicio. Tus paquetes: {codigos}."):
    return client.post(_CREAR, data={"nombre": nombre, "texto": texto})


# --------------------------------------------------------------------------- #
# Ticket 01 -- crear
# --------------------------------------------------------------------------- #
def test_admin_crea_un_mensaje_especial_y_lo_ve_en_la_lista(client):
    _login_admin(client)

    r = _crear(client)

    assert r.status_code == 200
    assert "Mensaje especial creado." in r.text  # el toast
    assert "Fin de semana" in _seccion(r.text)
    assert "Fin de semana" in _seccion(client.get(_URL).text)


def test_operador_no_puede_crear_mensajes_especiales(client):
    _login_operador(client)

    r = _crear(client)

    assert r.status_code == 403
    assert client.db.query(MensajeEspecial).count() == 0


def test_nombre_repetido_sin_distinguir_mayusculas_se_rechaza(client):
    _login_admin(client)
    _crear(client, nombre="Fin de semana")

    r = _crear(client, nombre="  FIN DE   SEMANA ")

    assert r.status_code == 400
    assert 'Ya existe un mensaje llamado &#34;FIN DE SEMANA&#34;.' in r.text
    assert client.db.query(MensajeEspecial).count() == 1


def test_variable_desconocida_se_rechaza_y_reabre_el_modal_con_lo_escrito(client):
    _login_admin(client)

    r = _crear(client, texto="Hola {recipient_name}, no hay servicio")

    assert r.status_code == 400
    assert "La variable {recipient_name} no existe. Usa: {nombre}, {codigos}." in r.text
    modal = re.search(r'<div[^>]*id="modal-mensaje-especial-nuevo"[^>]*>', r.text).group(0)
    assert " hidden" not in modal  # abierto
    assert "Hola {recipient_name}, no hay servicio</textarea>" in r.text
    assert client.db.query(MensajeEspecial).count() == 0


def test_nombre_y_texto_son_obligatorios(client):
    _login_admin(client)

    assert "El nombre no puede quedar vacío." in _crear(client, nombre="   ").text
    assert "El texto no puede quedar vacío." in _crear(client, texto="  ").text
    assert client.db.query(MensajeEspecial).count() == 0


def test_la_lista_muestra_el_largo_como_sms_resuelto_y_sin_tildes(client):
    # 160 caracteres = 1 SMS: el conteo es el del texto que de verdad sale (variables de ejemplo, sin tildes).
    _login_admin(client)
    _crear(client, nombre="Corto", texto="Hola {nombre}")  # "Hola MARIA PEREZ" = 16
    _crear(client, nombre="Largo", texto="á" * 170)

    seccion = _seccion(client.get(_URL).text)
    assert "16 car." in seccion
    assert "170 car." in seccion


def test_la_vista_previa_del_editor_resuelve_las_variables_con_datos_de_ejemplo():
    from app.domain.aviso_especial_service import vista_previa

    previa = vista_previa("Hola {nombre}, tus paquetes {codigos} esperan el lunes.")

    assert previa["texto"] == "Hola MARIA PEREZ, tus paquetes A1B2, C3D4 esperan el lunes."
    assert previa["caracteres"] == len(previa["texto"])
    assert previa["un_sms"] is True
    assert vista_previa("x" * 161)["un_sms"] is False


def test_crear_deja_la_creacion_en_el_historial_con_el_admin(client):
    admin = _login_admin(client)

    _crear(client, nombre="Festivo", texto="Lunes festivo: sin servicio.")

    historial = client.db.query(MensajeEspecialHistorial).one()
    assert historial.accion.value == "CREADO"
    assert historial.usuario_id == admin.id
    assert (historial.nombre_nuevo, historial.texto_nuevo) == ("Festivo", "Lunes festivo: sin servicio.")


# --------------------------------------------------------------------------- #
# Ticket 02 -- editar y eliminar
# --------------------------------------------------------------------------- #
def _id_de(client, nombre):
    return client.db.query(MensajeEspecial).filter_by(nombre=nombre).one().id


def test_editar_cambia_nombre_y_texto_y_deja_antes_y_despues_en_el_historial(client):
    admin = _login_admin(client)
    _crear(client, nombre="Fin de semana", texto="Sabado sin servicio.")
    mid = _id_de(client, "Fin de semana")

    r = client.post(f"{_CREAR}/{mid}/editar", data={"nombre": "Puente festivo", "texto": "Lunes 13 sin servicio, {nombre}."})

    assert r.status_code == 200
    assert "Mensaje especial actualizado." in r.text
    assert "Puente festivo" in _seccion(r.text) and "Fin de semana" not in _seccion(r.text)
    client.db.expire_all()
    edicion = client.db.query(MensajeEspecialHistorial).filter_by(accion="EDITADO").one()
    assert edicion.usuario_id == admin.id
    assert (edicion.nombre_anterior, edicion.nombre_nuevo) == ("Fin de semana", "Puente festivo")
    assert (edicion.texto_anterior, edicion.texto_nuevo) == ("Sabado sin servicio.", "Lunes 13 sin servicio, {nombre}.")


def test_editar_con_nombre_de_otro_mensaje_se_rechaza_y_reabre_su_modal(client):
    _login_admin(client)
    _crear(client, nombre="Festivo")
    _crear(client, nombre="Fin de semana")
    mid = _id_de(client, "Fin de semana")

    r = client.post(f"{_CREAR}/{mid}/editar", data={"nombre": "festivo", "texto": "Otro texto"})

    assert r.status_code == 400
    modal = re.search(rf'<div[^>]*id="modal-mensaje-especial-{mid}"[^>]*>', r.text).group(0)
    assert " hidden" not in modal
    client.db.expire_all()
    assert client.db.get(MensajeEspecial, mid).nombre == "Fin de semana"


def test_editar_valida_las_variables_igual_que_crear(client):
    _login_admin(client)
    _crear(client, nombre="Festivo")
    mid = _id_de(client, "Festivo")

    r = client.post(f"{_CREAR}/{mid}/editar", data={"nombre": "Festivo", "texto": "Hola {apartamento}"})

    assert r.status_code == 400
    assert "La variable {apartamento} no existe." in r.text


def test_eliminar_lo_quita_de_la_lista_sin_borrarlo_y_libera_el_nombre(client):
    admin = _login_admin(client)
    _crear(client, nombre="Festivo")
    mid = _id_de(client, "Festivo")

    r = client.post(f"{_CREAR}/{mid}/eliminar")

    assert r.status_code == 200
    assert "Mensaje especial eliminado." in r.text
    assert "Festivo" not in _seccion(r.text)
    client.db.expire_all()
    assert client.db.get(MensajeEspecial, mid).eliminado_en is not None  # eliminación lógica
    eliminacion = client.db.query(MensajeEspecialHistorial).filter_by(accion="ELIMINADO").one()
    assert (eliminacion.usuario_id, eliminacion.nombre_anterior) == (admin.id, "Festivo")
    # El nombre vuelve a estar libre para un mensaje nuevo.
    assert _crear(client, nombre="Festivo").status_code == 200


def test_eliminar_pide_confirmacion(client):
    _login_admin(client)
    _crear(client, nombre="Festivo")
    mid = _id_de(client, "Festivo")

    html = client.get(_URL).text

    assert f'data-open="modal-mensaje-especial-eliminar-{mid}"' in html
    confirmacion = html[html.index(f'id="modal-mensaje-especial-eliminar-{mid}"'):]
    assert f'action="{_CREAR}/{mid}/eliminar"' in confirmacion[:3000]


def test_editar_o_eliminar_un_mensaje_ya_eliminado_responde_error(client):
    _login_admin(client)
    _crear(client, nombre="Festivo")
    mid = _id_de(client, "Festivo")
    client.post(f"{_CREAR}/{mid}/eliminar")

    assert client.post(f"{_CREAR}/{mid}/eliminar").status_code == 400
    assert client.post(f"{_CREAR}/{mid}/editar", data={"nombre": "X", "texto": "Y"}).status_code == 400
    assert client.post(f"{_CREAR}/no-es-un-id/eliminar").status_code == 404
