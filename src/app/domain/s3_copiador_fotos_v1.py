# -*- coding: utf-8 -*-
"""
S3CopiadorFotosV1 — implementación real del puerto `CopiadorFotosV1` del
importador espejo (`.scratch/importador-v1-espejo`, ticket 06).

Origen: el bucket PRIVADO de la v1 (`elclub-paqueteria`, fotos con
`ACL='private'`), leído con credenciales propias de la v1. Destino: el bucket
de fotos que la v2 tenga configurado (las MISMAS variables que
`S3FotoStorage`), en `public-read` porque `/consultar` es pública y no puede
renovar URLs firmadas.

Son dos cuentas/credenciales distintas, así que no sirve `copy_object` del lado
del servidor: se hace `get_object` + `put_object`. La key de destino se deriva
de la de origen (`<prefijo-fotos-v2>legacy_<nombre-original>`, la convención
del import del 2026-08-20), y si ya existe no se vuelve a copiar -- copiar dos
veces la misma foto nunca la duplica.

Variables de entorno:
    V1_AWS_ACCESS_KEY_ID / V1_AWS_SECRET_ACCESS_KEY   lectura del bucket de la v1
    V1_AWS_S3_BUCKET (default `elclub-paqueteria`), V1_AWS_REGION (default `us-east-1`)
    AWS_S3_BUCKET_NAME, AWS_REGION, AWS_S3_PREFIX_FOTOS,
    AWS_S3_ACCESS_KEY_ID / AWS_S3_SECRET_ACCESS_KEY    destino (ver `S3FotoStorage`)
"""

import os

import boto3
from botocore.exceptions import ClientError


class S3CopiadorFotosV1:
    def __init__(self) -> None:
        destino = os.environ.get("AWS_S3_BUCKET_NAME")
        if not destino:
            raise RuntimeError("AWS_S3_BUCKET_NAME no está definido (bucket de fotos de la v2).")
        self._bucket_origen = os.environ.get("V1_AWS_S3_BUCKET", "elclub-paqueteria")
        self._bucket_destino = destino
        self._region_destino = os.environ.get("AWS_REGION", "us-east-1")
        self._prefijo = os.environ.get("AWS_S3_PREFIX_FOTOS", "paquetes-recibidos-imagenes/")
        self._origen = boto3.client(
            "s3",
            region_name=os.environ.get("V1_AWS_REGION", "us-east-1"),
            aws_access_key_id=os.environ.get("V1_AWS_ACCESS_KEY_ID") or None,
            aws_secret_access_key=os.environ.get("V1_AWS_SECRET_ACCESS_KEY") or None,
        )
        self._destino = boto3.client(
            "s3",
            region_name=self._region_destino,
            aws_access_key_id=os.environ.get("AWS_S3_ACCESS_KEY_ID") or None,
            aws_secret_access_key=os.environ.get("AWS_S3_SECRET_ACCESS_KEY") or None,
        )

    def copiar(self, s3_key_origen: str) -> str:
        key_destino = f"{self._prefijo}legacy_{s3_key_origen.rsplit('/', 1)[-1]}"
        url = f"https://{self._bucket_destino}.s3.{self._region_destino}.amazonaws.com/{key_destino}"
        if self._existe(key_destino):
            return url
        objeto = self._origen.get_object(Bucket=self._bucket_origen, Key=s3_key_origen)
        self._destino.put_object(
            Bucket=self._bucket_destino,
            Key=key_destino,
            Body=objeto["Body"].read(),
            ContentType=objeto.get("ContentType") or "image/webp",
            ACL="public-read",
        )
        return url

    def _existe(self, key: str) -> bool:
        """¿Ya está en destino? Solo un atajo para no releer el origen: si no se
        puede confirmar, se copia igual (`put_object` a una key fija es
        idempotente). Sin `s3:ListBucket` -- el caso del usuario IAM de staging,
        2026-09-24 -- S3 responde 403 y no 404 para una key inexistente."""
        try:
            self._destino.head_object(Bucket=self._bucket_destino, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("403", "404", "NoSuchKey", "NotFound", "AccessDenied"):
                return False
            raise
