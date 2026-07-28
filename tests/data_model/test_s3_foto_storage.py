# -*- coding: utf-8 -*-
"""
S3FotoStorage (Grupo 15, Ronda 2) — probado con `boto3.client` reemplazado
por un doble de prueba vía `monkeypatch` (no depende de red real ni de
credenciales verdaderas en la suite automatizada, mismo patrón que
`test_liwa_sender.py`).
"""

import pytest

from app.domain import s3_foto_storage as mod
from app.domain.s3_foto_storage import S3FotoStorage


class _ClienteS3Falso:
    def __init__(self):
        self.llamadas = []

    def put_object(self, **kwargs):
        self.llamadas.append(kwargs)


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setenv("AWS_S3_BUCKET_NAME", "paquetex-fotos-test")
    monkeypatch.setenv("AWS_REGION", "us-east-1")


def test_guardar_sube_con_acl_publica_y_devuelve_url_directa(monkeypatch):
    cliente = _ClienteS3Falso()
    monkeypatch.setattr(mod.boto3, "client", lambda *a, **kw: cliente)

    storage = S3FotoStorage()
    url = storage.guardar("recibo.jpg", b"contenido-de-prueba")

    assert len(cliente.llamadas) == 1
    llamada = cliente.llamadas[0]
    assert llamada["Bucket"] == "paquetex-fotos-test"
    assert llamada["Body"] == b"contenido-de-prueba"
    assert llamada["ACL"] == "public-read"
    assert llamada["ContentType"] == "image/jpeg"
    assert llamada["Key"].startswith("paquetes-recibidos-imagenes/")
    assert llamada["Key"].endswith("_recibo.jpg")

    assert url == f"https://paquetex-fotos-test.s3.us-east-1.amazonaws.com/{llamada['Key']}"


def test_guardar_no_colisiona_nombres_repetidos(monkeypatch):
    cliente = _ClienteS3Falso()
    monkeypatch.setattr(mod.boto3, "client", lambda *a, **kw: cliente)

    storage = S3FotoStorage()
    url1 = storage.guardar("foto.jpg", b"uno")
    url2 = storage.guardar("foto.jpg", b"dos")

    assert url1 != url2


def test_prefijo_configurable_por_variable_de_entorno(monkeypatch):
    monkeypatch.setenv("AWS_S3_PREFIX_FOTOS", "otro-prefijo/")
    cliente = _ClienteS3Falso()
    monkeypatch.setattr(mod.boto3, "client", lambda *a, **kw: cliente)

    S3FotoStorage().guardar("recibo.jpg", b"contenido")

    assert cliente.llamadas[0]["Key"].startswith("otro-prefijo/")


def test_sin_bucket_configurado_lanza_runtimeerror(monkeypatch):
    monkeypatch.delenv("AWS_S3_BUCKET_NAME", raising=False)
    with pytest.raises(RuntimeError):
        S3FotoStorage()
