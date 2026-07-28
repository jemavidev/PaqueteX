# -*- coding: utf-8 -*-
"""
S3FotoStorage — implementación real de `FotoStorage` sobre AWS S3
(Grupo 15, Ronda 2 de `ajustes-post-referencia-funcional`), mismo patrón que
`LiwaNotificationSender` frente a `ConsoleNotificationSender`: el dominio no
cambia una línea, solo el *wiring* en `app/web/fotos.py` decide cuál usar.

Investigado en el legacy (`app/services/s3_service.py`): mismas variables de
entorno (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_BUCKET_NAME`,
`AWS_REGION`). Diferencia deliberada frente al legacy: el legacy sube con
`ACL='private'` y sirve por URL firmada (con expiración) porque son facturas
sensibles — las fotos de paquete se muestran en `/consultar`, una pantalla
**pública** sin sesión, y deben seguir visibles indefinidamente (no hay flujo
para "refrescar" una URL firmada expirada ahí), así que este `guardar` sube
con `ACL='public-read'` y devuelve la URL directa y permanente del objeto.
"""

import mimetypes
import os
import uuid

import boto3


class S3FotoStorage:
    """Sube a S3 con ACL pública y devuelve la URL directa (sin expiración).

    Requiere `AWS_S3_BUCKET_NAME` en el entorno — lanza `RuntimeError` al
    construirse si falta (fail-fast, mismo criterio que `secret_key()` en
    producción). Las credenciales (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`)
    se resuelven vía la cadena estándar de `boto3` si no están en el entorno
    (rol de instancia/IAM, etc.) — no son estrictamente obligatorias aquí.
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
        self._client = boto3.client("s3", region_name=self._region)

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
