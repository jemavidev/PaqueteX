# -*- coding: utf-8 -*-
"""
S3FotoStorage — implementación real de `FotoStorage` sobre AWS S3
(Grupo 15, Ronda 2 de `ajustes-post-referencia-funcional`), mismo patrón que
`LiwaNotificationSender` frente a `ConsoleNotificationSender`: el dominio no
cambia una línea, solo el *wiring* en `app/web/fotos.py` decide cuál usar.

Investigado en el legacy (`app/services/s3_service.py`): mismo bucket real
(`elclub-paqueteria`) y mismas variables base (`AWS_S3_BUCKET_NAME`,
`AWS_REGION`) — pero el legacy sube con `ACL='private'` y sirve por URL
firmada (con expiración) porque son facturas sensibles. Las fotos de
paquete se muestran en `/consultar`, una pantalla **pública** sin sesión, y
deben seguir visibles indefinidamente (no hay flujo para "refrescar" una
URL firmada expirada ahí), así que este `guardar` sube con
`ACL='public-read'` y devuelve la URL directa y permanente del objeto.

Credenciales (corrección en vivo 2026-08-02, bucket dedicado de staging
`paquetex-staging-fotos` — separado del bucket real de producción a
propósito, mismo criterio de aislamiento que el resto del entorno de
staging): `AWS_S3_ACCESS_KEY_ID`/`AWS_S3_SECRET_ACCESS_KEY` son variables
DISTINTAS de las genéricas `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` —
esas ya están en uso para el fallback de SMS por AWS SNS
(`paquetex-sns-publisher`, ver `notifications.py`), y reusarlas aquí
pisaría esas credenciales con las de un usuario IAM distinto (scope
exclusivo a este bucket). Si las variables S3-específicas no están
definidas, cae a la cadena estándar de `boto3` (rol de instancia, etc.) —
compatible con un futuro despliegue que use un rol IAM en vez de llaves
explícitas.
"""

import mimetypes
import os
import uuid

import boto3


class S3FotoStorage:
    """Sube a S3 con ACL pública y devuelve la URL directa (sin expiración).

    Requiere `AWS_S3_BUCKET_NAME` en el entorno — lanza `RuntimeError` al
    construirse si falta (fail-fast, mismo criterio que `secret_key()` en
    producción). Las credenciales se resuelven de `AWS_S3_ACCESS_KEY_ID`/
    `AWS_S3_SECRET_ACCESS_KEY` si están presentes (nombres deliberadamente
    distintos de las genéricas, que ya sirven a otro propósito — ver
    docstring del módulo); si no, caen a la cadena estándar de `boto3` (rol
    de instancia/IAM, etc.) — no son estrictamente obligatorias aquí.
    """

    def __init__(self) -> None:
        bucket = os.environ.get("AWS_S3_BUCKET_NAME")
        if not bucket:
            raise RuntimeError(
                "AWS_S3_BUCKET_NAME no está definido (requerido por S3FotoStorage)."
            )
        self._bucket = bucket
        self._region = os.environ.get("AWS_REGION", "us-east-1")
        self._prefix = os.environ.get(
            "AWS_S3_PREFIX_FOTOS", "paquetes-recibidos-imagenes/"
        )
        self._client = boto3.client(
            "s3",
            region_name=self._region,
            aws_access_key_id=os.environ.get("AWS_S3_ACCESS_KEY_ID") or None,
            aws_secret_access_key=os.environ.get("AWS_S3_SECRET_ACCESS_KEY") or None,
        )

    def guardar(self, filename: str, contenido: bytes) -> str:
        nombre_unico = f"{uuid.uuid4().hex}_{filename}"
        key = f"{self._prefix}{nombre_unico}"
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=contenido,
            ContentType=content_type,
            ACL="public-read",
        )
        return f"https://{self._bucket}.s3.{self._region}.amazonaws.com/{key}"
