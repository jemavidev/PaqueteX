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
import json
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from .configuracion_conjunto_service import obtener_nombre_conjunto
from .email_sender import EmailSender
from .plantilla_env import generar_plantilla_env
from .zona_horaria import ZONA_HORARIA_APP

ARCHIVO_BD = "base_datos.dump"
ARCHIVO_SISTEMA = "sistema.tar.gz"
ARCHIVO_PLANTILLA_ENV = "env.plantilla"
ARCHIVO_MANIFIESTO = "manifiesto.txt"

# Cuántos respaldos se conservan en el disco del servidor (los de S3 los rota S3 con sus reglas), más el último diario.
RESPALDOS_LOCALES = 3

# Las tablas cuyo conteo guarda el manifiesto: lo que una restauración debe devolver intacto.
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


class RestauracionRechazada(Exception):
    """La restauración no se hizo porque una protección la frenó; la base quedó intacta."""


class MigracionFallida(Exception):
    """La base SÍ quedó restaurada, pero `alembic upgrade head` falló al ponerla al día con el código instalado."""


@dataclass(frozen=True)
class Instalacion:
    """Una instalación de PaqueteX (un dominio): su base y lo que la identifica. Es el origen de un respaldo y el
    destino de una restauración."""

    database_url: str
    dominio: str
    commit: str
    # El checkout desplegado (de ahí salen la copia del código y la plantilla del `.env`). Sin él, el respaldo lleva
    # solo la base.
    checkout: Path | None = None
    # Subcarpeta del checkout donde vive el código desplegable (en local, el monorepo tiene el código en `CODE/`, que
    # es lo mismo que la raíz del repo de deploy). Vacío = la raíz del checkout.
    subcarpeta: str = ""

    @property
    def carpeta_codigo(self) -> Path | None:
        if self.checkout is None:
            return None
        return Path(self.checkout) / self.subcarpeta if self.subcarpeta else Path(self.checkout)


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
    # Las variables que el `docker-compose.yml` desplegado toma del `.env` (`${VAR}`): lo mínimo a configurar al
    # restaurar en otro servidor.
    variables_requeridas: list[str] = field(default_factory=list)
    solicitado_por: str | None = None  # quién pidió un respaldo "a pedido"


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


def _copiar_codigo(checkout: Path, commit: str, destino: Path, subcarpeta: str = "") -> None:
    """`git archive` del commit desplegado: exactamente lo versionado. Nunca el `.env`, sus copias viejas
    (`.env.bak...`) ni otros archivos sueltos o ignorados que haya en el checkout del servidor.
    `safe.directory`: el checkout es de otro usuario del host que el del contenedor."""
    archivo = subprocess.run(
        [
            "git", "-c", "safe.directory=*", "-C", str(checkout), "archive", "--format=tar.gz", "--output", str(destino),
            f"{commit}:{subcarpeta}" if subcarpeta else commit,
        ],
        capture_output=True,
        text=True,
    )
    if archivo.returncode != 0:
        raise RespaldoFallido("copia del código (git archive)", archivo.stderr.strip())


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
    origen: Instalacion,
    carpeta_respaldos: Path,
    motivo: MotivoRespaldo,
    ahora: datetime | None = None,
    solicitado_por: str | None = None,
) -> Respaldo:
    with operacion_exclusiva(carpeta_respaldos):
        respaldo = _crear_respaldo(origen, Path(carpeta_respaldos), motivo, ahora or datetime.now(timezone.utc), solicitado_por)
        _rotar_locales(Path(carpeta_respaldos))
        return respaldo


def listar_respaldos(carpeta_respaldos: Path) -> list[Path]:
    """Las carpetas de respaldo completas, de la más vieja a la más nueva (el nombre empieza con la fecha)."""
    carpeta = Path(carpeta_respaldos)
    if not carpeta.is_dir():
        return []
    return sorted(p for p in carpeta.iterdir() if p.is_dir() and not p.name.startswith(".") and (p / ARCHIVO_MANIFIESTO).is_file())


