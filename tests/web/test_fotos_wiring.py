# -*- coding: utf-8 -*-
"""
`get_foto_storage` — selección por entorno (Grupo 15, Ronda 2), mismo patrón
que `get_notification_sender`/`get_otp_sender` con LIWA (Grupo 8).
"""

import io

from PIL import Image

from app.domain.foto_storage import LocalFotoStorage
from app.domain.s3_foto_storage import S3FotoStorage
from app.web.fotos import get_foto_storage, procesar_foto_individual


def test_sin_bucket_configurado_devuelve_local_storage(monkeypatch):
    monkeypatch.delenv("AWS_S3_BUCKET_NAME", raising=False)
    storage = get_foto_storage()
    assert isinstance(storage, LocalFotoStorage)


def test_con_bucket_configurado_devuelve_s3_storage(monkeypatch):
    monkeypatch.setenv("AWS_S3_BUCKET_NAME", "paquetex-fotos-test")
    storage = get_foto_storage()
    assert isinstance(storage, S3FotoStorage)


# --------------------------------------------------------------------------- #
# `procesar_foto_individual` (análisis de diseño 2026-09-18, subida
# progresiva): comprime + sube UNA foto, sin tocar la base de datos --
# pensado para el endpoint `/paquetes/{id}/fotos`, un request por foto,
# mientras el staff sigue tomando las siguientes.
# --------------------------------------------------------------------------- #
def test_procesar_foto_individual_comprime_y_sube(tmp_path):
    storage = LocalFotoStorage(tmp_path)
    buffer = io.BytesIO()
    Image.new("RGB", (3000, 2000), color=(10, 20, 30)).save(buffer, format="JPEG")
    original = buffer.getvalue()

    url = procesar_foto_individual(storage, "recibo.jpg", original)

    assert url.startswith("/static/fotos-recibidas/")
    nombre_archivo = url.rsplit("/", 1)[-1]
    contenido_guardado = (tmp_path / nombre_archivo).read_bytes()
    assert len(contenido_guardado) < len(original)  # se comprimió antes de guardar


def test_procesar_foto_individual_con_bytes_invalidos_los_guarda_igual(tmp_path):
    storage = LocalFotoStorage(tmp_path)

    url = procesar_foto_individual(storage, "recibo.jpg", b"no-es-una-imagen")

    nombre_archivo = url.rsplit("/", 1)[-1]
    assert (tmp_path / nombre_archivo).read_bytes() == b"no-es-una-imagen"
