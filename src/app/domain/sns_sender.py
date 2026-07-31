# -*- coding: utf-8 -*-
"""
Conector real de SMS vía AWS SNS — implementa tanto `NotificationSender`
como `OtpSender` (mismo patrón que `s3_foto_storage.py`: vía `boto3`,
dependencia que ya existe en el proyecto — ver
`.scratch/sms-failover-twilio-sns/spec.md`).

Envío: ``boto3.client("sns", region_name=...).publish(PhoneNumber=destino,
Message=mensaje, MessageAttributes={"AWS.SNS.SMS.SMSType": "Transactional"})``
— Transactional para priorizar entrega de OTP y notificaciones de estado
sobre tráfico promocional.

Reutiliza la cadena estándar de credenciales de `boto3`
(`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_REGION`) — las MISMAS que
ya usa `S3FotoStorage`, sin un segundo juego de credenciales AWS.

Habilitado por la bandera explícita ``AWS_SNS_SMS_ENABLED=true`` —
necesaria porque esas credenciales AWS ya pueden existir hoy solo para S3;
activar SNS por su sola presencia rompería el patrón de "flag explícito"
que ya usa `S3FotoStorage` (gateado por `AWS_S3_BUCKET_NAME`, no por
credenciales genéricas).
"""

import os

import boto3
import botocore.exceptions

from app.domain.otp_sender import mensaje_codigo
from app.domain.sms_failover import ErrorConectividadSms

_REGION_DEFAULT = "us-east-1"

# Códigos de error de AWS que indican un problema de infra/throttling (no un
# rechazo del contenido del mensaje) — reintentables con el siguiente
# proveedor de la cadena de failover.
_CODIGOS_RECONECTABLES = {
    "Throttling",
    "ThrottlingException",
    "InternalError",
    "InternalFailure",
    "ServiceUnavailable",
    "RequestTimeout",
}


def sns_habilitado() -> bool:
    return os.environ.get("AWS_SNS_SMS_ENABLED", "").strip().lower() == "true"


def _cliente():
    region = os.environ.get("AWS_REGION", _REGION_DEFAULT)
    return boto3.client("sns", region_name=region)


def _enviar_sms(destino: str, mensaje: str) -> None:
    if not sns_habilitado():
        raise RuntimeError(
            "AWS SNS no está habilitado — se requiere AWS_SNS_SMS_ENABLED=true."
        )
    try:
        respuesta = _cliente().publish(
            PhoneNumber=destino,
            Message=mensaje,
            MessageAttributes={
                "AWS.SNS.SMS.SMSType": {"DataType": "String", "StringValue": "Transactional"}
            },
        )
    except (botocore.exceptions.ConnectionError, botocore.exceptions.HTTPClientError) as error:
        # No hubo respuesta en absoluto (timeout, endpoint inalcanzable).
        raise ErrorConectividadSms(f"AWS SNS no fue alcanzable: {error}") from error
    except botocore.exceptions.ClientError as error:
        codigo = error.response.get("Error", {}).get("Code", "")
        if codigo in _CODIGOS_RECONECTABLES:
            raise ErrorConectividadSms(f"AWS SNS respondió {codigo}") from error
        raise RuntimeError(f"AWS SNS rechazó el envío ({codigo}): {error}") from error

    if not respuesta.get("MessageId"):
        raise RuntimeError("AWS SNS no devolvió MessageId — respuesta inesperada.")


class SnsNotificationSender:
    """Implementación real de `NotificationSender` vía AWS SNS."""

    def enviar(self, destino: str, mensaje: str) -> None:
        _enviar_sms(destino, mensaje)


class SnsOtpSender:
    """Implementación real de `OtpSender` vía AWS SNS."""

    def enviar(self, telefono: str, codigo: str) -> None:
        _enviar_sms(telefono, mensaje_codigo(codigo))
