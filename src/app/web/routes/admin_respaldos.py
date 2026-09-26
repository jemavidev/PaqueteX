# -*- coding: utf-8 -*-
"""
Pantalla "Respaldos" (`.scratch/respaldos-y-restauracion`, tickets 08-11) -- solo ADMIN.

Lista los respaldos del disco del servidor (las últimas 3 copias; los anteriores viven en S3 y se descargan con la
cuenta de AWS: la llave del servidor, a propósito, no puede leer el bucket), los entrega como `.zip` por partes y
muestra el comando exacto para restaurar cada uno por SSH. Restaurar NO se hace desde la web.
"""

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from app.domain.respaldo_service import (
    MotivoRespaldo,
    leer_historial,
    leer_manifiesto,
    listar_respaldos,
    zip_de_respaldo,
    zip_por_partes,
)
from app.domain.operacion_respaldo import EstadoOperacion, TipoOperacion
from app.domain.operacion_respaldo_service import (
    OperacionEnCurso,
    en_curso,
    iniciar_operacion,
    registrar_descarga_fotos,
    terminar_operacion,
    ultima_operacion,
)
from app.domain.respaldo_fotos_service import fotos_locales, fotos_nuevas
from app.domain.usuario import Usuario
from app.domain.zona_horaria import ZONA_HORARIA_APP

from ..db import get_db
from ..security import require_admin
from ..templating import templates

router = APIRouter()

MOTIVOS = {
    MotivoRespaldo.DIARIO.value: "Diario",
    MotivoRespaldo.ANTES_DE_DEPLOY.value: "Antes de deploy",
    MotivoRespaldo.A_PEDIDO.value: "A pedido",
    MotivoRespaldo.ANTES_DE_RESTAURAR.value: "Antes de restaurar",
}


def _carpeta() -> Path:
    return Path(os.environ.get("RESPALDO_DIR", "/respaldos"))


def _carpeta_fotos() -> Path:
    return Path(os.environ.get("RESPALDO_FOTOS_DIR", "/fotos-copia"))


def _estado_descargas(db: Session) -> dict:
    ultima = ultima_operacion(db, TipoOperacion.DESCARGA_FOTOS)
    marca = ultima.inicio if ultima is not None else None
    nuevas = fotos_nuevas(db, _carpeta_fotos(), marca, datetime.now(timezone.utc))
    return {
        "ultima": fecha_amigable(marca.astimezone(ZONA_HORARIA_APP)) if marca else None,
        "nuevas": len(nuevas.fotos),
        "nuevas_mb": nuevas.tamano / 1_000_000,
        "sin_copiar": nuevas.sin_copiar,
        "en_servidor": len(fotos_locales(_carpeta_fotos())),
    }


_COMANDOS = {
    TipoOperacion.RESPALDO: ["respaldar", "--motivo", "a_pedido"],
    TipoOperacion.COPIA_FOTOS: ["copiar-fotos"],
}