def _rotar_locales(carpeta_respaldos: Path) -> None:
    """Quedan los últimos `RESPALDOS_LOCALES`, más el último diario si no está entre ellos: los puntuales (varios
    deploys en un día) no deben dejar el disco sin un diario para la prueba del domingo."""
    respaldos = listar_respaldos(carpeta_respaldos)
    conservar = set(respaldos[-RESPALDOS_LOCALES:])
    diarios = [r for r in respaldos if r.name.endswith("_" + MotivoRespaldo.DIARIO.value)]
    if diarios:
        conservar.add(diarios[-1])
    for vieja in respaldos:
        if vieja not in conservar:
            shutil.rmtree(vieja)


def _crear_respaldo(
    origen: Instalacion, carpeta_respaldos: Path, motivo: MotivoRespaldo, ahora: datetime, solicitado_por: str | None = None
) -> Respaldo:
    local = ahora.astimezone(ZONA_HORARIA_APP)
    nombre = f"{local:%Y-%m-%d_%H%M%S}_{motivo.value}"
    destino = Path(carpeta_respaldos) / nombre
    # Se arma en una carpeta oculta y solo se renombra al nombre final cuando está completa: una corrida que falla
    # a mitad de camino nunca deja algo con apariencia de respaldo bueno.
    en_curso = Path(carpeta_respaldos) / f".en_curso_{nombre}"
    en_curso.mkdir(parents=True)
    try:
        _armar(origen, en_curso, motivo, local, solicitado_por)
        en_curso.rename(destino)
    except RespaldoFallido:
        shutil.rmtree(en_curso, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(en_curso, ignore_errors=True)
        raise RespaldoFallido("respaldo", str(exc)) from exc
    return Respaldo(carpeta=destino)


def _armar(origen: Instalacion, carpeta: Path, motivo: MotivoRespaldo, local: datetime, solicitado_por: str | None) -> None:
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
    archivos = [ARCHIVO_BD]
    if origen.checkout is not None:
        _copiar_codigo(Path(origen.checkout), origen.commit, carpeta / ARCHIVO_SISTEMA, origen.subcarpeta)
        archivos.append(ARCHIVO_SISTEMA)
        env = origen.carpeta_codigo / ".env"
        texto_env = env.read_text(encoding="utf-8") if env.is_file() else ""
        (carpeta / ARCHIVO_PLANTILLA_ENV).write_text(
            generar_plantilla_env(texto_env, origen.dominio, f"{local:%Y-%m-%d %H:%M}"), encoding="utf-8"
        )
        archivos.append(ARCHIVO_PLANTILLA_ENV)

    manifiesto = configparser.ConfigParser()
    manifiesto["respaldo"] = {
        "fecha_hora_colombia": f"{local:%Y-%m-%d %H:%M:%S}",
        "dominio": origen.dominio,
        "conjunto": conjunto,
        "commit": origen.commit,
        "motivo": motivo.value,
        "version_bd": version,
    }
    if solicitado_por:
        manifiesto["respaldo"]["solicitado_por"] = solicitado_por
    manifiesto["conteos"] = {t: str(n) for t, n in conteos.items()}
    compose = origen.carpeta_codigo / "docker-compose.yml" if origen.carpeta_codigo is not None else None
    if compose is not None and compose.is_file():
        requeridas = sorted(set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", compose.read_text(encoding="utf-8"))))
        manifiesto["variables_requeridas"] = {"nombres": ", ".join(requeridas)}
    # Una sección por archivo: `[archivo base_datos.dump]` con su tamaño y su sha256.
    for nombre in archivos:
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
        solicitado_por=r.get("solicitado_por"),
        variables_requeridas=[
            v.strip() for v in cp.get("variables_requeridas", "nombres", fallback="").split(",") if v.strip()
        ],
    )


def verificar_respaldo(carpeta: Path) -> Manifiesto:
    """El manifiesto del respaldo, tras comprobar que cada archivo existe y coincide con su tamaño y su huella.
    `RestauracionRechazada` nombrando el archivo que falla (ej. una descarga incompleta)."""
    carpeta = Path(carpeta)
    if not (carpeta / ARCHIVO_MANIFIESTO).is_file():
        raise RestauracionRechazada(f"{carpeta} no es un respaldo: falta {ARCHIVO_MANIFIESTO}.")
    try:
        manifiesto = leer_manifiesto(carpeta)
    except (KeyError, ValueError, configparser.Error) as exc:
        raise RestauracionRechazada(f"El manifiesto de {carpeta.name} está incompleto o dañado (falta {exc}).") from exc
    for nombre, esperada in manifiesto.archivos.items():
        ruta = carpeta / nombre
        if not ruta.is_file():
            raise RestauracionRechazada(f"Falta {nombre} en el respaldo.")
        if _huella(ruta) != esperada:
            raise RestauracionRechazada(f"{nombre} no coincide con la huella del manifiesto (archivo dañado o incompleto).")
    return manifiesto


def comprobar_restauracion(carpeta: Path, destino: Instalacion, codigo: Path, permitir_otro_destino: bool = False) -> Manifiesto:
    """Las protecciones que no tocan nada -- huellas, mismo sistema (salvo `permitir_otro_destino`) y versión compatible
    con el código instalado en `codigo` --, para correrlas con el sistema todavía encendido, antes de pedir la
    confirmación y de detener la app. `RestauracionRechazada` si alguna frena."""
    manifiesto = verificar_respaldo(carpeta)
    if manifiesto.dominio != destino.dominio and not permitir_otro_destino:
        raise RestauracionRechazada(
            f"El respaldo es de {manifiesto.dominio} y esta instalación es {destino.dominio}. Si de verdad quieres "
            "restaurarlo aquí (ej. un servidor nuevo), pídelo explícitamente con --otro-destino."
        )
    script = _scripts_alembic(codigo)
    if manifiesto.version_bd != script.get_current_head():
        try:
            script.get_revision(manifiesto.version_bd)
        except Exception:
            raise RestauracionRechazada(
                f"El respaldo es de una versión de la base ({manifiesto.version_bd}) más nueva que el código instalado "
                "aquí. Primero despliega el código de ese respaldo (sistema.tar.gz) y después restaura."
            )
    return manifiesto


def restaurar(
    carpeta: Path,
    destino: Instalacion,
    confirmacion: str,
    carpeta_respaldos: Path,
    codigo: Path,
    permitir_otro_destino: bool = False,
) -> "Respaldo":
    """Reemplaza la base de `destino` por la del respaldo de `carpeta`, con sus protecciones (ticket 02): confirmación
    escribiendo el dominio, huellas, mismo sistema (salvo `permitir_otro_destino`), versión compatible con el código
    instalado en `codigo` (la carpeta con `alembic.ini`) y un respaldo de lo actual antes de tocar nada. Si el
    respaldo es de una versión anterior, al final aplica las migraciones pendientes. Cualquier protección que frene
    lanza `RestauracionRechazada` con la base intacta; si lo que falla son esas migraciones, `MigracionFallida` (la
    base ya quedó restaurada). Devuelve la copia de lo que había antes (para subirla a S3)."""
    if confirmacion.strip().lower() != destino.dominio.lower():
        raise RestauracionRechazada(f"No se confirmó: hay que escribir el dominio exacto ({destino.dominio}).")
    comprobar_restauracion(carpeta, destino, codigo, permitir_otro_destino)
    with operacion_exclusiva(carpeta_respaldos):
        # Sin rotar: la copia de lo actual no debe empujar fuera del disco al respaldo que se está restaurando.
        ahora = datetime.now(timezone.utc)
        previo = _crear_respaldo(destino, Path(carpeta_respaldos), MotivoRespaldo.ANTES_DE_RESTAURAR, ahora)
        _registrar(
            Path(carpeta_respaldos),
            {"fecha_utc": ahora.isoformat(timespec="seconds"), "motivo": MotivoRespaldo.ANTES_DE_RESTAURAR.value,
             "ok": True, "respaldo": previo.carpeta.name,
             "tamano": sum(a.stat().st_size for a in previo.carpeta.iterdir()), "subido_a": [], "error": None},
        )
        try:
            _reemplazar_base(destino.database_url, Path(carpeta) / ARCHIVO_BD)
        except Exception as exc:
            # A mitad de camino la base puede quedar vacía: se vuelve a lo que había justo antes.
            _reemplazar_base(destino.database_url, previo.carpeta / ARCHIVO_BD)
            raise RestauracionRechazada(
                f"La restauración falló y se devolvió la base a como estaba ({previo.carpeta.name}): {exc}"
            ) from exc
    try:
        _migrar_a_la_version_del_codigo(destino.database_url, codigo)
    except subprocess.CalledProcessError as exc:
        raise MigracionFallida((exc.stderr or str(exc)).strip()) from exc
    return previo


def _scripts_alembic(codigo: Path):
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(Path(codigo) / "alembic.ini"))
    config.set_main_option("script_location", str(Path(codigo) / "alembic"))
    return ScriptDirectory.from_config(config)


