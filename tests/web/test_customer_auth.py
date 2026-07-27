# -*- coding: utf-8 -*-
"""
Capa web — autenticación de cliente (OTP) + sesión independiente de staff.

Comportamiento observable por HTTP: pedir OTP, verificar (válido abre sesión,
inválido no — mensaje genérico), la ruta protegida exige/expone la Persona
correcta, logout cierra solo la sesión de cliente, y la sesión de staff/cliente
coexisten sin pisarse.
"""

from app.domain.otp_cliente import OtpCliente
from app.domain.otp_sender import DevOtpSender
from app.web.otp import get_otp_sender

_CANON = "+573001234567"


class _SenderQueFalla:
    """Simula un proveedor SMS inalcanzable (p.ej. LIWA sin whitelist de IP)."""

    def enviar(self, telefono, codigo):
        raise ConnectionError("timeout de red simulado")


def _pedir_codigo(client, telefono="3001234567"):
    sender = DevOtpSender()
    client.app.dependency_overrides[get_otp_sender] = lambda: sender
    r = client.post("/otp/solicitar", data={"telefono": telefono})
    assert r.status_code == 200
    return sender.enviados[_CANON]


def test_get_customer_login_renderiza_el_formulario(client):
    r = client.get("/otp")
    assert r.status_code == 200
    assert 'name="telefono"' in r.text


def test_request_otp_muestra_pantalla_de_verificar(client):
    r = client.post(
        "/otp/solicitar", data={"telefono": "3001234567"}
    )
    assert r.status_code == 200
    assert 'name="codigo"' in r.text


def test_verify_otp_valido_abre_sesion_y_redirige(client):
    codigo = _pedir_codigo(client)
    r = client.post(
        "/otp/verificar",
        data={"telefono": "3001234567", "codigo": codigo},
    )
    assert r.status_code == 200  # siguió el redirect a /otp/perfil
    assert _CANON in r.text


def test_verify_otp_invalido_mensaje_generico_sin_sesion(client):
    codigo = _pedir_codigo(client)
    # Con solo 100 códigos posibles, un valor fijo podría coincidir por azar con
    # el generado — se calcula uno garantizado distinto.
    codigo_incorrecto = "00" if codigo != "00" else "01"
    r = client.post(
        "/otp/verificar",
        data={"telefono": "3001234567", "codigo": codigo_incorrecto},
    )
    assert r.status_code == 400
    assert "inválido" in r.text.lower() or "expirado" in r.text.lower()


def test_request_otp_con_proveedor_caido_muestra_mensaje_claro_no_500(client):
    antes = client.db.query(OtpCliente).filter(OtpCliente.telefono == _CANON).count()
    client.app.dependency_overrides[get_otp_sender] = lambda: _SenderQueFalla()

    r = client.post("/otp/solicitar", data={"telefono": "3001234567"})

    assert r.status_code == 502
    assert "no pudimos enviar" in r.text.lower()
    # No queda un OTP fantasma sin código realmente entregado (se deshace el
    # flush): el conteo no debe subir respecto a antes del intento fallido.
    despues = client.db.query(OtpCliente).filter(OtpCliente.telefono == _CANON).count()
    assert despues == antes


def test_request_otp_con_proveedor_caido_no_bloquea_un_intento_posterior_exitoso(client):
    client.app.dependency_overrides[get_otp_sender] = lambda: _SenderQueFalla()
    client.post("/otp/solicitar", data={"telefono": "3001234567"})

    codigo = _pedir_codigo(client)  # ahora con un sender que sí funciona
    r = client.post(
        "/otp/verificar", data={"telefono": "3001234567", "codigo": codigo}
    )
    assert r.status_code == 200
    assert _CANON in r.text  # sesión abierta pese al intento fallido anterior


def test_ruta_protegida_sin_sesion_redirige_a_customer_login(client):
    r = client.get("/otp/perfil", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/otp")


def test_logout_cierra_solo_la_sesion_de_cliente(client):
    codigo = _pedir_codigo(client)
    client.post(
        "/otp/verificar",
        data={"telefono": "3001234567", "codigo": codigo},
    )
    assert client.get("/otp/perfil").status_code == 200

    client.post("/otp/salir")
    r = client.get("/otp/perfil", follow_redirects=False)
    assert r.status_code == 303


def test_sesion_de_staff_y_cliente_coexisten_sin_pisarse(client):
    from app.domain.staff_service import create_initial_admin

    create_initial_admin(client.db, "admin@club.com", "Admin", "Contrasena1")
    client.db.commit()

    # Abrir sesión de staff.
    client.post(
        "/ingresar", data={"email": "admin@club.com", "password": "Contrasena1"}
    )
    assert client.get("/mi-sesion").status_code == 200
    # La sesión de cliente NO existe todavía: la ruta de cliente sigue rechazando.
    assert client.get("/otp/perfil", follow_redirects=False).status_code == 303

    # Abrir también sesión de cliente, en el MISMO navegador (mismo client/cookies).
    codigo = _pedir_codigo(client)
    client.post(
        "/otp/verificar",
        data={"telefono": "3001234567", "codigo": codigo},
    )

    # Ambas sesiones responden 200 a la vez: no se pisaron.
    assert client.get("/mi-sesion").status_code == 200
    assert client.get("/otp/perfil").status_code == 200

    # Cerrar la sesión de STAFF no debe afectar la de cliente.
    client.post("/salir")
    assert client.get("/otp/perfil").status_code == 200
    r = client.get("/mi-sesion", follow_redirects=False)
    assert r.status_code == 303  # staff sí cerró
