# -*- coding: utf-8 -*-
"""
Respaldos, ticket 03 (`.scratch/respaldos-y-restauracion`): cada respaldo lleva la copia exacta del código
desplegado (`sistema.tar.gz`) y una plantilla del `.env` (`env.plantilla`) con los secretos ofuscados. Seam A: el
módulo de respaldos contra un Postgres real y un checkout git real en un directorio temporal.
"""

import tarfile
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text

import _harness as H
from app.domain.respaldo_service import Instalacion, MotivoRespaldo, crear_respaldo, leer_manifiesto

_AHORA = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def bd(postgres_server_url):
    server = postgres_server_url.rsplit("/", 1)[0]
    nombre = f"sis_{uuid.uuid4().hex[:10]}"
    admin = create_engine(server + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{nombre}"'))
    url = server + "/" + nombre
    H.run_alembic(url, "upgrade", "head")
    yield url
    with admin.connect() as conn:
        conn.execute(
            text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n AND pid <> pg_backend_pid()"),
            {"n": nombre},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{nombre}"'))
    admin.dispose()


def _git(repo, *args):
    import subprocess

    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def checkout(tmp_path):
    """Un checkout como el del servidor: código versionado, más un `.env` y copias viejas de él fuera de git."""
    repo = tmp_path / "checkout"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("print('hola')\n")
    (repo / "docker-compose.yml").write_text("services:\n  app:\n    environment:\n      SECRET_KEY: ${SECRET_KEY}\n")
    (repo / ".gitignore").write_text(".env*\nscripts/local.sh\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "uno")
    (repo / ".env").write_text(
        "# comentario del servidor\n"
        "SECRET_KEY=clave-super-secreta-de-produccion\n"
        "PG_PASSWORD=corta1\n"
        "AWS_REGION=us-east-1\n"
        "SMTP_PORT=587\n"
        "AWS_S3_SECRET_ACCESS_KEY='wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'\n"
    )
    (repo / ".env.bak.20260731").write_text("SECRET_KEY=otra-clave-vieja\n")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "local.sh").write_text("echo local\n")
    return repo


def _instalacion(url, repo):
    return Instalacion(database_url=url, dominio="test.papyrus.com.co", commit=_git(repo, "rev-parse", "HEAD"), checkout=repo)


def test_la_copia_del_codigo_es_exactamente_el_commit_desplegado(bd, checkout, tmp_path):
    respaldo = crear_respaldo(_instalacion(bd, checkout), tmp_path / "respaldos", MotivoRespaldo.DIARIO, ahora=_AHORA)

    with tarfile.open(respaldo.carpeta / "sistema.tar.gz") as tar:
        archivos = sorted(m.name for m in tar.getmembers() if m.isfile())
    assert archivos == [".gitignore", "docker-compose.yml", "src/app.py"]  # sin .env, sin copias de él, sin ignorados
    assert "sistema.tar.gz" in leer_manifiesto(respaldo.carpeta).archivos


def _plantilla(respaldo):
    return (respaldo.carpeta / "env.plantilla").read_text()


def _valor(plantilla, variable):
    return next(l.split("=", 1)[1] for l in plantilla.splitlines() if l.startswith(variable + "="))


def test_la_plantilla_del_env_muestra_lo_no_secreto_y_ofusca_los_secretos(bd, checkout, tmp_path):
    respaldo = crear_respaldo(_instalacion(bd, checkout), tmp_path / "respaldos", MotivoRespaldo.DIARIO, ahora=_AHORA)
    plantilla = _plantilla(respaldo)

    assert _valor(plantilla, "AWS_REGION") == "us-east-1"
    assert _valor(plantilla, "SMTP_PORT") == "587"
    assert _valor(plantilla, "SECRET_KEY") == "cla****ion"  # 12 o más: 3 primeros + 3 últimos
    assert _valor(plantilla, "AWS_S3_SECRET_ACCESS_KEY") == "wJa****KEY"  # sin las comillas del .env
    assert _valor(plantilla, "PG_PASSWORD") == "****"  # menos de 12: nada que revele media contraseña
    assert "clave-super-secreta-de-produccion" not in plantilla and "corta1" not in plantilla
    assert "env.plantilla" in leer_manifiesto(respaldo.carpeta).archivos


def test_una_variable_nueva_aparece_sola_y_si_parece_secreta_se_ofusca(bd, checkout, tmp_path):
    with open(checkout / ".env", "a") as f:
        f.write("PROVEEDOR_NUEVO_API_TOKEN=abcdefghijklmnop\nPROVEEDOR_NUEVO_COLOR=verde\n")

    respaldo = crear_respaldo(_instalacion(bd, checkout), tmp_path / "respaldos", MotivoRespaldo.DIARIO, ahora=_AHORA)
    plantilla = _plantilla(respaldo)

    assert _valor(plantilla, "PROVEEDOR_NUEVO_API_TOKEN") == "abc****nop"
    assert _valor(plantilla, "PROVEEDOR_NUEVO_COLOR") == "verde"
    # Cada variable conocida lleva su comentario justo arriba.
    lineas = plantilla.splitlines()
    assert lineas[lineas.index("AWS_REGION=us-east-1") - 1].startswith("# ")


def test_el_manifiesto_lista_las_variables_que_exige_el_despliegue(bd, checkout, tmp_path):
    respaldo = crear_respaldo(_instalacion(bd, checkout), tmp_path / "respaldos", MotivoRespaldo.DIARIO, ahora=_AHORA)

    assert leer_manifiesto(respaldo.carpeta).variables_requeridas == ["SECRET_KEY"]


def test_con_una_subcarpeta_la_copia_del_codigo_es_solo_esa_carpeta_del_commit(bd, tmp_path):
    # En local el checkout es el monorepo y el código desplegable vive en `CODE/` (lo mismo que el repo de deploy).
    repo = tmp_path / "monorepo"
    (repo / "CODE" / "src").mkdir(parents=True)
    (repo / "CODE" / "src" / "app.py").write_text("x\n")
    (repo / "CODE" / "docker-compose.yml").write_text("x: ${SECRET_KEY}\n")
    (repo / "notas.md").write_text("fuera del código desplegable\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "uno")
    instalacion = Instalacion(
        database_url=bd, dominio="localhost", commit=_git(repo, "rev-parse", "HEAD"), checkout=repo, subcarpeta="CODE"
    )

    respaldo = crear_respaldo(instalacion, tmp_path / "respaldos", MotivoRespaldo.A_PEDIDO, ahora=_AHORA)

    with tarfile.open(respaldo.carpeta / "sistema.tar.gz") as tar:
        assert sorted(m.name for m in tar.getmembers() if m.isfile()) == ["docker-compose.yml", "src/app.py"]
    assert leer_manifiesto(respaldo.carpeta).variables_requeridas == ["SECRET_KEY"]