def _migrar_a_la_version_del_codigo(database_url: str, codigo: Path) -> None:
    # Mismo camino que el arranque de la app (`alembic upgrade head`); `-x db_url` para no depender del entorno.
    subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"db_url={database_url}", "upgrade", "head"],
        cwd=str(codigo),
        check=True,
        capture_output=True,
        text=True,
    )


def _reemplazar_base(database_url: str, volcado: Path) -> None:
    # Se vacía el esquema entero (no `pg_restore --clean`): así no sobrevive ninguna tabla que exista hoy y no en el
    # respaldo.
    engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()
    subprocess.run(
        ["pg_restore", "--no-owner", "--no-privileges", "--exit-on-error", "--dbname", _pg_url(database_url), str(volcado)],
        check=True,
        capture_output=True,
        text=True,
    )


class DestinoRespaldos(Protocol):
    """Adónde suben los respaldos (S3 en el servidor; uno falso en las pruebas). La conservación la deciden las reglas
    de S3 por carpeta (`<dominio>/diario/`, `mensual/`, `anual/`, `puntual/`; ver `infra/respaldos/`)."""

    def subir(self, clave: str, ruta: Path) -> None: ...


def carpetas_destino(manifiesto: Manifiesto) -> list[str]:
    """En qué carpetas del bucket va un respaldo: los diarios en `diario/`, además en `mensual/` el del día 1 y en
    `anual/` el del 1 de enero (en hora de Colombia); todo lo demás en `puntual/`."""
    if manifiesto.motivo != MotivoRespaldo.DIARIO:
        return ["puntual"]
    fecha = datetime.strptime(manifiesto.fecha_hora_colombia, "%Y-%m-%d %H:%M:%S")
    tipos = ["diario"]
    if fecha.day == 1:
        tipos.append("mensual")
        if fecha.month == 1:
            tipos.append("anual")
    return tipos


