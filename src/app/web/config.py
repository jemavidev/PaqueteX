# -*- coding: utf-8 -*-
"""
Settings mínimos de la capa web del rebuild (clean-room, ADR-0004).

NO importa `app.config` (que exige credenciales AWS S3 al arrancar). Solo expone
lo que la capa web necesita: la conexión a la BD desde `DATABASE_URL`, leída de
forma perezosa (el app puede importarse sin la variable puesta).
"""

import os


def database_url() -> str:
    """La URL de la BD desde el entorno. Se lee al usarse, no al importar."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL no está definido (requerido por la capa web del rebuild)."
        )
    return url


def whatsapp_soporte_numero() -> str | None:
    """Número de WhatsApp de soporte para el enlace del footer (Grupo 10,
    Ronda 2) — o `None` si no está configurado, en cuyo caso el enlace
    simplemente no se muestra (nunca se publica un enlace roto)."""
    return os.environ.get("WHATSAPP_SOPORTE_NUMERO") or None


def public_base_url() -> str:
    """Origen público completo (``https://dominio``, sin `/` final) para
    construir enlaces absolutos en correos (recuperación de contraseña).

    `request.base_url` NO es confiable para esto: uvicorn corre sin
    `--proxy-headers` detrás de Caddy, así que el esquema reportado sería
    `http` incluso en producción/staging (Caddy sí reenvía `X-Forwarded-*`,
    pero uvicorn no los honra sin ese flag). `PUBLIC_BASE_URL` es obligatorio
    fuera de desarrollo/test.
    """
    url = os.environ.get("PUBLIC_BASE_URL")
    if url:
        return url.rstrip("/")
    if os.environ.get("WEB_ENV") in ("staging", "production"):
        raise RuntimeError("PUBLIC_BASE_URL es obligatorio en staging/producción.")
    return "http://testserver"


def public_base_url_relaxed() -> str | None:
    """Como `public_base_url()`, pero nunca lanza -- `None` si no se puede
    resolver (issue 222, .scratch/pendientes-cliente). Para contextos
    best-effort (el link `{link}` de una notificación de evento de Paquete):
    un `PUBLIC_BASE_URL` mal configurado en staging/producción no debe
    bloquear la transición REAL del Paquete (recibir/entregar/cancelar) solo
    porque el mensaje de aviso no pudo resolver un link absoluto -- mismo
    espíritu "best-effort" que ya rige el envío en sí
    (`notificacion_service.notificar_evento`)."""
    try:
        return public_base_url()
    except RuntimeError:
        return None


def secret_key() -> str:
    """Llave para firmar la cookie de sesión.

    Obligatoria en producción (`WEB_ENV=production`); en desarrollo/test usa un
    default explícito e inseguro (nunca debe usarse en prod).
    """
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    if os.environ.get("WEB_ENV") == "production":
        raise RuntimeError("SECRET_KEY es obligatorio en producción.")
    return "dev-insecure-secret-solo-desarrollo"
