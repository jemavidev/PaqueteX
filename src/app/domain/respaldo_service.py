# -*- coding: utf-8 -*-
"""
Respaldos de una instalación de PaqueteX (`.scratch/respaldos-y-restauracion`).

Un respaldo es una carpeta autocontenida con fecha -- `base_datos.dump` (`pg_dump`
en formato nativo) y `manifiesto.txt` (qué es: fecha, dominio, commit, versión de la
base, motivo, conteos y huellas) -- que se arma aparte y solo aparece completa al
final. Lo usan, por el mismo camino, el cron diario, el paso previo al deploy y el
botón "Respaldar ahora" (ver `app.respaldo_cli`).
"""

import configparser
import contextlib
import enum
import fcntl
import hashlib
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from .configuracion_conjunto_service import obtener_nombre_conjunto
from .zona_horaria import ZONA_HORARIA_APP

ARCHIVO_BD = "base_datos.dump"
ARCHIVO_MANIFIESTO = "manifiesto.txt"

# Las tablas cuyo conteo guarda el manifiesto: lo que una restauración debe devolver intacto.
# Cuántos respaldos se conservan en el disco del servidor (los de S3 los rota S3 con sus reglas).
RESPALDOS_LOCALES = 3

TABLAS_CONTADAS = ("paquetes", "personas", "usuarios", "cobros", "paquete_fotos", "apartamentos")


class MotivoRespaldo(str, enum.Enum):
    DIARIO = "diario"
    ANTES_DE_DEPLOY = "antes_de_deploy"
    A_PEDIDO = "a_pedido"
    ANTES_DE_RESTAURAR = "antes_de_restaurar"


class RespaldoFallido(Exception):
    """Un respaldo no se completó. `paso` dice cuál falló (lo usa el aviso por correo)."""

    def __init__(self, paso: str, detalle: str) -> None:
        super().__init__(f"{paso}: {detalle}")
        self.paso = paso
        self.detalle = detalle


class RespaldoEnCurso(Exception):
    """Ya hay otra operación de respaldo/restauración corriendo sobre la misma carpeta."""


@dataclass(frozen=True)
class OrigenRespaldo:
    """Qué se respalda: la base y los datos de la instalación que la identifican."""

    database_url: str
    dominio: str
    commit: str


@dataclass(frozen=True)
class HuellaArchivo:
    tamano: int
    sha256: str


@dataclass(frozen=True)
class Manifiesto:
    fecha_hora_colombia: str
    dominio: str
    conjunto: str
    commit: str
    motivo: MotivoRespaldo
    version_bd: str
    conteos: dict[str, int] = field(default_factory=dict)
    archivos: dict[str, HuellaArchivo] = field(default_factory=dict)


@dataclass(frozen=True)
class Respaldo:
    carpeta: Path


def leer_commit(checkout: Path) -> str:
    """El commit en el que está el checkout, leyendo `.git` a mano: la imagen de la app no trae `git`. Soporta
    `HEAD` separado, ref suelta y `packed-refs` (lo que deja `git gc`)."""
    git = Path(checkout) / ".git"
    head = (git / "HEAD").read_text().strip()
    if not head.startswith("ref: "):
        return head
    ref = head.removeprefix("ref: ")
    suelta = git / ref
    if suelta.is_file():
        return suelta.read_text().strip()
    empaquetadas = git / "packed-refs"
    if empaquetadas.is_file():
        for linea in empaquetadas.read_text().splitlines():
            if linea.endswith(" " + ref):
                return linea.split(" ", 1)[0]
    raise ValueError(f"No se encontró el commit de {ref} en {git}")


def _pg_url(database_url: str) -> str:
    # `pg_dump` entiende URLs `postgresql://`, no el dialecto de SQLAlchemy.
    return database_url.replace("postgresql+psycopg2://", "postgresql://", 1)


def _huella(ruta: Path) -> HuellaArchivo:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return HuellaArchivo(tamano=ruta.stat().st_size, sha256=h.hexdigest())


def _leer_estado_bd(database_url: str) -> tuple[str, str, dict[str, int]]:
    """(versión Alembic, nombre del conjunto, conteos de las tablas principales)."""
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            conjunto = obtener_nombre_conjunto(session)
            conteos = {t: session.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in TABLAS_CONTADAS}
    finally:
        engine.dispose()
    return version, conjunto, conteos


