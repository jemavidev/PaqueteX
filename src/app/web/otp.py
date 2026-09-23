# -*- coding: utf-8 -*-
"""
Wiring de envío de OTP de la capa web (ADR-0004).

A diferencia de las notificaciones de evento, el OTP **no** pasa por
`StagingOverrideSender` — el código debe llegar de verdad a quien lo pidió
(es autodirigido: cada quien solo puede solicitar el suyo), no hay riesgo de
"spamear" a un tercero como sí lo hay con las notificaciones de paquete.

`get_otp_sender` (dependencia FastAPI) arma el sender a partir de los
proveedores SMS reales cuya configuración esté COMPLETA (`configurado()` de
cada módulo, no solo la presencia de una variable), combinado con el
habilitado/orden de `ProveedorConfig` (issue 02, `.scratch/administracion-
proveedores/spec.md` -- `proveedor_config_service.armar_candidatos()`, mismo
mecanismo y misma razón que `app/web/notifications.py::_sender_base()`). El
orden YA NO es una constante fija -- se lee de la BD, editable desde
`/administracion/proveedores` sin redeploy. El orden por defecto (sembrado
por la migración 0037) sigue siendo **AWS SNS → LIWA → Twilio**
(`.scratch/pendientes-cliente`, pedido del cliente 2026-08-06: problemas
puntuales con Twilio, AWS pasa al frente mientras se investiga. Antes de ese
cambio el orden era LIWA → Twilio → SNS, `.scratch/sms-failover-twilio-sns/
spec.md`), vía el dispatch compartido `sms_failover.construir_sender()` (que
en sí no cambió): 0 configurados → `DevOtpSender` (desarrollo/tests, la
suite nunca manda SMS real); 1 configurado → ese sender directo; 2+
configurados → `FailoverSmsSender`, reintenta con el siguiente SOLO ante una
falla de conectividad.

`enviar_en_segundo_plano` (corrección en vivo 2026-08-02) es la contraparte
OTP de `notifications.enviar_en_segundo_plano` -- pensada para
`BackgroundTasks.add_task`, best-effort (traga cualquier excepción). Antes
el envío de OTP era síncrono a propósito ("el cliente necesita saber YA si
no le va a llegar el código"); se acepta ahora el mismo trade-off que las
notificaciones de evento (retroalimentación 2026-08-02: la demora de 5-10s
en "pedir el código" pesaba más que la garantía de error visible).
"""

import logging

from fastapi import Depends
from sqlalchemy.orm import Session, sessionmaker

from app.domain import liwa_sender, sns_sender, twilio_sender
from app.domain.liwa_sender import LiwaOtpSender
from app.domain.otp_sender import DevOtpSender, OtpSender
from app.domain.preferencia_notificacion import CanalNotificacion
from app.domain.proveedor_config_service import armar_candidatos
from app.domain.registro_sms import TipoRegistroSms
from app.domain.registro_sms_service import registrar_envio
from app.domain.sms_failover import construir_sender
from app.domain.sns_sender import SnsOtpSender
from app.domain.twilio_sender import TwilioOtpSender

from .db import get_db

logger = logging.getLogger(__name__)


def get_otp_sender(db: Session = Depends(get_db)) -> OtpSender:
    """`db` (issue 02, `.scratch/administracion-proveedores/spec.md`): el
    orden/habilitado de cada proveedor se lee de `ProveedorConfig` vía
    `proveedor_config_service.armar_candidatos()` -- mismo mecanismo que
    `app/web/notifications.py::get_notification_sender()`, ya no una
    constante fija en código."""
    candidatos = armar_candidatos(
        db,
        CanalNotificacion.SMS,
        [
            ("AWS_SNS", sns_sender.sns_habilitado(), SnsOtpSender()),
            ("LIWA", liwa_sender.configurado(), LiwaOtpSender()),
            ("TWILIO", twilio_sender.configurado(), TwilioOtpSender()),
        ],
    )
    return construir_sender(candidatos, DevOtpSender())


def _registrar_envio_en_segundo_plano(
    session_factory: sessionmaker, exitoso: bool, proveedor: str = None
) -> None:
    """Anota el intento en el registro de envíos SMS (ticket 12) desde una
    sesión PROPIA, corta -- mismo mecanismo/misma razón que `notifications.
    _registrar_envio_en_segundo_plano` (nunca la del request, que puede
    estar cerrada para cuando este `BackgroundTask` corre). Un OTP nunca
    lleva `evento`/`paquete_id` -- no hay Paquete detrás."""
    try:
        db = session_factory()
        try:
            registrar_envio(db, TipoRegistroSms.OTP, exitoso, proveedor=proveedor)
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("No se pudo registrar el envío de OTP.")


def enviar_en_segundo_plano(
    sender: OtpSender, telefono: str, codigo: str, session_factory: sessionmaker = None
) -> None:
    """Best-effort -- `FailoverSmsSender` (si hay 2+ proveedores) solo deja
    propagar una excepción cuando TODOS fallaron (ver `sms_failover.py`), así
    que cualquier excepción que llegue hasta acá es un envío que de verdad no
    salió, no un proveedor caído a mitad de failover -- se registra con
    `logger.exception` (2026-09-01, diagnóstico en vivo: un fallo total y
    silencioso de los 3 proveedores tomó horas de investigación manual por
    AWS CLI porque acá no quedaba ningún rastro).

    `session_factory` (ticket 12, `.scratch/estadisticas-cobro-dashboard`):
    opcional -- sin ella simplemente no hay dónde anotar el registro de
    envíos SMS, pero el envío real sigue intentándose igual."""
    try:
        proveedor = sender.enviar(telefono, codigo)
    except Exception:
        logger.exception("Envío de OTP a %s falló en los 3 proveedores.", telefono)
        if session_factory is not None:
            _registrar_envio_en_segundo_plano(session_factory, exitoso=False)
        return
    if proveedor is not None and session_factory is not None:
        _registrar_envio_en_segundo_plano(session_factory, exitoso=True, proveedor=proveedor)