def subir_respaldo(respaldo: Respaldo, destino: DestinoRespaldos) -> list[str]:
    """Sube todos los archivos del respaldo a `<dominio>/<tipo>/<nombre del respaldo>/`. Devuelve las carpetas. Si
    falla, `RespaldoFallido` y el respaldo local queda intacto."""
    manifiesto = leer_manifiesto(respaldo.carpeta)
    tipos = carpetas_destino(manifiesto)
    try:
        for tipo in tipos:
            for archivo in sorted(respaldo.carpeta.iterdir()):
                clave = f"{manifiesto.dominio}/{tipo}/{respaldo.carpeta.name}/{archivo.name}"
                destino.subir(clave, archivo)
    except Exception as exc:
        raise RespaldoFallido("subida a S3", str(exc)) from exc
    return tipos


class S3DestinoRespaldos:
    """El bucket de respaldos. La llave del servidor solo puede SUBIR bajo la carpeta de su dominio (ver
    `infra/respaldos/`): ni leer, ni listar, ni borrar, ni etiquetar."""

    def __init__(self, bucket: str, region: str, access_key_id: str, secret_access_key: str) -> None:
        import boto3

        self._bucket = bucket
        self._s3 = boto3.client(
            "s3", region_name=region, aws_access_key_id=access_key_id, aws_secret_access_key=secret_access_key
        )

    def subir(self, clave: str, ruta: Path) -> None:
        self._s3.upload_file(str(ruta), self._bucket, clave)


