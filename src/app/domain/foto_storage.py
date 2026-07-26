# -*- coding: utf-8 -*-
"""
Puerto de almacenamiento de fotos de Paquete — el dominio no sabe (ni le
importa) dónde vive el archivo físicamente. La implementación real (S3,
Grupo 2 de `ajustes-post-referencia-funcional/REQUERIMIENTOS.md`, pendiente
de confirmar bucket) es otra rebanada; aquí solo el punto de extensión + una
implementación de desarrollo que guarda en disco local (mismo patrón que
`OtpSender`/`NotificationSender`).
"""

import uuid
from pathlib import Path
from typing import Protocol


class FotoStorage(Protocol):
    def guardar(self, filename: str, contenido: bytes) -> str:
        """Guarda `contenido` (los bytes del archivo) y devuelve la URL/ruta
        pública para acceder a él."""
        ...


class LocalFotoStorage:
    """Implementación de desarrollo: guarda en disco local bajo `directorio`,
    NO sube a S3 real. Devuelve una ruta servible vía `/static` (el directorio
    por defecto vive dentro de `app/web/static`)."""

    def __init__(self, directorio: Path) -> None:
        self._directorio = Path(directorio)
        self._directorio.mkdir(parents=True, exist_ok=True)

    def guardar(self, filename: str, contenido: bytes) -> str:
        nombre_unico = f"{uuid.uuid4().hex}_{filename}"
        (self._directorio / nombre_unico).write_bytes(contenido)
        return f"/static/fotos-recibidas/{nombre_unico}"
