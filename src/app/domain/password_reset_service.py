# -*- coding: utf-8 -*-
"""
Servicio de dominio de recuperación de contraseña de staff (Seam A).

`solicitar_reset` resuelve si `email` corresponde a un `Usuario` ACTIVO y, si
es así, genera+persiste un token de un solo uso (30 minutos) -- rápido, solo
BD, SIN enviar nada. El envío real (`EmailSender`) se difiere a un
`BackgroundTask` (`enviar_en_segundo_plano` en `app/web/password_reset.py`),
mismo patrón que OTP/notificaciones de evento.

Mensaje genérico (anti-enumeración, mismo principio que `elegible_para_otp`/
`verify_credentials`): `solicitar_reset` devuelve `None` si el email no existe
o la cuenta está desactivada, SIN crear ningún registro -- quien llama responde
IGUAL en ambos casos.

`confirmar_reset` valida el token vigente y, si es correcto, cambia la
contraseña vía `staff_service.set_password` (comparte el hasheo/política con
el reset admin-driven) y marca el token como consumido. Dos modos de fallo
DISTINTOS a propósito: token inválido/expirado/usado -> mensaje genérico (no
revela nada del token); contraseña débil -> mensaje específico de
`_validar_password` (no es un riesgo de enumeración, es solo validación de
formulario) -- se valida el token PRIMERO, así que solo alguien con un token
realmente vigente llega a ver el motivo específico.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .password_reset import PasswordReset
from .staff_service import set_password
from .usuario import Usuario

_EXPIRACION_MINUTOS = 30
_MENSAJE_GENERICO = "Este enlace ya no es válido. Solicita uno nuevo."


def _hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _normalizar_email(email: str) -> str:
    return (email or "").strip().lower()


def solicitar_reset(session: Session, email: str) -> tuple[Usuario, str] | None:
    """Genera+persiste un token de reset si `email` es una cuenta ACTIVA.
    Devuelve `(usuario, token_crudo)` para diferir el envío a un
    `BackgroundTask`, o `None` si no corresponde a ninguna cuenta activa (no
    se crea ningún registro en ese caso)."""
    email_norm = _normalizar_email(email)
    if not email_norm:
        return None

    usuario = (
        session.query(Usuario)
        .filter(Usuario.email == email_norm, Usuario.activo.is_(True))
        .one_or_none()
    )
    if usuario is None:
        return None

    token = secrets.token_urlsafe(32)
    reset = PasswordReset(
        usuario_id=usuario.id,
        token_hash=_hash_token(token),
        expira_en=datetime.now(timezone.utc) + timedelta(minutes=_EXPIRACION_MINUTOS),
    )
    session.add(reset)
    session.flush()
    return usuario, token


def _reset_vigente(session: Session, token: str) -> PasswordReset | None:
    ahora = datetime.now(timezone.utc)
    return (
        session.query(PasswordReset)
        .filter(
            PasswordReset.token_hash == _hash_token(token),
            PasswordReset.usado_en.is_(None),
            PasswordReset.expira_en > ahora,
        )
        .first()
    )


def confirmar_reset(session: Session, token: str, nueva_password: str) -> Usuario:
    """Verifica `token` y, si es vigente, cambia la contraseña del Usuario
    dueño y consume el token.

    Raises:
        ValueError: token inválido/expirado/ya usado (mensaje genérico), o
            `nueva_password` no cumple la política de fuerza (mensaje
            específico de `staff_service._validar_password`).
    """
    reset = _reset_vigente(session, token)
    if reset is None:
        raise ValueError(_MENSAJE_GENERICO)

    usuario = session.get(Usuario, reset.usuario_id)
    set_password(session, usuario, nueva_password)

    reset.usado_en = datetime.now(timezone.utc)
    session.flush()
    return usuario