# --------------------------------------------------------------------------------------------------------------------
# Corrida completa (ticket 05): respaldar + subir + avisar + registrar
# --------------------------------------------------------------------------------------------------------------------
UMBRAL_DISCO = 0.80
ARCHIVO_HISTORIAL = ".historial.jsonl"
# Las entradas del historial son corridas de respaldo (su `MotivoRespaldo`) o la prueba del domingo.
MOTIVO_PRUEBA_RESTAURACION = "prueba_restauracion"


def uso_disco(carpeta: Path) -> float:
    """Fracción usada (0 a 1) del disco donde vive `carpeta`."""
    uso = shutil.disk_usage(carpeta)
    return uso.used / uso.total


@dataclass
class Avisos:
    """A quién y cómo avisar por correo. `uso_disco` se inyecta para poder probar el umbral."""

    sender: EmailSender
    destinatarios: list[str]
    uso_disco: Callable[[Path], float] = uso_disco

    def enviar(self, asunto: str, cuerpo: str) -> None:
        for destino in self.destinatarios:
            try:
                self.sender.enviar(destino, asunto, cuerpo)
            except Exception:
                pass  # un correo que no sale no debe tapar el resultado del respaldo (queda en el log del host)


def _registrar(carpeta_respaldos: Path, corrida: dict) -> None:
    with open(Path(carpeta_respaldos) / ARCHIVO_HISTORIAL, "a", encoding="utf-8") as f:
        f.write(json.dumps(corrida, ensure_ascii=False) + "\n")


def leer_historial(carpeta_respaldos: Path) -> list[dict]:
    """Una entrada por corrida (la más vieja primero): para el resumen del lunes y la pantalla de respaldos."""
    ruta = Path(carpeta_respaldos) / ARCHIVO_HISTORIAL
    if not ruta.is_file():
        return []
    return [json.loads(linea) for linea in ruta.read_text(encoding="utf-8").splitlines() if linea.strip()]


