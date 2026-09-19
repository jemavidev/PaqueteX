# -*- coding: utf-8 -*-
"""
Compresión de fotos de Paquete (análisis de diseño 2026-09-18, sistema de
fotos) -- una foto de cámara moderna pesa varios MB; el propósito real
(identificar un paquete, leer una guía) no necesita esa resolución. Reducir
acá, una sola vez, antes de guardar, es más barato que cargarla completa
en cada vista de `/consultar`/`/mis-paquetes` para siempre.
"""

import io
from pathlib import Path

from PIL import Image

_LADO_MAXIMO = 1600
_CALIDAD_JPEG = 82


def comprimir_imagen(contenido: bytes, filename: str | None) -> tuple[bytes, str | None]:
    """Redimensiona `contenido` a un máximo de `_LADO_MAXIMO` px de lado
    mayor (nunca agranda una imagen más chica) y recomprime a JPEG calidad
    `_CALIDAD_JPEG` -- reduce el peso de varios MB a un rango manejable sin
    pérdida visible perceptible para el propósito de estas fotos.

    Nunca falla: si `contenido` no es una imagen que Pillow pueda abrir
    (archivo corrupto, formato no soportado, o bytes de prueba sin relación
    real con una imagen), lo devuelve TAL CUAL -- mismo criterio
    "best-effort" que `subir_fotos_diferido`: nunca perder una foto real
    por un error de procesamiento.

    Devuelve `(contenido_final, filename_final)` -- `filename` cambia su
    extensión a `.jpg` SOLO cuando la conversión realmente ocurrió, para
    que el `Content-Type` que infiere `S3FotoStorage` (por extensión) siga
    describiendo el archivo de verdad.
    """
    try:
        imagen = Image.open(io.BytesIO(contenido))
        imagen.load()
        imagen = imagen.convert("RGB")  # tolera PNG con alpha, paletas, etc.
        imagen.thumbnail((_LADO_MAXIMO, _LADO_MAXIMO))
        buffer = io.BytesIO()
        imagen.save(buffer, format="JPEG", quality=_CALIDAD_JPEG, optimize=True)
    except Exception:
        return contenido, filename

    nombre_final = f"{Path(filename).stem}.jpg" if filename else filename
    return buffer.getvalue(), nombre_final
