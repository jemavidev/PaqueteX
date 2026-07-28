# -*- coding: utf-8 -*-
"""
`get_foto_storage` — selección por entorno (Grupo 15, Ronda 2), mismo patrón
que `get_notification_sender`/`get_otp_sender` con LIWA (Grupo 8).
"""

from app.domain.foto_storage import LocalFotoStorage
from app.domain.s3_foto_storage import S3FotoStorage
from app.web.fotos import get_foto_storage


def test_sin_bucket_configurado_devuelve_local_storage(monkeypatch):
    monkeypatch.delenv("AWS_S3_BUCKET_NAME", raising=False)
    storage = get_foto_storage()
    assert isinstance(storage, LocalFotoStorage)


def test_con_bucket_configurado_devuelve_s3_storage(monkeypatch):
    monkeypatch.setenv("AWS_S3_BUCKET_NAME", "paquetex-fotos-test")
    storage = get_foto_storage()
    assert isinstance(storage, S3FotoStorage)