def ejecutar_respaldo(
    instalacion: Instalacion,
    carpeta_respaldos: Path,
    motivo: MotivoRespaldo,
    destino: DestinoRespaldos | None,
    avisos: Avisos,
    ahora: datetime | None = None,
    solicitado_por: str | None = None,
) -> Respaldo:
    """Una corrida completa: respaldo local, subida a S3 (si hay destino), correo inmediato si cualquier paso falla,
    aviso si el disco pasa del 80 % y una línea en el historial. Relanza `RespaldoFallido` tras avisar."""
    ahora = ahora or datetime.now(timezone.utc)
    carpeta_respaldos = Path(carpeta_respaldos)
    carpeta_respaldos.mkdir(parents=True, exist_ok=True)
    corrida = {
        "fecha_utc": ahora.isoformat(timespec="seconds"),
        "motivo": motivo.value,
        "ok": False,
        "respaldo": None,
        "tamano": None,
        "subido_a": [],
        "error": None,
    }
    try:
        respaldo = crear_respaldo(instalacion, carpeta_respaldos, motivo, ahora=ahora, solicitado_por=solicitado_por)
        corrida["respaldo"] = respaldo.carpeta.name
        corrida["tamano"] = sum(a.stat().st_size for a in respaldo.carpeta.iterdir())
        if destino is not None:
            corrida["subido_a"] = subir_respaldo(respaldo, destino)
        corrida["ok"] = True
    except RespaldoEnCurso as exc:
        # Ej. el diario de las 3:00 mientras corría uno "a pedido": este no se hizo, y eso también se avisa.
        corrida["error"] = str(exc)
        _registrar(carpeta_respaldos, corrida)
        avisos.enviar(
            f"[Respaldos] No se hizo el respaldo de {instalacion.dominio}",
            f"El respaldo ({motivo.value}) de {instalacion.dominio} no corrió porque ya había otro respaldo o una "
            "restauración en curso. Si esto se repite, revisar el servidor.",
        )
        raise
    except RespaldoFallido as exc:
        corrida["error"] = str(exc)
        _registrar(carpeta_respaldos, corrida)
        local = ahora.astimezone(ZONA_HORARIA_APP)
        avisos.enviar(
            f"[Respaldos] FALLÓ el respaldo de {instalacion.dominio}",
            f"El respaldo ({motivo.value}) de {instalacion.dominio} del {local:%Y-%m-%d %H:%M} (hora Colombia) no se "
            f"completó.\n\nPaso que falló: {exc.paso}\nDetalle: {exc.detalle}\n\n"
            + (
                f"El respaldo local SÍ quedó en el disco del servidor ({corrida['respaldo']}); solo falló la subida.\n"
                if corrida["respaldo"]
                else "No quedó ningún respaldo nuevo de esta corrida.\n"
            )
            + "Revisar en el servidor: el log de respaldos y `docker compose logs app`.",
        )
        raise
    finally:
        _avisar_si_el_disco_se_llena(instalacion, carpeta_respaldos, avisos)
    _registrar(carpeta_respaldos, corrida)
    return respaldo


def _avisar_si_el_disco_se_llena(instalacion: Instalacion, carpeta_respaldos: Path, avisos: Avisos) -> None:
    try:
        uso = avisos.uso_disco(carpeta_respaldos)
    except OSError:
        return
    if uso > UMBRAL_DISCO:
        avisos.enviar(
            f"[Respaldos] Disco casi lleno en {instalacion.dominio}",
            f"El disco del servidor de {instalacion.dominio} está al {uso * 100:.0f} % (el aviso salta al pasar del "
            f"{UMBRAL_DISCO * 100:.0f} %).\n\nSin espacio, los respaldos y la copia de las fotos van a empezar a "
            "fallar. Liberar espacio (ej. `docker system prune`, la caché de construcción) o ampliar el disco.",
        )


# --------------------------------------------------------------------------------------------------------------------
# Prueba de restauración del domingo y resumen del lunes (ticket 06)
# --------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ResultadoPrueba:
    ok: bool
    detalle: str
    respaldo: str | None = None
    conteos: dict[str, int] = field(default_factory=dict)


def _ultimo_para_probar(carpeta_respaldos: Path) -> Path | None:
    respaldos = listar_respaldos(carpeta_respaldos)
    diarios = [r for r in respaldos if r.name.endswith("_" + MotivoRespaldo.DIARIO.value)]
    return (diarios or respaldos or [None])[-1]


