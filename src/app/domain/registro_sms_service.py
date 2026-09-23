# -*- coding: utf-8 -*-
"""
Servicio de dominio de `RegistroSms` (módulo "Registro de envíos SMS",
`.scratch/estadisticas-cobro-dashboard`, ticket 11).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from .paquete import EstadoPaquete
from .registro_sms import RegistroSms, TipoRegistroSms


def registrar_envio(
    session: Session,
    tipo: TipoRegistroSms,
    exitoso: bool,
    proveedor: str = None,
    evento: EstadoPaquete = None,
    paquete_id=None,
) -> None:
    """Anota un intento de envío SMS -- BEST-EFFORT, incondicional: cualquier
    excepción se traga acá mismo, nunca debe tumbar el envío real que ya
    salió ni la transición del Paquete que lo disparó (ver
    `notificacion_service.notificar_evento` y `app/web/notifications.
    enviar_en_segundo_plano`, que llaman a esta función justo después de
    intentar el envío real, dentro de la MISMA sesión que puede tener otro
    trabajo pendiente sin comitear todavía).

    Aislado con un SAVEPOINT (`session.begin_nested`) a propósito: si el
    `INSERT` fallara (ej. una columna que ya no exista, una migración
    pendiente), un `session.rollback()` a secas deshacía TAMBIÉN cualquier
    cambio previo sin comitear de esa misma sesión -- exactamente lo que
    este ticket prohíbe. El SAVEPOINT limita el daño a esta sola fila:
    ante una falla, se revierte solo hasta ahí, dejando el resto de la
    transacción intacto."""
    try:
        with session.begin_nested():
            registro = RegistroSms(
                id=uuid.uuid4(),
                tipo=tipo,
                evento=evento,
                paquete_id=paquete_id,
                proveedor=proveedor,
                exitoso=exitoso,
                created_at=datetime.now(timezone.utc),
            )
            session.add(registro)
            session.flush()
    except Exception:
        pass


def contar_envios(
    session: Session,
    tipo: TipoRegistroSms = None,
    proveedor: str = None,
    exitoso: bool = None,
    desde: datetime = None,
    hasta: datetime = None,
) -> int:
    """Cuántos registros matchean los filtros dados -- todos opcionales (sin
    ninguno, el total histórico). Fuente de las tarjetas de SMS del tablero
    de estadísticas de cobro (tickets 14-16)."""
    query = session.query(func.count(RegistroSms.id))
    if tipo is not None:
        query = query.filter(RegistroSms.tipo == tipo)
    if proveedor is not None:
        query = query.filter(RegistroSms.proveedor == proveedor)
    if exitoso is not None:
        query = query.filter(RegistroSms.exitoso == exitoso)
    if desde is not None:
        query = query.filter(RegistroSms.created_at >= desde)
    if hasta is not None:
        query = query.filter(RegistroSms.created_at <= hasta)
    return int(query.scalar())


def fecha_primer_registro(session: Session) -> datetime | None:
    """El `created_at` del registro más antiguo que exista -- `None` sin
    ninguno todavía (spec.md: no hay forma de reconstruir el histórico de
    SMS anterior a la activación de este registro)."""
    return session.query(func.min(RegistroSms.created_at)).scalar()