def _lanzar(operacion_id, tipo: TipoOperacion) -> None:
    """La operación en un proceso aparte (sesión propia): sigue aunque se cierre la pantalla o termine la petición.
    Es el mismo comando que usan el cron y el deploy; el proceso marca la operación al terminar. Su salida va al log
    de operaciones."""
    src = Path(__file__).resolve().parents[3]
    try:
        _carpeta().mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"la carpeta de respaldos ({_carpeta()}) no existe y no se puede crear en este ambiente -- configura "
            "RESPALDO_DIR (en el servidor la monta docker-compose; en local, scripts/paquetex_dev_up.sh)"
        ) from exc
    with open(_carpeta() / ".operaciones.log", "a") as log:  # el proceso hijo hereda su propia copia
        subprocess.Popen(
            [sys.executable, "-m", "app.respaldo_cli", *_COMANDOS[tipo], "--operacion", str(operacion_id)],
            cwd=src,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def get_lanzador_respaldo():
    return _lanzar


_DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
_MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def fecha_amigable(momento: datetime) -> dict:
    """Issue 411: fechas fáciles de leer para cualquier usuario -- {"dia": "sáb 26 sep", "hora": "7:00 a. m."}."""
    hora = f"{(momento.hour - 1) % 12 + 1}:{momento.minute:02d} {'a. m.' if momento.hour < 12 else 'p. m.'}"
    return {"dia": f"{_DIAS[momento.weekday()]} {momento.day} {_MESES[momento.month - 1]}", "hora": hora}


def _fila(carpeta: Path, subidas: dict[str, list[str]]) -> dict:
    try:
        m = leer_manifiesto(carpeta)
        fecha = fecha_amigable(datetime.strptime(m.fecha_hora_colombia[:16], "%Y-%m-%d %H:%M"))
        motivo, conteos = MOTIVOS.get(m.motivo.value, m.motivo.value), m.conteos
    except Exception:
        fecha, motivo, conteos = {"dia": carpeta.name, "hora": ""}, "(manifiesto ilegible)", {}
    return {
        "nombre": carpeta.name,
        "fecha": fecha,
        "motivo": motivo,
        "tamano_mb": sum(a.stat().st_size for a in carpeta.iterdir() if a.is_file()) / 1_000_000,
        "paquetes": conteos.get("paquetes"),
        "personas": conteos.get("personas"),
        # Carpetas de S3 a las que subió (del historial); vacío = quedó solo en el servidor o no hay registro.
        "en_s3": subidas.get(carpeta.name, []),
    }


def _pantalla(request: Request, db: Session, admin: Usuario, aviso: str | None = None):
    carpeta = _carpeta()
    historial = leer_historial(carpeta)
    corridas = [c for c in historial if c["motivo"] in MOTIVOS]
    subidas = {c["respaldo"]: c.get("subido_a") or [] for c in historial if c.get("respaldo") and c.get("ok")}
    ultima = None
    if corridas:
        ultima = dict(corridas[-1])
        ultima["fecha"] = fecha_amigable(datetime.fromisoformat(ultima["fecha_utc"]).astimezone(ZONA_HORARIA_APP))
        ultima["motivo_texto"] = MOTIVOS[ultima["motivo"]]
    return templates.TemplateResponse(
        "admin/respaldos.html",
        {
            "request": request,
            "admin": admin,
            "respaldos": [_fila(c, subidas) for c in reversed(listar_respaldos(carpeta))],
            "ultima_corrida": ultima,
            "carpeta_host": os.environ.get("RESPALDO_DIR_HOST", "/home/ubuntu/paquetex-respaldos"),
            "app_host": os.environ.get("RESPALDO_APP_DIR_HOST", "/home/ubuntu/app/PaqueteX"),
            "bucket": os.environ.get("RESPALDO_S3_BUCKET", "paquetex-respaldos"),
            "a_pedido": _estado_operacion(ultima_operacion(db, TipoOperacion.RESPALDO)),
            "copia_fotos": _estado_operacion(ultima_operacion(db, TipoOperacion.COPIA_FOTOS)),
            "descargas": _estado_descargas(db),
            "aviso": aviso,
        },
    )


def _estado_operacion(operacion) -> dict | None:
    if operacion is None:
        return None
    return {
        "en_curso": en_curso(operacion),
        "interrumpida": operacion.estado == EstadoOperacion.EN_CURSO.value and not en_curso(operacion),
        "ok": operacion.estado == EstadoOperacion.OK.value,
        "desde": fecha_amigable(operacion.inicio.astimezone(ZONA_HORARIA_APP)),
        "inicio_utc": operacion.inicio.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "solicitado_por": operacion.solicitado_por,
        "detalle": operacion.detalle,
        "avance_actual": operacion.avance_actual,
        "avance_total": operacion.avance_total,
    }


def _lanzar_o_marcar_fallo(request, db: Session, admin: Usuario, lanzar, operacion, tipo: TipoOperacion):
    try:
        lanzar(operacion.id, tipo)
    except Exception as exc:
        # Sin proceso detrás, la operación no puede quedar "en curso".
        terminar_operacion(db, operacion.id, False, f"No se pudo iniciar: {exc}")
        db.commit()
        return _pantalla(request, db, admin, aviso=f"No se pudo iniciar: {exc}")
    return RedirectResponse("/administracion/respaldos", status_code=303)


@router.get("/administracion/respaldos", response_class=HTMLResponse)
def admin_respaldos(request: Request, db: Session = Depends(get_db), admin: Usuario = Depends(require_admin)):
    return _pantalla(request, db, admin)


@router.post("/administracion/respaldos/ahora")
def admin_respaldos_ahora(
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    lanzar=Depends(get_lanzador_respaldo),
):
    try:
        operacion = iniciar_operacion(db, TipoOperacion.RESPALDO, admin.email)
    except OperacionEnCurso:
        return _pantalla(request, db, admin, aviso="Ya hay un respaldo en curso: espera a que termine.")
    db.commit()
    return _lanzar_o_marcar_fallo(request, db, admin, lanzar, operacion, TipoOperacion.RESPALDO)


@router.post("/administracion/respaldos/fotos/copiar")
def admin_respaldos_copiar_fotos(
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    lanzar=Depends(get_lanzador_respaldo),
):
    try:
        operacion = iniciar_operacion(db, TipoOperacion.COPIA_FOTOS, admin.email)
    except OperacionEnCurso:
        return _pantalla(request, db, admin, aviso="Ya hay una copia de fotos en curso: espera a que termine.")
    db.commit()
    return _lanzar_o_marcar_fallo(request, db, admin, lanzar, operacion, TipoOperacion.COPIA_FOTOS)


@router.get("/administracion/respaldos/fotos/descargar")
def admin_respaldos_descargar_fotos(
    cuales: str = "nuevas", db: Session = Depends(get_db), admin: Usuario = Depends(require_admin)
):
    """Declarada ANTES de `/{nombre}/descargar`, que si no la atraparía ("fotos" como nombre de respaldo).
    `.zip` por partes con la estructura de S3. "nuevas" = registradas desde la última descarga, y deja la marca
    para la siguiente (por sistema, no por usuario); "todas" no depende de la marca ni la mueve."""
    if cuales == "todas":
        fotos = fotos_locales(_carpeta_fotos())
        return _zip_fotos(fotos, "fotos-todas")
    ultima = ultima_operacion(db, TipoOperacion.DESCARGA_FOTOS)
    ahora = datetime.now(timezone.utc)
    nuevas = fotos_nuevas(db, _carpeta_fotos(), ultima.inicio if ultima else None, ahora)
    fabrica = sessionmaker(bind=db.get_bind())

    def al_terminar() -> None:
        # Solo si el .zip se envió completo: una descarga cortada no mueve la marca.
        with fabrica() as session:
            registrar_descarga_fotos(session, admin.email, nuevas.marca_siguiente, len(nuevas.fotos))
            session.commit()

    return _zip_fotos(nuevas.fotos, f"fotos-nuevas-{ahora.astimezone(ZONA_HORARIA_APP):%Y-%m-%d_%H%M}", al_terminar)


def _zip_fotos(fotos, nombre: str, al_terminar=None) -> StreamingResponse:
    def partes():
        yield from zip_por_partes(list(fotos))
        if al_terminar is not None:
            al_terminar()

    return StreamingResponse(
        partes(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nombre}.zip"'},
    )


@router.get("/administracion/respaldos/{nombre}/descargar")
def admin_respaldos_descargar(nombre: str, admin: Usuario = Depends(require_admin)):
    # Solo un nombre de la lista: nunca una ruta armada con lo que mande el navegador.
    respaldo = next((c for c in listar_respaldos(_carpeta()) if c.name == nombre), None)
    if respaldo is None:
        raise HTTPException(status_code=404)
    return StreamingResponse(
        zip_de_respaldo(respaldo),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{respaldo.name}.zip"'},
    )

