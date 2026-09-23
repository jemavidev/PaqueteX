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

from PIL import Image, ImageOps

# Issue 389 (.scratch/pendientes-cliente, "comprimidas pero con calidad para leerlas"): las fotos son sobre todo de
# etiquetas y guías -- 2048 px de lado mayor y calidad 85 (antes 1600/82) dejan el texto legible, en unos pocos
# cientos de KB. Todo esto corre en el SERVIDOR: el equipo de portería (a veces de poca memoria) sube la foto tal cual.
_LADO_MAXIMO = 2048
_CALIDAD_JPEG = 85


class ImagenInvalida(ValueError):
    """`contenido` no es una imagen que Pillow pueda abrir (issue 389): se rechaza en vez de subirlo tal cual."""


def comprimir_imagen(contenido: bytes, filename: str | None) -> tuple[bytes, str | None]:
    """Endereza, redimensiona a un máximo de `_LADO_MAXIMO` px de lado mayor (nunca agranda una imagen más chica) y
    recomprime a JPEG calidad `_CALIDAD_JPEG`.

    Issue 389 (.scratch/pendientes-cliente):
    - aplica la orientación EXIF (`ImageOps.exif_transpose`): la cámara del celular guarda la foto "acostada" con una
      marca que dice cómo girarla, y al recomprimir esa marca se perdía -- las fotos verticales quedaban giradas 90°;
    - rechaza (`ImagenInvalida`) lo que no es una imagen, en vez de devolverlo tal cual: antes se subía público al
      almacenamiento con el tipo que dijera la extensión.

    Devuelve `(contenido_final, filename_final)` -- `filename` cambia su extensión a `.jpg`, para que el
    `Content-Type` que infiere `S3FotoStorage` (por extensión) siga describiendo el archivo de verdad.
    """
    try:
        imagen = Image.open(io.BytesIO(contenido))
        imagen.load()
    except Exception as exc:
        raise ImagenInvalida("El archivo no es una imagen.") from exc
    imagen = ImageOps.exif_transpose(imagen)
    imagen = imagen.convert("RGB")  # tolera PNG con alpha, paletas, etc.
    imagen.thumbnail((_LADO_MAXIMO, _LADO_MAXIMO))
    buffer = io.BytesIO()
    imagen.save(buffer, format="JPEG", quality=_CALIDAD_JPEG, optimize=True)
    nombre_final = f"{Path(filename).stem}.jpg" if filename else filename
    return buffer.getvalue(), nombre_final
