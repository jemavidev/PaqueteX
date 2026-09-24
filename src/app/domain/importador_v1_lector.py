# -*- coding: utf-8 -*-
"""
Lector de la base de la v1 para el importador espejo
(`.scratch/importador-v1-espejo`).

Adaptador DELGADO a propósito: solo arma la `InstantaneaV1` a partir de SQL
contra la base de producción v1 (`paqueteria_v4`), sin reglas de negocio --
esas viven en `importador_v1_service`, que es lo que se prueba. Se valida con
`--simular` contra la base real (spec, Testing Decisions).

Abre la conexión en modo SOLO LECTURA (`SET TRANSACTION READ ONLY`) además de
usar el rol `paquetex_importador`, que solo tiene `SELECT`: dos barreras para
que el importador nunca escriba en producción.
"""

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, text

from .importador_v1_service import (
    AnuncioV1,
    ClienteV1,
    FotoV1,
    HistorialV1,
    InstantaneaV1,
    PaqueteV1,
    UsuarioV1,
)

# La v1 mezcla columnas `timestamp with time zone` (packages, file_uploads) y
# `without time zone` (anuncios, historial). Las segundas guardan
# la hora LOCAL de Bogotá (verificado 2026-09-23: `package_history.changed_at`
# va 5 h detrás del `packages.received_at` del mismo evento).
_ZONA_V1_SIN_TZ = ZoneInfo("America/Bogota")


def _utc(valor: datetime | None) -> datetime | None:
    if valor is None:
        return None
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=_ZONA_V1_SIN_TZ)
    return valor.astimezone(timezone.utc)


def _texto_o_none(valor) -> str | None:
    valor = (valor or "").strip()
    return valor or None


_SQL_PAQUETES = """
    SELECT p.id, p.customer_id::text AS cliente_id, p.tracking_number, p.guide_number,
           p.display_name, p.status::text AS estado, p.package_type::text AS package_type,
           p.package_condition::text AS package_condition, p.announced_at, p.received_at,
           p.delivered_at, p.cancelled_at, p.total_amount,
           (SELECT a.id::text FROM package_announcements_new a
             WHERE a.package_id = p.id ORDER BY a.created_at LIMIT 1) AS anuncio_id
      FROM packages p
     ORDER BY p.id
"""

_SQL_HISTORIAL = """
    SELECT package_id, new_status::text AS estado, changed_by, changed_at, additional_data
      FROM package_history
     ORDER BY changed_at, id
"""


def _motivo_cancelacion(additional_data) -> str | None:
    """`package_history.additional_data` trae `{"cancellation_reason": ...}`
    en las filas `CANCELADO` (como texto JSON o ya decodificado)."""
    if additional_data is None:
        return None
    if isinstance(additional_data, str):
        try:
            additional_data = json.loads(additional_data)
        except ValueError:
            return None
    if not isinstance(additional_data, dict):
        return None
    return _texto_o_none(additional_data.get("cancellation_reason"))


# Solo los anuncios vivos que todavía no son paquete: los procesados ya llegan
# como `packages`, y los inactivos no existen para el residente.
_SQL_ANUNCIOS = """
    SELECT id::text AS id, customer_id::text AS cliente_id, tracking_code, guide_number,
           customer_name, announced_at
      FROM package_announcements_new
     WHERE is_active AND NOT is_processed AND package_id IS NULL
     ORDER BY announced_at
"""


def leer_instantanea_v1(database_url: str) -> InstantaneaV1:
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            clientes = [
                ClienteV1(
                    id=str(r.id),
                    telefono=r.phone,
                    nombre=_texto_o_none(r.full_name)
                    or f"{r.first_name or ''} {r.last_name or ''}".strip(),
                    email=_texto_o_none(r.email),
                )
                for r in conn.execute(
                    text("SELECT id, phone, full_name, first_name, last_name, email FROM customers ORDER BY created_at, id")
                )
            ]
            usuarios = [
                UsuarioV1(
                    username=r.username,
                    email=_texto_o_none(r.email),
                    nombre=_texto_o_none(r.full_name) or r.username,
                    rol=r.rol,
                )
                for r in conn.execute(text("SELECT username, email, full_name, role::text AS rol FROM users"))
            ]
            historial = [
                HistorialV1(
                    paquete_id=r.package_id,
                    estado=r.estado,
                    changed_by=_texto_o_none(r.changed_by),
                    changed_at=_utc(r.changed_at),
                    motivo_cancelacion=_motivo_cancelacion(r.additional_data) if r.estado == "CANCELADO" else None,
                )
                for r in conn.execute(text(_SQL_HISTORIAL))
            ]
            paquetes = [
                PaqueteV1(
                    id=r.id,
                    cliente_id=r.cliente_id,
                    tracking_number=r.tracking_number,
                    guide_number=_texto_o_none(r.guide_number),
                    display_name=_texto_o_none(r.display_name),
                    estado=r.estado,
                    package_type=r.package_type,
                    package_condition=r.package_condition,
                    announced_at=_utc(r.announced_at),
                    received_at=_utc(r.received_at),
                    delivered_at=_utc(r.delivered_at),
                    cancelled_at=_utc(r.cancelled_at),
                    anuncio_id=r.anuncio_id,
                    total_amount=r.total_amount,
                )
                for r in conn.execute(text(_SQL_PAQUETES))
            ]
            anuncios = [
                AnuncioV1(
                    id=r.id,
                    cliente_id=r.cliente_id,
                    tracking_code=r.tracking_code,
                    guide_number=_texto_o_none(r.guide_number),
                    nombre_destinatario=_texto_o_none(r.customer_name),
                    announced_at=_utc(r.announced_at),
                )
                for r in conn.execute(text(_SQL_ANUNCIOS))
            ]
            fotos = [
                FotoV1(id=r.id, paquete_id=r.package_id, s3_key=r.s3_key, creada_en=_utc(r.created_at))
                for r in conn.execute(
                    text(
                        "SELECT id, package_id, s3_key, created_at FROM file_uploads"
                        " WHERE package_id IS NOT NULL AND s3_key IS NOT NULL ORDER BY id"
                    )
                )
            ]
            conn.rollback()
    finally:
        engine.dispose()
    return InstantaneaV1(
        clientes=clientes,
        usuarios=usuarios,
        paquetes=paquetes,
        historial=historial,
        anuncios=anuncios,
        fotos=fotos,
    )
