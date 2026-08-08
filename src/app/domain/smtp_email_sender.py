# -*- coding: utf-8 -*-
"""
Conector real de correo vía SMTP genérico (`smtplib` de la librería estándar --
sin SDK de proveedor, consistente con el resto del proyecto, ver
`twilio_sender.py`/`liwa_sender.py`). Cuenta en uso: `paquetex@papyrus.com.co`
(MXroute), dedicada a este entorno, separada de cualquier SMTP de producción.

Variables de entorno requeridas: ``SMTP_HOST``, ``SMTP_PORT``, ``SMTP_USER``,
``SMTP_PASSWORD``, ``SMTP_FROM_EMAIL``. Si falta alguna, `_config()` lanza
`RuntimeError` -- la selección de esta implementación vs. consola vive en la
capa web (`app/web/password_reset.py`), no aquí.

``SMTP_USE_SSL`` (por defecto "false") usa `SMTP_SSL` de entrada (puerto 465
típico); si no, `SMTP_USE_TLS` (por defecto "true") hace STARTTLS tras
conectar en claro (puerto 587 típico) -- ambos modos confirmados funcionando
contra MXroute antes de esta rebanada.
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

_TIMEOUT_SEGUNDOS = 15.0
# Nombre visible del remitente en cualquier cliente de correo (confirmado por
# el cliente, .scratch/pendientes-cliente) -- separado de `SMTP_FROM_EMAIL`
# (la dirección real), vive en código porque es una decisión de producto, no
# de configuración de infraestructura.
_NOMBRE_REMITENTE = "PaqueteX - Papyrus"


def _env_bool(nombre: str, default: bool) -> bool:
    valor = os.environ.get(nombre)
    if valor is None or not valor.strip():
        return default
    return valor.strip().lower() in ("true", "1", "yes")


def _config() -> tuple[str, int, str, str, str, bool, bool]:
    host = os.environ.get("SMTP_HOST")
    port = os.environ.get("SMTP_PORT")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    from_email = os.environ.get("SMTP_FROM_EMAIL")
    if not (host and port and user and password and from_email):
        raise RuntimeError(
            "Configuración SMTP incompleta -- se requieren SMTP_HOST, SMTP_PORT, "
            "SMTP_USER, SMTP_PASSWORD y SMTP_FROM_EMAIL."
        )
    use_tls = _env_bool("SMTP_USE_TLS", default=True)
    use_ssl = _env_bool("SMTP_USE_SSL", default=False)
    return host, int(port), user, password, from_email, use_tls, use_ssl


def configurado() -> bool:
    """¿Están las CINCO variables de SMTP presentes? Usado por la capa web
    para decidir si este proveedor entra a producir envíos reales, en vez de
    la consola de desarrollo/test (mismo principio que `twilio_sender.
    configurado()`: mirar solo una variable dejaría entrar una config a
    medias)."""
    try:
        _config()
        return True
    except RuntimeError:
        return False


def _enviar_correo(
    destino: str, asunto: str, cuerpo: str, cuerpo_html: str | None = None
) -> None:
    host, port, user, password, from_email, use_tls, use_ssl = _config()

    if cuerpo_html:
        # multipart/alternative, texto plano PRIMERO y HTML AL FINAL (RFC 2046
        # -- el cliente de correo elige la ÚLTIMA parte que sepa mostrar): así
        # sigue funcionando en clientes que bloquean HTML, y se ve enriquecido
        # en el resto.
        mensaje = MIMEMultipart("alternative")
        mensaje.attach(MIMEText(cuerpo, "plain"))
        mensaje.attach(MIMEText(cuerpo_html, "html"))
    else:
        mensaje = MIMEText(cuerpo)
    mensaje["Subject"] = asunto
    mensaje["From"] = formataddr((_NOMBRE_REMITENTE, from_email))
    mensaje["To"] = destino

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT_SEGUNDOS) as server:
                server.login(user, password)
                server.sendmail(from_email, [destino], mensaje.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=_TIMEOUT_SEGUNDOS) as server:
                if use_tls:
                    server.starttls()
                server.login(user, password)
                server.sendmail(from_email, [destino], mensaje.as_string())
    except (smtplib.SMTPException, OSError) as error:
        raise RuntimeError(f"SMTP no pudo enviar el correo: {error}") from error


class SmtpEmailSender:
    """Implementación real de `EmailSender` vía SMTP."""

    def enviar(
        self, destino: str, asunto: str, cuerpo: str, cuerpo_html: str | None = None
    ) -> None:
        _enviar_correo(destino, asunto, cuerpo, cuerpo_html)
