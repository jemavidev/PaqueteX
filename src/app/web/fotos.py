# -*- coding: utf-8 -*-
"""
Wiring de almacenamiento de fotos de la capa web (ADR-0004).

`get_foto_storage` (dependencia FastAPI) usa `S3FotoStorage` si hay bucket
configurado (`AWS_S3_BUCKET_NAME`), o `LocalFotoStorage` si no
(desarrollo/tests — mismo patrón que `get_notification_sender`/
`get_otp_sender` con LIWA, Grupo 15 de Ronda 2 sobre el Grupo 8 de Ronda 1).
No se cachea: se lee el entorno en cada llamada, barato de construir.

`subir_fotos_diferido` es la contraparte de `notifications.
enviar_en_segundo_plano` para fotos (corrección en vivo 2026-08-01): la subida
real (`storage.guardar`, un `put_object` de S3 sin timeout propio) se saca del
request y se pasa a un `BackgroundTask`, igual que el envío de SMS.
"""

import logging
import os
import uuid
from pathlib import Path

from app.domain.foto_storage import FotoStorage, LocalFotoStorage
from app.domain.paquete import Paquete
from app.domain.paquete_foto_service import agregar_foto
from app.domain.s3_foto_storage import S3FotoStorage

_FOTOS_DIR = Path(__file__).resolve().parent / "static" / "fotos-recibidas"

logger = logging.getLogger(__name__)


def get_foto_storage() -> FotoStorage:
    if os.environ.get("AWS_S3_BUCKET_NAME"):
        return S3FotoStorage()
    return LocalFotoStorage(_FOTOS_DIR)


def subir_fotos_diferido(
    session_factory,
    storage: FotoStorage,
    paquete_id: uuid.UUID,
    archivos: list[tuple[str, bytes]],
) -> None:
    """Ejecuta `agregar_foto` para cada `(filename, contenido)` — pensado para
    pasarse a `BackgroundTasks.add_task`. `archivos` ya viene leído a memoria
    (el `UploadFile` del request no sobrevive fuera de él).

    Abre su PROPIA sesión vía `session_factory` (ver `db.get_session_factory`)
    — nunca la del request. Busca el `Paquete` por id (nunca recibe el objeto
    ORM del request, que pertenece a otra sesión).

    Best-effort, mismo espíritu que `notifications.enviar_en_segundo_plano`:
    si `storage.guardar` falla para un archivo (ej. S3 caído), se registra en
    logs y se sigue con los demás — recibir un paquete NUNCA depende de que
    las fotos se suban. No hay (todavía) una sección de "novedades" visible
    al staff para este tipo de falla, así que por ahora el rastro vive solo
    en logs del servidor."""
    session = session_factory()
    try:
        paquete = session.get(Paquete, paquete_id)
        if paquete is None:
            return
        for filename, contenido in archivos:
            try:
                agregar_foto(session, paquete, storage, filename, contenido)
            except ValueError:
                break  # tope de fotos alcanzado, igual que en el flujo síncrono
            except Exception:
                logger.exception(
                    "fallo subiendo foto diferida (paquete_id=%s, filename=%s)",
                    paquete_id,
                    filename,
                )
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("fallo guardando fotos diferidas (paquete_id=%s)", paquete_id)
    finally:
        session.close()
