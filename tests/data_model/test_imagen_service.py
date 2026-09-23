# -*- coding: utf-8 -*-
"""
`comprimir_imagen` (análisis de diseño 2026-09-18, sistema de fotos de
paquete): redimensiona + recomprime a JPEG para bajar el peso de una foto
de cámara sin pérdida visible perceptible, manteniendo alta calidad.

Issue 389 (.scratch/pendientes-cliente): legible (lado mayor 2048 px, calidad 85, antes 1600/82), con la orientación
EXIF aplicada (una foto vertical del celular quedaba acostada) y rechazo de lo que no es una imagen (antes se subía
tal cual, público, con el tipo que dijera la extensión).
"""

import io

import pytest
from PIL import Image

from app.domain.imagen_service import ImagenInvalida, comprimir_imagen


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
    assert max(imagen.size) == 2048  # issue 389: legible (antes 1600)
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


def test_bytes_que_no_son_una_imagen_se_rechazan():
    """Issue 389: antes pasaban tal cual y se subían públicos con el tipo de la extensión (un `.html` quedaba
    servido como página)."""
    with pytest.raises(ImagenInvalida):
        comprimir_imagen(b"esto-no-es-una-imagen", "cualquiera.jpg")


def test_sin_nombre_de_archivo_no_falla():
    resultado, filename = comprimir_imagen(_imagen_de_prueba(400, 300), None)

    assert Image.open(io.BytesIO(resultado)).format == "JPEG"
    assert filename is None


def test_una_foto_vertical_del_celular_queda_vertical():
    """La cámara guarda la foto "acostada" con una marca EXIF de orientación; sin aplicarla, quedaba girada 90°."""
    exif = Image.Exif()
    exif[0x0112] = 6  # "rotar 90°": se ve vertical, 300 de ancho x 400 de alto
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), color=(10, 120, 200)).save(buffer, format="JPEG", exif=exif.tobytes())

    resultado, _ = comprimir_imagen(buffer.getvalue(), "vertical.jpg")

    assert Image.open(io.BytesIO(resultado)).size == (300, 400)
