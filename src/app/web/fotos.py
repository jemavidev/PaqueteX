# -*- coding: utf-8 -*-
"""
Wiring de almacenamiento de fotos de la capa web (ADR-0004).

`get_foto_storage` (dependencia FastAPI) siempre devuelve `LocalFotoStorage`
por ahora — no hay S3 real conectado todavía (pendiente de confirmar bucket,
ver Grupo 2 de `ajustes-post-referencia-funcional/REQUERIMIENTOS.md`). El día
que se conecte S3, esta es la única función que cambia.
"""

from pathlib import Path

from app.domain.foto_storage import FotoStorage, LocalFotoStorage

_FOTOS_DIR = Path(__file__).resolve().parent / "static" / "fotos-recibidas"


def get_foto_storage() -> FotoStorage:
    return LocalFotoStorage(_FOTOS_DIR)
