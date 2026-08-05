# -*- coding: utf-8 -*-
"""
Capa web — autenticación de cliente (OTP) + sesión independiente de staff.

Comportamiento observable por HTTP: solo teléfonos ELEGIBLES (existen +
tienen un Paquete Recibido, corrección en vivo 2026-08-02) reciben un
código; la respuesta es la MISMA para elegibles y no elegibles (no revela
cuál es cuál). El envío real es asíncrono (BackgroundTask) — `TestClient`
lo corre sincrónicamente antes de devolver la respuesta, así que
`sender.enviados` sigue siendo verificable sin cambios. Verificar válido
abre sesión y redirige a `/mis-datos`; inválido no — mensaje genérico. La
ruta protegida exige/expone la Persona correcta, logout cierra solo la
sesión de cliente, y la sesión de staff/cliente coexisten sin pisarse.
"""

from app.domain.otp_cliente import OtpCliente
from app.domain.otp_sender import DevOtpSender
from app.domain.paquete_lifecycle import receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin
from app.web.otp import get_otp_sender

_CANON = "+573001234567"


class _SenderQueFalla:
    """Simula un proveedor SMS inalcanzable (p.ej. LIWA sin whitelist de IP)."""

    def enviar(self, telefono, codigo):
        raise ConnectionError("timeout de red simulado")


def _hacer_elegible(client, telefono="3001234567", nombre="Ana"):
    """Un teléfono es elegible para OTP si tiene un Paquete en RECIBIDO
    (announce + receive, mismo patrón que tests de dominio)."""
    staff = create_initial_admin(client.db, "admin@club.com", "Admin", "Contrasena1")
    p = announce(
        client.db,
        anunciante_telefono=telefono,
        anunciante_nombre=nombre,
        destinatario=Destinatario.yo_mismo(),
    )
    receive(client.db, p, staff)
    client.db.commit()


def _pedir_codigo(client, telefono="3001234567"):
    sender = DevOtpSender()
    client.app.dependency_overrides[get_otp_sender] = lambda: sender
    r = client.post("/otp/solicitar", data={"telefono": telefono})
    assert r.status_code == 200  # siguió el redirect a /otp/verificar
    return sender.enviados[_CANON]


def test_get_customer_login_renderiza_el_formulario(client):
    r = client.get("/otp")
    assert r.status_code == 200
    assert 'name="telefono"' in r.text


def test_request_otp_elegible_muestra_pantalla_de_verificar(client):
    _hacer_elegible(client)
    r = client.post("/otp/solicitar", data={"telefono": "3001234567"})
    assert r.status_code == 200
    assert 'name="codigo"' in r.text


def test_request_otp_no_elegible_misma_respuesta_sin_crear_registro(client):
    # Teléfono que nunca anunció ni recibió nada -- no elegible.
    r = client.post("/otp/solicitar", data={"telefono": "3009998877"})
    assert r.status_code == 200
    assert 'name="codigo"' in r.text  # MISMA pantalla que un elegible
    assert (
        client.db.query(OtpCliente)
        .filter(OtpCliente.telefono == "+573009998877")
        .count()
        == 0
    )


def test_verify_otp_valido_abre_sesion_y_redirige_a_mis_datos(client):
    _hacer_elegible(client)
    codigo = _pedir_codigo(client)
    r = client.post(
        "/otp/verificar",
        data={"telefono": "3001234567", "codigo": codigo},
    )
    assert r.status_code == 200  # siguió el redirect a /mis-datos
    assert _CANON in r.text


def test_verify_otp_invalido_mensaje_generico_sin_sesion(client):
    _hacer_elegible(client)
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


def test_request_otp_con_proveedor_caido_no_falla_el_response(client):
    # Corrección en vivo 2026-08-02: el envío es best-effort en background --
    # un proveedor caído ya NO devuelve 502 ni deshace el OTP (antes sí, con
    # request_otp síncrono). El código se genera igual, el fallo queda solo
    # en logs del servidor.
    _hacer_elegible(client)
    client.app.dependency_overrides[get_otp_sender] = lambda: _SenderQueFalla()

    r = client.post("/otp/solicitar", data={"telefono": "3001234567"})

    assert r.status_code == 200
    assert (
        client.db.query(OtpCliente).filter(OtpCliente.telefono == _CANON).count() == 1
    )


def test_ruta_protegida_sin_sesion_redirige_a_customer_login(client):
    r = client.get("/otp/perfil", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/otp")


def test_logout_cierra_solo_la_sesion_de_cliente(client):
    _hacer_elegible(client)
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
    _hacer_elegible(client)

    # Abrir sesión de staff (mismo admin que _hacer_elegible ya creó).
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
