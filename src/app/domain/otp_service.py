# -*- coding: utf-8 -*-
"""
Servicio de dominio de OTP de cliente (Seam A).

`request_otp` genera un código de 2 dígitos, lo hashea, lo persiste con
expiración corta y lo entrega al `OtpSender` (el canal real es otra rebanada).
`verify_otp` valida el OTP vigente para ese teléfono y, si es correcto, hace
get-or-create de la Persona (mismo patrón que `announce`) — la verificación de
teléfono y el registro implícito comparten una sola vía de identidad.

Código corto (2 dígitos = 100 combinaciones) a propósito, para bajar la
fricción de tecleo en el cliente: la seguridad la da `max_intentos` (5, ver
`request_otp`) atado al teléfono en `_otp_vigente`, no el tamaño del espacio de
búsqueda — a los 5 intentos fallidos ese código queda inválido sin importar
desde dónde se reintente.
"""

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlalchemy.orm import Session

from .otp_cliente import OtpCliente
from .otp_sender import OtpSender
from .persona import Persona
from .persona_service import get_or_create_persona
from .telefono import normalizar_telefono

_EXPIRACION_MINUTOS = 5
_LONGITUD_CODIGO = 2
_BCRYPT_MAX_BYTES = 72

_MENSAJE_GENERICO = "Código inválido o expirado."


def _generar_codigo() -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(_LONGITUD_CODIGO))


def _hash_codigo(codigo: str) -> str:
    return bcrypt.hashpw(
        codigo.encode("utf-8")[:_BCRYPT_MAX_BYTES], bcrypt.gensalt()
    ).decode("ascii")


def _verificar_codigo(codigo: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(
            (codigo or "").encode("utf-8")[:_BCRYPT_MAX_BYTES], hashed.encode("ascii")
        )
    except (ValueError, TypeError):
        return False


def request_otp(session: Session, telefono: str, sender: OtpSender) -> None:
    """Genera un OTP de 2 dígitos para `telefono` y lo entrega al `sender`.

    El código se persiste SOLO hasheado; el `sender` recibe el código en claro
    para entregarlo por el canal real (SMS), que el dominio no retiene.
    """
    telefono_canonico = normalizar_telefono(telefono)
    codigo = _generar_codigo()

    otp = OtpCliente(
        telefono=telefono_canonico,
        codigo_hash=_hash_codigo(codigo),
        intentos=0,
        max_intentos=5,
        expira_en=datetime.now(timezone.utc) + timedelta(minutes=_EXPIRACION_MINUTOS),
    )
    session.add(otp)
    session.flush()

    sender.enviar(telefono_canonico, codigo)


def _otp_vigente(session: Session, telefono_canonico: str):
    ahora = datetime.now(timezone.utc)
    return (
        session.query(OtpCliente)
        .filter(
            OtpCliente.telefono == telefono_canonico,
            OtpCliente.verificado_en.is_(None),
            OtpCliente.expira_en > ahora,
            OtpCliente.intentos < OtpCliente.max_intentos,
        )
        .order_by(OtpCliente.created_at.desc())
        .first()
    )


def verify_otp(session: Session, telefono: str, codigo: str) -> Persona:
    """Verifica el OTP vigente de `telefono` contra `codigo`.

    Si es correcto: marca el OTP como consumido (`verificado_en`, no reutilizable)
    y hace get-or-create de la Persona por teléfono. Si no hay un OTP vigente, o el
    código no coincide, incrementa los intentos del OTP más reciente (si existe) y
    lanza `ValueError` **genérico** — no distingue "no existe", "expiró" o
    "incorrecto" (mismo principio que `verify_credentials` de staff).

    Raises:
        ValueError: código inválido, expirado, agotado o inexistente.
    """
    telefono_canonico = normalizar_telefono(telefono)
    otp = _otp_vigente(session, telefono_canonico)

    if otp is None or not _verificar_codigo(codigo, otp.codigo_hash):
        if otp is not None:
            otp.intentos += 1
            session.flush()
        raise ValueError(_MENSAJE_GENERICO)

    otp.verificado_en = datetime.now(timezone.utc)
    session.flush()

    return get_or_create_persona(session, telefono_canonico, nombre="")
