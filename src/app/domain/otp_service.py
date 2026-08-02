# -*- coding: utf-8 -*-
"""
Servicio de dominio de OTP de cliente (Seam A).

`preparar_otp` (corrección en vivo 2026-08-02, reemplaza el antiguo
`request_otp` síncrono) resuelve la ELEGIBILIDAD y genera+persiste el código
de 2 dígitos -- solo lectura/escritura de BD, rápido, SIN enviar nada. El
envío real (`OtpSender`) se difiere a un `BackgroundTask` (`enviar_en_
segundo_plano` en `app/web/otp.py`), mismo patrón que las notificaciones de
evento de paquete (`notificacion_service.preparar_notificacion` +
`notifications.enviar_en_segundo_plano`) -- la demora de 5-10s que sufría
"pedir el código" era el mismo LIWA bloqueado que ya se diagnosticó para
`/anunciar`. A diferencia de las notificaciones de evento, aquí SÍ importaba
antes que el usuario supiera si el envío falló -- se acepta ese trade-off a
propósito (retroalimentación 2026-08-02): mejor la solicitud instantánea con
riesgo de que un envío puntual falle en silencio, que 5-10s de espera en
cada intento.

Elegibilidad (corrección en vivo 2026-08-02, restricción anti-abuso): solo se
genera un OTP para teléfonos que ya existan en el sistema con al menos un
Paquete en estado RECIBIDO (como anunciante o destinatario) -- sin esto,
cualquiera podía usar `/otp/solicitar` para mandar SMS masivos a números
ajenos con solo escribirlos ahí. `preparar_otp` devuelve `None` si el
teléfono no es elegible, SIN crear ningún registro -- quien llama responde
IGUAL en ambos casos (mensaje genérico), para no revelar por timing ni por
contenido si un teléfono específico está registrado.

`verify_otp` valida el OTP vigente para ese teléfono y, si es correcto, hace
get-or-create de la Persona (mismo patrón que `announce`) — la verificación de
teléfono y el registro implícito comparten una sola vía de identidad.

Código corto (2 dígitos = 100 combinaciones) a propósito, para bajar la
fricción de tecleo en el cliente: la seguridad la da `max_intentos` (5, ver
`preparar_otp`) atado al teléfono en `_otp_vigente`, no el tamaño del espacio
de búsqueda — a los 5 intentos fallidos ese código queda inválido sin
importar desde dónde se reintente.
"""

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .otp_cliente import OtpCliente
from .paquete import EstadoPaquete, Paquete
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


def elegible_para_otp(session: Session, telefono_canonico: str) -> bool:
    """True si `telefono_canonico` tiene al menos un Paquete en estado
    RECIBIDO asociado (como anunciante o destinatario)."""
    return (
        session.query(Paquete)
        .filter(
            Paquete.estado == EstadoPaquete.RECIBIDO,
            or_(
                Paquete.announced_by_phone == telefono_canonico,
                Paquete.recipient_phone == telefono_canonico,
            ),
        )
        .first()
        is not None
    )


def preparar_otp(session: Session, telefono: str) -> tuple[str, str] | None:
    """Resuelve elegibilidad y genera+persiste el OTP -- rápido, solo BD, NO
    envía nada. Devuelve `(telefono_canonico, codigo)` para diferir el envío
    a un `BackgroundTask`, o `None` si el teléfono no es elegible (no se crea
    ningún registro en ese caso).

    Raises:
        ValueError: teléfono mal formado (error de uso, no de elegibilidad).
    """
    telefono_canonico = normalizar_telefono(telefono)
    if not elegible_para_otp(session, telefono_canonico):
        return None

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
    return telefono_canonico, codigo


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