def probar_restauracion(carpeta_respaldos: Path, url_bd_temporal: str, avisos: Avisos, ahora: datetime | None = None) -> ResultadoPrueba:
    """Restaura el último respaldo diario en `url_bd_temporal` -- una base VACÍA y aparte, nunca la de la instalación
    (en el servidor, un Postgres desechable que el script del host levanta y borra) -- y comprueba que termine sin
    errores y que versión y conteos coincidan con el manifiesto. Queda en el historial; si falla, correo inmediato."""
    ahora = ahora or datetime.now(timezone.utc)
    carpeta = _ultimo_para_probar(carpeta_respaldos)
    try:
        if carpeta is None:
            raise RestauracionRechazada("No hay ningún respaldo en el disco para probar.")
        manifiesto = verificar_respaldo(carpeta)
        restaurado = subprocess.run(
            ["pg_restore", "--no-owner", "--no-privileges", "--exit-on-error", "--dbname", _pg_url(url_bd_temporal), str(carpeta / ARCHIVO_BD)],
            capture_output=True,
            text=True,
        )
        if restaurado.returncode != 0:
            raise RestauracionRechazada(f"pg_restore falló: {restaurado.stderr.strip()}")
        version, _, conteos = _leer_estado_bd(url_bd_temporal)
        diferencias = [f"{t}: {conteos.get(t)} restaurados vs {n} en el manifiesto" for t, n in manifiesto.conteos.items() if conteos.get(t) != n]
        if version != manifiesto.version_bd:
            diferencias.insert(0, f"versión: {version} restaurada vs {manifiesto.version_bd} en el manifiesto")
        if diferencias:
            raise RestauracionRechazada("No cuadra con el manifiesto -- " + "; ".join(diferencias))
        resultado = ResultadoPrueba(ok=True, detalle="Restauración completa y cuadra con el manifiesto.", respaldo=carpeta.name, conteos=conteos)
    except Exception as exc:  # cualquier cosa (rechazo, base temporal caída...) es una prueba fallida, nunca silenciosa
        detalle = str(exc) if isinstance(exc, RestauracionRechazada) else f"{type(exc).__name__}: {exc}"
        resultado = ResultadoPrueba(ok=False, detalle=detalle, respaldo=carpeta.name if carpeta else None)
    _registrar(
        carpeta_respaldos,
        {
            "fecha_utc": ahora.isoformat(timespec="seconds"),
            "motivo": MOTIVO_PRUEBA_RESTAURACION,
            "ok": resultado.ok,
            "respaldo": resultado.respaldo,
            "conteos": resultado.conteos,
            "error": None if resultado.ok else resultado.detalle,
        },
    )
    if not resultado.ok:
        avisos.enviar(
            "[Respaldos] FALLÓ la prueba de restauración",
            f"La prueba semanal de restauración del respaldo {resultado.respaldo or '(ninguno)'} no pasó.\n\n"
            f"Detalle: {resultado.detalle}\n\nUn respaldo que no se puede restaurar no protege: revisarlo hoy.",
        )
    return resultado


