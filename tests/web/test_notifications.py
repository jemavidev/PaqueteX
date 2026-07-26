# -*- coding: utf-8 -*-
"""
Override fail-closed de staging + wiring en las rutas de `/paquetes` (ticket 02).

El test más importante de esta rebanada: en `WEB_ENV=staging` SIN
`SMS_OVERRIDE_NUMBER`, una transición real (recibir) no debe disparar NINGUNA
llamada al sender envuelto — el fail-closed es lo que protege a un residente
real de un SMS de una prueba de staging.
"""

from app.domain.notification_sender import ConsoleNotificationSender
from app.domain.staff_service import create_initial_admin
from app.domain.paquete_service import Destinatario, announce
from app.web.notifications import StagingOverrideSender, get_notification_sender

_PW = "Contrasena1"


def _login_staff(client, email="staff@club.com"):
    create_initial_admin(client.db, email, "Operador", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _anunciar(client, tel="3001234567", nombre="Ana"):
    p = announce(
        client.db,
        anunciante_telefono=tel,
        anunciante_nombre=nombre,
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()
    return p


# --------------------------------------------------------------------------- #
# StagingOverrideSender — unidad, sin DB ni HTTP.
# --------------------------------------------------------------------------- #
class _SenderEspia:
    def __init__(self):
        self.enviados = []

    def enviar(self, destino, mensaje):
        self.enviados.append((destino, mensaje))


def test_override_ausente_no_envia_nada_fail_closed():
    espia = _SenderEspia()
    StagingOverrideSender(espia, None).enviar("+573001234567", "hola")
    assert espia.enviados == []


def test_override_vacio_tambien_falla_cerrado():
    espia = _SenderEspia()
    StagingOverrideSender(espia, "   ").enviar("+573001234567", "hola")
    assert espia.enviados == []


def test_override_presente_redirige_al_numero_de_prueba():
    espia = _SenderEspia()
    StagingOverrideSender(espia, "+570000000000").enviar("+573001234567", "hola")
    assert espia.enviados == [("+570000000000", "hola")]  # nunca el real


# --------------------------------------------------------------------------- #
# get_notification_sender — selección por entorno.
# --------------------------------------------------------------------------- #
def test_sin_web_env_devuelve_console_sender_directo(monkeypatch):
    monkeypatch.delenv("WEB_ENV", raising=False)
    sender = get_notification_sender()
    assert isinstance(sender, ConsoleNotificationSender)


def test_staging_devuelve_staging_override_sender(monkeypatch):
    monkeypatch.setenv("WEB_ENV", "staging")
    sender = get_notification_sender()
    assert isinstance(sender, StagingOverrideSender)


# --------------------------------------------------------------------------- #
# Wiring en las rutas (desarrollo/test: sin WEB_ENV, sender inyectado).
# --------------------------------------------------------------------------- #
def test_receive_notifica_al_destinatario(client):
    _login_staff(client)
    p = _anunciar(client, nombre="Ana")

    espia = _SenderEspia()
    client.app.dependency_overrides[get_notification_sender] = lambda: espia

    client.post(f"/paquetes/{p.id}/recibir", data={})

    assert len(espia.enviados) == 1
    destino, mensaje = espia.enviados[0]
    assert destino == "+573001234567"
    assert "Ana" in mensaje and "portería" in mensaje


def test_deliver_notifica(client):
    _login_staff(client)
    p = _anunciar(client)
    client.post(f"/paquetes/{p.id}/recibir", data={})

    espia = _SenderEspia()
    client.app.dependency_overrides[get_notification_sender] = lambda: espia
    client.post(f"/paquetes/{p.id}/entregar")

    assert len(espia.enviados) == 1
    assert "entregado" in espia.enviados[0][1].lower()


def test_cancel_notifica_con_motivo(client):
    _login_staff(client)
    p = _anunciar(client)

    espia = _SenderEspia()
    client.app.dependency_overrides[get_notification_sender] = lambda: espia
    client.post(f"/paquetes/{p.id}/cancelar", data={"motivo": "NO_RECLAMADO"})

    assert len(espia.enviados) == 1
    assert "no reclamado" in espia.enviados[0][1].lower()


def test_transicion_rechazada_no_notifica(client):
    _login_staff(client)
    p = _anunciar(client)
    client.post(f"/paquetes/{p.id}/recibir", data={})  # ya RECIBIDO

    espia = _SenderEspia()
    client.app.dependency_overrides[get_notification_sender] = lambda: espia
    r = client.post(f"/paquetes/{p.id}/recibir", data={})  # inválido: ya recibido

    assert r.status_code == 400
    assert espia.enviados == []


# --------------------------------------------------------------------------- #
# El test más importante: fail-closed de punta a punta (HTTP real, sin overrides
# de dependencia — ejercita la wiring de producción tal cual).
# --------------------------------------------------------------------------- #
def test_staging_sin_override_number_cero_llamadas_tras_transicion_real(
    client, monkeypatch
):
    monkeypatch.setenv("WEB_ENV", "staging")
    monkeypatch.delenv("SMS_OVERRIDE_NUMBER", raising=False)

    llamadas = []
    monkeypatch.setattr(
        ConsoleNotificationSender,
        "enviar",
        lambda self, destino, mensaje: llamadas.append((destino, mensaje)),
    )

    _login_staff(client)
    p = _anunciar(client)

    r = client.post(f"/paquetes/{p.id}/recibir", data={}, follow_redirects=False)
    assert r.status_code == 303

    assert llamadas == []  # fail-closed: CERO llamadas al sender envuelto


def test_staging_con_override_number_redirige_al_numero_de_prueba(client, monkeypatch):
    monkeypatch.setenv("WEB_ENV", "staging")
    monkeypatch.setenv("SMS_OVERRIDE_NUMBER", "+570000000000")

    llamadas = []
    monkeypatch.setattr(
        ConsoleNotificationSender,
        "enviar",
        lambda self, destino, mensaje: llamadas.append((destino, mensaje)),
    )

    _login_staff(client)
    p = _anunciar(client, tel="3001234567")

    client.post(f"/paquetes/{p.id}/recibir", data={})

    assert len(llamadas) == 1
    destino, _mensaje = llamadas[0]
    assert destino == "+570000000000"
    assert destino != "+573001234567"  # nunca el teléfono real del residente
