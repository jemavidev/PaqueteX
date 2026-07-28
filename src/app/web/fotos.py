# -*- coding: utf-8 -*-
"""
Wiring de almacenamiento de fotos de la capa web (ADR-0004).

`get_foto_storage` (dependencia FastAPI) usa `S3FotoStorage` si hay bucket
configurado (`AWS_S3_BUCKET_NAME`), o `LocalFotoStorage` si no
(desarrollo/tests — mismo patrón que `get_notification_sender`/
`get_otp_sender` con LIWA, Grupo 15 de Ronda 2 sobre el Grupo 8 de Ronda 1).
No se cachea: se lee el entorno en cada llamada, barato de construir.
"""

import os
from pathlib import Path

from app.domain.foto_storage import FotoStorage, LocalFotoStorage
from app.domain.s3_foto_storage import S3FotoStorage

_FOTOS_DIR = Path(__file__).resolve().parent / "static" / "fotos-recibidas"


def get_foto_storage() -> FotoStorage:
    if os.environ.get("AWS_S3_BUCKET_NAME"):
        return S3FotoStorage()
    return LocalFotoStorage(_FOTOS_DIR)
