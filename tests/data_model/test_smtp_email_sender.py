# -*- coding: utf-8 -*-
"""
Conector real de correo SMTP — probado con `smtplib.SMTP` reemplazado por un
doble de prueba vía `monkeypatch` (mismo patrón que `test_twilio_sender.py`;
no depende de red real ni de credenciales verdaderas).
"""

from email import message_from_string
from email.header import decode_header, make_header

import pytest

from app.domain import smtp_email_sender as mod
from app.domain.smtp_email_sender import SmtpEmailSender


class _ServidorFalso:
    """Doble de `smtplib.SMTP`/`SMTP_SSL` -- captura `sendmail()`, sin conectar."""

    instancias = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.logins = []
        self.envios = []
        self.starttls_llamado = False
        _ServidorFalso.instancias.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.starttls_llamado = True

    def login(self, user, password):
        self.logins.append((user, password))

    def sendmail(self, from_email, destinatarios, mensaje_str):
        self.envios.append((from_email, destinatarios, mensaje_str))


@pytest.fixture(autouse=True)
def _credenciales(monkeypatch):
    _ServidorFalso.instancias.clear()
    monkeypatch.setenv("SMTP_HOST", "smtp.fake.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "paquetex@papyrus.com.co")
    monkeypatch.setenv("SMTP_PASSWORD", "fake-password")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "paquetex@papyrus.com.co")
    monkeypatch.setenv("SMTP_USE_TLS", "true")
    monkeypatch.setenv("SMTP_USE_SSL", "false")
    monkeypatch.setattr(mod.smtplib, "SMTP", _ServidorFalso)
    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", _ServidorFalso)


def test_sin_html_manda_un_solo_part_de_texto_plano():
    SmtpEmailSender().enviar("destino@club.com", "Asunto", "Cuerpo plano")

    servidor = _ServidorFalso.instancias[0]
    assert servidor.starttls_llamado
    from_email, destinatarios, mensaje_str = servidor.envios[0]
    assert destinatarios == ["destino@club.com"]
    assert "Content-Type: text/plain" in mensaje_str
    assert "multipart" not in mensaje_str.lower()
    assert "Cuerpo plano" in mensaje_str


def test_con_html_manda_multipart_alternative_con_texto_y_html():
    SmtpEmailSender().enviar(
        "destino@club.com", "Asunto", "Cuerpo plano", "<p>Cuerpo html</p>"
    )

    servidor = _ServidorFalso.instancias[0]
    _, _, mensaje_str = servidor.envios[0]
    assert "multipart/alternative" in mensaje_str
    assert "Content-Type: text/plain" in mensaje_str
    assert "Content-Type: text/html" in mensaje_str
    assert "Cuerpo plano" in mensaje_str
    assert "<p>Cuerpo html</p>" in mensaje_str
    # Texto plano PRIMERO, HTML AL FINAL (RFC 2046 -- el cliente de correo
    # elige la ÚLTIMA parte que sepa mostrar).
    assert mensaje_str.index("text/plain") < mensaje_str.index("text/html")


def test_remitente_muestra_el_nombre_confirmado_por_el_cliente():
    SmtpEmailSender().enviar("destino@club.com", "Asunto", "Cuerpo")

    _, _, mensaje_str = _ServidorFalso.instancias[0].envios[0]
    # El header "From" con tilde va RFC 2047-encoded en el mensaje crudo
    # (=?utf-8?q?...?=) -- se decodifica con email.message_from_string en
    # vez de buscar el texto literal, que nunca aparece así en el mensaje.
    mensaje = message_from_string(mensaje_str)
    remitente = str(make_header(decode_header(mensaje["From"])))
    assert remitente == "PaqueteX - Papyrus <paquetex@papyrus.com.co>"


def test_sin_configuracion_completa_falla(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    with pytest.raises(RuntimeError):
        SmtpEmailSender().enviar("destino@club.com", "Asunto", "Cuerpo")
