# -*- coding: utf-8 -*-
"""
S3CopiadorFotosV1 (`.scratch/importador-v1-espejo`, ticket 06) -- con
`boto3.client` reemplazado por un doble vía `monkeypatch`, mismo patrón que
`test_s3_foto_storage.py`: sin red ni credenciales reales.
"""

import io

import pytest
from botocore.exceptions import ClientError

from app.domain import s3_copiador_fotos_v1 as mod
from app.domain.s3_copiador_fotos_v1 import S3CopiadorFotosV1


class _S3Falso:
    """Un solo "S3" con varios buckets: sirve de origen y de destino."""

    def __init__(self, objetos):
        self.objetos = dict(objetos)  # (bucket, key) -> (bytes, content_type, acl)

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objetos:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    def get_object(self, Bucket, Key):
        contenido, content_type, _ = self.objetos[(Bucket, Key)]
        return {"Body": io.BytesIO(contenido), "ContentType": content_type}

    def put_object(self, Bucket, Key, Body, ContentType, ACL):
        self.objetos[(Bucket, Key)] = (Body, ContentType, ACL)


KEY_V1 = "2026/09/01/packages/announcement_HSZN/receive/HSZN_1.webp"


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("AWS_S3_BUCKET_NAME", "fotos-v2")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    falso = _S3Falso({("elclub-paqueteria", KEY_V1): (b"imagen", "image/webp", "private")})
    monkeypatch.setattr(mod.boto3, "client", lambda *a, **kw: falso)
    return falso


def test_copia_la_foto_privada_de_la_v1_como_publica_en_la_v2(s3):
    url = S3CopiadorFotosV1().copiar(KEY_V1)

    key_destino = "paquetes-recibidos-imagenes/legacy_HSZN_1.webp"
    assert url == f"https://fotos-v2.s3.us-east-1.amazonaws.com/{key_destino}"
    assert s3.objetos[("fotos-v2", key_destino)] == (b"imagen", "image/webp", "public-read")


def test_copiar_dos_veces_no_vuelve_a_leer_el_origen(s3):
    copiador = S3CopiadorFotosV1()
    primera = copiador.copiar(KEY_V1)
    del s3.objetos[("elclub-paqueteria", KEY_V1)]  # si volviera a leerlo, fallaría

    assert copiador.copiar(KEY_V1) == primera


def test_sin_bucket_de_destino_falla_al_construirse(monkeypatch):
    monkeypatch.delenv("AWS_S3_BUCKET_NAME", raising=False)

    with pytest.raises(RuntimeError):
        S3CopiadorFotosV1()


class _S3SinListBucket(_S3Falso):
    """Como el usuario IAM real de staging (2026-09-24): sin `s3:ListBucket`,
    S3 responde 403 -- no 404 -- al consultar una key que no existe."""

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objetos:
            raise ClientError({"Error": {"Code": "403"}}, "HeadObject")


def test_sin_permiso_de_listar_igual_copia(monkeypatch):
    monkeypatch.setenv("AWS_S3_BUCKET_NAME", "fotos-v2")
    falso = _S3SinListBucket({("elclub-paqueteria", KEY_V1): (b"imagen", "image/webp", "private")})
    monkeypatch.setattr(mod.boto3, "client", lambda *a, **kw: falso)

    url = S3CopiadorFotosV1().copiar(KEY_V1)

    assert url.endswith("paquetes-recibidos-imagenes/legacy_HSZN_1.webp")
    assert falso.objetos[("fotos-v2", "paquetes-recibidos-imagenes/legacy_HSZN_1.webp")][2] == "public-read"