def enviar_resumen_semanal(carpeta_respaldos: Path, dominio: str, avisos: Avisos, ahora: datetime | None = None) -> str:
    """El correo de los lunes, llegue o no a haber problemas: si un lunes NO llega, algo se rompió (incluso el propio
    mecanismo de avisos). Cubre los 7 días calendario anteriores, en hora de Colombia (enviado un lunes: de lunes a
    domingo). Devuelve el cuerpo enviado."""
    ahora = ahora or datetime.now(timezone.utc)
    hoy = ahora.astimezone(ZONA_HORARIA_APP).date()
    primer_dia, ultimo_dia = hoy - timedelta(days=7), hoy - timedelta(days=1)
    semana = [
        c
        for c in leer_historial(carpeta_respaldos)
        if primer_dia <= datetime.fromisoformat(c["fecha_utc"]).astimezone(ZONA_HORARIA_APP).date() <= ultimo_dia
    ]
    diarios = [c for c in semana if c["motivo"] == MotivoRespaldo.DIARIO.value]
    buenos = [c for c in diarios if c["ok"]]
    fallidos = [c for c in diarios if not c["ok"]]
    pruebas = [c for c in semana if c["motivo"] == MOTIVO_PRUEBA_RESTAURACION]
    puntuales = [c for c in semana if c["motivo"] in (MotivoRespaldo.ANTES_DE_DEPLOY.value, MotivoRespaldo.A_PEDIDO.value)]

    def dia(c):
        return datetime.fromisoformat(c["fecha_utc"]).astimezone(ZONA_HORARIA_APP).strftime("%Y-%m-%d")

    lineas = [f"Respaldos diarios: {len(buenos)} de 7 salieron bien."]
    for c in fallidos:
        lineas.append(f"  - {dia(c)}: FALLÓ -- {c['error']}")
    if buenos:
        ultimo = buenos[-1]
        subido = ", ".join(ultimo["subido_a"]) if ultimo["subido_a"] else "NO se subió a S3 (solo disco)"
        lineas.append(f"Último respaldo bueno: {ultimo['respaldo']} ({ultimo['tamano'] / 1_000_000:.1f} MB), subido a: {subido}.")
    if puntuales:
        lineas.append(f"Respaldos puntuales (antes de deploy / a pedido): {len(puntuales)}, {sum(1 for c in puntuales if c['ok'])} bien.")
    if pruebas:
        prueba = pruebas[-1]
        if prueba["ok"]:
            conteos = prueba["conteos"]
            lineas.append(
                f"Prueba de restauración (domingo): OK -- {conteos.get('paquetes', 0)} paquetes y "
                f"{conteos.get('personas', 0)} personas recuperados de {prueba['respaldo']}."
            )
        else:
            lineas.append(f"Prueba de restauración (domingo): FALLÓ -- {prueba['error']}")
    else:
        lineas.append("Prueba de restauración (domingo): NO corrió esta semana.")
    try:
        lineas.append(f"Espacio en disco del servidor: {avisos.uso_disco(Path(carpeta_respaldos)) * 100:.0f} % usado.")
    except OSError:
        pass
    todo_bien = len(buenos) == 7 and not fallidos and bool(pruebas) and pruebas[-1]["ok"]
    cuerpo = "\n".join(lineas) + "\n"
    avisos.enviar(f"[Respaldos] Resumen semanal de {dominio}: {'OK' if todo_bien else 'ATENCIÓN'}", cuerpo)
    return cuerpo


# --------------------------------------------------------------------------------------------------------------------
# Descarga (ticket 08)
# --------------------------------------------------------------------------------------------------------------------
class _Tubo:
    """Archivo de solo escritura que acumula lo escrito para entregarlo por partes (`zipfile` acepta destinos que no
    se pueden recorrer hacia atrás: escribe los tamaños después de cada archivo)."""

    def __init__(self) -> None:
        self._partes: list[bytes] = []
        self._escrito = 0

    def write(self, datos) -> int:
        self._partes.append(bytes(datos))
        self._escrito += len(datos)
        return len(datos)

    def tell(self) -> int:
        return self._escrito

    def flush(self) -> None:
        pass

    def vaciar(self) -> bytes:
        datos, self._partes = b"".join(self._partes), []
        return datos


def zip_por_partes(archivos: list[tuple[Path, str]], bloque: int = 1024 * 1024):
    """Un `.zip` de `archivos` ((ruta, nombre dentro del zip)) entregado por partes, sin armarlo entero en memoria ni
    en disco: una descarga de varios GB no agota la memoria del servidor. Sin comprimir: el volcado y las fotos ya
    vienen comprimidos."""
    tubo = _Tubo()
    with zipfile.ZipFile(tubo, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
        for ruta, nombre in archivos:
            with open(ruta, "rb") as origen, z.open(zipfile.ZipInfo.from_file(ruta, nombre), "w", force_zip64=True) as destino:
                for datos in iter(lambda: origen.read(bloque), b""):
                    destino.write(datos)
                    yield tubo.vaciar()
            yield tubo.vaciar()
    yield tubo.vaciar()


def zip_de_respaldo(carpeta: Path):
    carpeta = Path(carpeta)
    return zip_por_partes([(a, f"{carpeta.name}/{a.name}") for a in sorted(carpeta.iterdir()) if a.is_file()])
