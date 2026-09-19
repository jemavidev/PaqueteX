# -*- coding: utf-8 -*-
"""
`comprimir_imagen` (análisis de diseño 2026-09-18, sistema de fotos de
paquete): redimensiona + recomprime a JPEG para bajar el peso de una foto
de cámara sin pérdida visible perceptible, manteniendo alta calidad. Nunca
falla -- si `contenido` no es una imagen que Pillow pueda abrir, se
devuelve tal cual (mismo criterio "best-effort" que `subir_fotos_diferido`:
nunca perder una foto real por un error de procesamiento).
"""

import io

import pytest
from PIL import Image

from app.domain.imagen_service import comprimir_imagen


def _imagen_de_prueba(ancho, alto, formato="JPEG"):
    buffer = io.BytesIO()
    Image.new("RGB", (ancho, alto), color=(200, 60, 60)).save(buffer, format=formato)
    return buffer.getvalue()


def test_reduce_una_imagen_grande_y_pesada():
    original = _imagen_de_prueba(3000, 2000)

    resultado, filename = comprimir_imagen(original, "recibo.jpg")

    assert len(resultado) < len(original)
    imagen = Image.open(io.BytesIO(resultado))
    assert imagen.format == "JPEG"
    assert max(imagen.size) <= 1600
    assert filename == "recibo.jpg"


def test_no_agranda_una_imagen_ya_chica():
    original = _imagen_de_prueba(400, 300)

    resultado, _ = comprimir_imagen(original, "recibo.jpg")

    imagen = Image.open(io.BytesIO(resultado))
    assert imagen.size == (400, 300)


def test_convierte_png_a_jpg_y_actualiza_la_extension():
    original = _imagen_de_prueba(800, 600, formato="PNG")

    resultado, filename = comprimir_imagen(original, "recibo.png")

    imagen = Image.open(io.BytesIO(resultado))
    assert imagen.format == "JPEG"
    assert filename == "recibo.jpg"


def test_bytes_que_no_son_una_imagen_pasan_sin_cambios():
    """Mismos bytes de prueba que usan los tests de `receive_action`
    (`b"foto-a"`, etc.) -- nunca deben hacer fallar la compresión."""
    resultado, filename = comprimir_imagen(b"esto-no-es-una-imagen", "cualquiera.jpg")

    assert resultado == b"esto-no-es-una-imagen"
    assert filename == "cualquiera.jpg"


def test_sin_nombre_de_archivo_no_falla():
    resultado, filename = comprimir_imagen(b"esto-no-es-una-imagen", None)

    assert resultado == b"esto-no-es-una-imagen"
    assert filename is None