@contextlib.contextmanager
def operacion_exclusiva(carpeta_respaldos: Path):
    """Candado de la carpeta de respaldos: una sola operación a la vez (el diario, uno "a pedido", una restauración).
    Si ya está tomado, `RespaldoEnCurso` en vez de esperar. Es un `flock` sobre un archivo oculto, así que se libera
    solo si el proceso muere."""
    carpeta = Path(carpeta_respaldos)
    carpeta.mkdir(parents=True, exist_ok=True)
    with open(carpeta / ".candado", "w") as archivo:
        try:
            fcntl.flock(archivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RespaldoEnCurso("Ya hay un respaldo o una restauración en curso.") from exc
        try:
            yield
        finally:
            fcntl.flock(archivo, fcntl.LOCK_UN)


def crear_respaldo(
    origen: OrigenRespaldo,
    carpeta_respaldos: Path,
    motivo: MotivoRespaldo,
    ahora: datetime | None = None,
) -> Respaldo:
    with operacion_exclusiva(carpeta_respaldos):
        respaldo = _crear_respaldo(origen, Path(carpeta_respaldos), motivo, ahora or datetime.now(timezone.utc))
        _rotar_locales(Path(carpeta_respaldos))
        return respaldo


def listar_respaldos(carpeta_respaldos: Path) -> list[Path]:
    """Las carpetas de respaldo completas, de la más vieja a la más nueva (el nombre empieza con la fecha)."""
    carpeta = Path(carpeta_respaldos)
    if not carpeta.is_dir():
        return []
    return sorted(p for p in carpeta.iterdir() if p.is_dir() and not p.name.startswith(".") and (p / ARCHIVO_MANIFIESTO).is_file())


def _rotar_locales(carpeta_respaldos: Path) -> None:
    for vieja in listar_respaldos(carpeta_respaldos)[:-RESPALDOS_LOCALES]:
        shutil.rmtree(vieja)


def _crear_respaldo(origen: OrigenRespaldo, carpeta_respaldos: Path, motivo: MotivoRespaldo, ahora: datetime) -> Respaldo:
    local = ahora.astimezone(ZONA_HORARIA_APP)
    nombre = f"{local:%Y-%m-%d_%H%M%S}_{motivo.value}"
    destino = Path(carpeta_respaldos) / nombre
    # Se arma en una carpeta oculta y solo se renombra al nombre final cuando está completa: una corrida que falla
    # a mitad de camino nunca deja algo con apariencia de respaldo bueno.
    en_curso = Path(carpeta_respaldos) / f".en_curso_{nombre}"
    en_curso.mkdir(parents=True)
    try:
        _armar(origen, en_curso, motivo, local)
        en_curso.rename(destino)
    except RespaldoFallido:
        shutil.rmtree(en_curso, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(en_curso, ignore_errors=True)
        raise RespaldoFallido("respaldo", str(exc)) from exc
    return Respaldo(carpeta=destino)


def _armar(origen: OrigenRespaldo, carpeta: Path, motivo: MotivoRespaldo, local: datetime) -> None:
    try:
        version, conjunto, conteos = _leer_estado_bd(origen.database_url)
    except Exception as exc:
        raise RespaldoFallido("conexión a la base de datos", str(exc)) from exc
    volcado = subprocess.run(
        ["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "--file", str(carpeta / ARCHIVO_BD), _pg_url(origen.database_url)],
        capture_output=True,
        text=True,
    )
    if volcado.returncode != 0:
        raise RespaldoFallido("volcado de la base de datos (pg_dump)", volcado.stderr.strip())

    manifiesto = configparser.ConfigParser()
    manifiesto["respaldo"] = {
        "fecha_hora_colombia": f"{local:%Y-%m-%d %H:%M:%S}",
        "dominio": origen.dominio,
        "conjunto": conjunto,
        "commit": origen.commit,
        "motivo": motivo.value,
        "version_bd": version,
    }
    manifiesto["conteos"] = {t: str(n) for t, n in conteos.items()}
    # Una sección por archivo: `[archivo base_datos.dump]` con su tamaño y su sha256.
    for nombre in (ARCHIVO_BD,):
        huella = _huella(carpeta / nombre)
        manifiesto[f"archivo {nombre}"] = {"tamano": str(huella.tamano), "sha256": huella.sha256}
    with open(carpeta / ARCHIVO_MANIFIESTO, "w", encoding="utf-8") as f:
        manifiesto.write(f)


def leer_manifiesto(carpeta: Path) -> Manifiesto:
    cp = configparser.ConfigParser()
    cp.read(Path(carpeta) / ARCHIVO_MANIFIESTO, encoding="utf-8")
    r = cp["respaldo"]
    return Manifiesto(
        fecha_hora_colombia=r["fecha_hora_colombia"],
        dominio=r["dominio"],
        conjunto=r["conjunto"],
        commit=r["commit"],
        motivo=MotivoRespaldo(r["motivo"]),
        version_bd=r["version_bd"],
        conteos={t: int(n) for t, n in cp["conteos"].items()},
        archivos={
            seccion.removeprefix("archivo "): HuellaArchivo(tamano=int(cp[seccion]["tamano"]), sha256=cp[seccion]["sha256"])
            for seccion in cp.sections()
            if seccion.startswith("archivo ")
        },
    )
