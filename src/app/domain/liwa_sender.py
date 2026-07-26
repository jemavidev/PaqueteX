# -*- coding: utf-8 -*-
"""
Conector real de SMS vía LIWA.co — implementa tanto `NotificationSender` como
`OtpSender` (mismo proveedor detrás de los dos puertos, ADR de Grupo 8 en
`ajustes-post-referencia-funcional/REQUERIMIENTOS.md`).

Formato de API confirmado contra el sistema legacy en producción
(`CODE/src/app/services/sms_service.py`, ya probado ahí):

  - Auth: ``POST {LIWA_AUTH_URL}`` con ``{"account", "password"}`` → ``{"token"}``.
    El token se cachea en memoria (~23h) para no autenticar en cada SMS.
  - Envío: ``POST https://api.liwa.co/v2/sms/single``, headers
    ``Authorization: Bearer {token}`` + ``API-KEY: {api_key}``, payload
    ``{"number": "57XXXXXXXXXX" (sin "+"), "message", "type": 1}``.

Variables de entorno requeridas: ``LIWA_API_KEY``, ``LIWA_ACCOUNT``,
``LIWA_PASSWORD``; opcional ``LIWA_AUTH_URL`` (default el de producción).
Si faltan, `_config()` lanza `RuntimeError` — la selección de esta
implementación vs. la de desarrollo/consola vive en la capa web
(`app/web/notifications.py`, `app/web/otp.py`), no aquí.
"""

import os
import time

import httpx

_SMS_URL = "https://api.liwa.co/v2/sms/single"
_AUTH_URL_DEFAULT = "https://api.liwa.co/v2/auth/login"
_TOKEN_TTL_SEGUNDOS = 23 * 60 * 60  # ~23h, mismo margen que el legacy
_TIMEOUT_SEGUNDOS = 15.0

# Cache de token en memoria del proceso, por cuenta — evita autenticar en cada SMS.
_token_cache: dict[str, tuple[str, float]] = {}


def _config() -> tuple[str, str, str, str]:
    api_key = os.environ.get("LIWA_API_KEY")
    account = os.environ.get("LIWA_ACCOUNT")
    password = os.environ.get("LIWA_PASSWORD")
    auth_url = os.environ.get("LIWA_AUTH_URL", _AUTH_URL_DEFAULT)
    if not (api_key and account and password):
        raise RuntimeError(
            "Configuración de LIWA incompleta — se requieren LIWA_API_KEY, "
            "LIWA_ACCOUNT y LIWA_PASSWORD."
        )
    return api_key, account, password, auth_url


def _autenticar(account: str, password: str, auth_url: str) -> str:
    respuesta = httpx.post(
        auth_url, json={"account": account, "password": password}, timeout=_TIMEOUT_SEGUNDOS
    )
    respuesta.raise_for_status()
    token = respuesta.json().get("token")
    if not token:
        raise RuntimeError("Autenticación LIWA falló: no se recibió token.")
    return token


def _obtener_token(account: str, password: str, auth_url: str, forzar: bool = False) -> str:
    ahora = time.monotonic()
    cacheado = _token_cache.get(account)
    if not forzar and cacheado and cacheado[1] > ahora:
        return cacheado[0]

    token = _autenticar(account, password, auth_url)
    _token_cache[account] = (token, ahora + _TOKEN_TTL_SEGUNDOS)
    return token


def _numero_liwa(telefono_canonico: str) -> str:
    """LIWA espera el número sin `+` (con el indicativo de país al frente,
    p.ej. `573001234567`) — mismo formato que el legacy."""
    return telefono_canonico.lstrip("+")


def _enviar_sms(destino: str, mensaje: str) -> None:
    api_key, account, password, auth_url = _config()
    token = _obtener_token(account, password, auth_url)
    payload = {"number": _numero_liwa(destino), "message": mensaje, "type": 1}
    headers = {
        "Authorization": f"Bearer {token}",
        "API-KEY": api_key,
        "Content-Type": "application/json",
    }

    respuesta = httpx.post(_SMS_URL, json=payload, headers=headers, timeout=_TIMEOUT_SEGUNDOS)
    if respuesta.status_code == 401:
        # Token posiblemente vencido antes de lo esperado: reautenticar una vez.
        token = _obtener_token(account, password, auth_url, forzar=True)
        headers["Authorization"] = f"Bearer {token}"
        respuesta = httpx.post(_SMS_URL, json=payload, headers=headers, timeout=_TIMEOUT_SEGUNDOS)

    respuesta.raise_for_status()
    data = respuesta.json()
    if not data.get("success"):
        raise RuntimeError(f"LIWA rechazó el envío: {data.get('message', 'sin detalle')}")


class LiwaNotificationSender:
    """Implementación real de `NotificationSender` vía LIWA.co."""

    def enviar(self, destino: str, mensaje: str) -> None:
        _enviar_sms(destino, mensaje)


class LiwaOtpSender:
    """Implementación real de `OtpSender` vía LIWA.co."""

    def enviar(self, telefono: str, codigo: str) -> None:
        _enviar_sms(telefono, f"Tu código de verificación PAQUETEX es: {codigo}")
