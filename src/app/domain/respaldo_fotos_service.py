# -*- coding: utf-8 -*-
"""
Fotos de los paquetes, fuera del respaldo diario (`.scratch/respaldos-y-restauracion`, tickets 10-11): a pedido, se
copian de S3 al disco del servidor -- incremental, con la misma estructura de carpetas de S3 -- para poder
descargarlas ("solo las nuevas" o "todas").
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Protocol

from sqlalchemy.orm import Session


class OrigenFotos(Protocol):
    """De dónde salen las fotos (el bucket de fotos de S3; uno falso en las pruebas)."""

    def listar(self) -> Iterable[tuple[str, int]]:
        """(clave, tamaño en bytes) de cada foto."""
        ...

    def descargar(self, clave: str, destino: Path) -> None: ...


@dataclass(frozen=True)
class ResultadoCopia:
    copiadas: int
    ya_estaban: int


def copiar_fotos(
    origen: OrigenFotos,
    carpeta_copia: Path,
    al_avanzar: Callable[[int, int], None] | None = None,
    cada: int = 50,
) -> ResultadoCopia:
    """Trae a `carpeta_copia/<clave>` las fotos que falten (o que quedaron cortadas: tamaño distinto al de S3). Nunca
    borra nada local, aunque ya no esté en S3. Cada foto se baja a un archivo temporal y se renombra al final, así una
    copia interrumpida nunca deja una foto a medias con su nombre final. `al_avanzar(actual, total)` cada `cada`
    fotos y al terminar."""
    carpeta_copia = Path(carpeta_copia)
    fotos = list(origen.listar())
    total = len(fotos)
    copiadas = ya_estaban = 0
    for i, (clave, tamano) in enumerate(fotos, start=1):
        destino = carpeta_copia / clave
        if destino.is_file() and destino.stat().st_size == tamano:
            ya_estaban += 1
        else:
            destino.parent.mkdir(parents=True, exist_ok=True)
            temporal = destino.with_name(f".{destino.name}.bajando")
            origen.descargar(clave, temporal)
            temporal.rename(destino)
            copiadas += 1
        if al_avanzar is not None and (i % cada == 0 or i == total):
            al_avanzar(i, total)
    return ResultadoCopia(copiadas=copiadas, ya_estaban=ya_estaban)


class S3OrigenFotos:
    """El bucket de fotos. La llave necesita `s3:ListBucket` y `s3:GetObject` solo sobre ese bucket/prefijo."""

    def __init__(self, bucket: str, prefijo: str, region: str, access_key_id: str, secret_access_key: str) -> None:
        import boto3

        self._bucket = bucket
        self._prefijo = prefijo
        self._s3 = boto3.client(
            "s3", region_name=region, aws_access_key_id=access_key_id, aws_secret_access_key=secret_access_key
        )

    def listar(self) -> Iterable[tuple[str, int]]:
        for pagina in self._s3.get_paginator("list_objects_v2").paginate(Bucket=self._bucket, Prefix=self._prefijo):
            for objeto in pagina.get("Contents", []):
                if not objeto["Key"].endswith("/"):
                    yield objeto["Key"], objeto["Size"]

    def descargar(self, clave: str, destino: Path) -> None:
        self._s3.download_file(self._bucket, clave, str(destino))


class LocalOrigenFotos:
    """Las fotos de un ambiente sin S3 (desarrollo: `LocalFotoStorage`, en disco). La clave de cada foto es
    `prefijo + nombre`, la misma ruta de su URL (`/static/fotos-recibidas/<nombre>`), para que "solo las nuevas" las
    encuentre igual que en S3."""

    def __init__(self, carpeta: Path, prefijo: str) -> None:
        self._carpeta = Path(carpeta)
        self._prefijo = prefijo

    def listar(self) -> Iterable[tuple[str, int]]:
        if not self._carpeta.is_dir():
            return []
        return [(self._prefijo + f.name, f.stat().st_size) for f in sorted(self._carpeta.iterdir()) if f.is_file()]

    def descargar(self, clave: str, destino: Path) -> None:
        import shutil

        shutil.copyfile(self._carpeta / clave.removeprefix(self._prefijo), destino)


# --------------------------------------------------------------------------------------------------------------------
# Descargas (ticket 11)
# --------------------------------------------------------------------------------------------------------------------
def clave_de_url(url: str) -> str:
    """La clave en S3 de una foto, a partir de la URL que guarda el sistema (`https://<bucket>.s3...amazonaws.com/<clave>`)."""
    from urllib.parse import unquote, urlparse

    return unquote(urlparse(url).path).lstrip("/")


def fotos_locales(carpeta_copia: Path) -> list[tuple[Path, str]]:
    """(ruta, clave) de cada foto de la copia del servidor, sin los temporales de una copia a medias."""
    carpeta_copia = Path(carpeta_copia)
    if not carpeta_copia.is_dir():
        return []
    return sorted(
        (ruta, ruta.relative_to(carpeta_copia).as_posix())
        for ruta in carpeta_copia.rglob("*")
        if ruta.is_file() and not ruta.name.startswith(".")
    )


@dataclass(frozen=True)
class FotosNuevas:
    fotos: list[tuple[Path, str]]
    sin_copiar: int  # registradas desde la marca pero todavía no copiadas al servidor
    marca_siguiente: datetime  # desde dónde contará la próxima "solo las nuevas" si esta descarga se completa

    @property
    def tamano(self) -> int:
        return sum(ruta.stat().st_size for ruta, _ in self.fotos)


def fotos_nuevas(session: Session, carpeta_copia: Path, desde: datetime | None, ahora: datetime) -> FotosNuevas:
    """Las fotos registradas en el sistema después de `desde` (la última descarga; `None` = nunca: todas) que ya están
    en la copia del servidor. `marca_siguiente` nunca deja atrás una foto registrada que todavía no se copió: queda
    justo antes de la más vieja de ellas, así sale en la próxima descarga (a costa de repetir alguna ya bajada)."""
    from .paquete_foto import PaqueteFoto

    consulta = session.query(PaqueteFoto.url, PaqueteFoto.created_at)
    if desde is not None:
        consulta = consulta.filter(PaqueteFoto.created_at > desde)
    carpeta_copia = Path(carpeta_copia)
    fotos, sin_copiar, primera_sin_copiar = [], 0, None
    for url, creada in consulta:
        clave = clave_de_url(url)
        ruta = carpeta_copia / clave
        if ruta.is_file():
            fotos.append((ruta, clave))
        else:
            sin_copiar += 1
            primera_sin_copiar = creada if primera_sin_copiar is None else min(primera_sin_copiar, creada)
    marca = ahora if primera_sin_copiar is None else primera_sin_copiar - timedelta(microseconds=1)
    return FotosNuevas(fotos=sorted(fotos, key=lambda f: f[1]), sin_copiar=sin_copiar, marca_siguiente=marca)
