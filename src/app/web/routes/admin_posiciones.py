# -*- coding: utf-8 -*-
"""
Administración → Posiciones (issue 416, `.scratch/pendientes-cliente`): el ADMIN activa/desactiva filas del estante.

Una fila desactivada no se puede elegir en el modal Recibir (`posicion_service.py`). Protegida por `require_admin`.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.domain.posicion import FILAS
from app.domain.posicion_service import SinFilasActivas, filas_desactivadas, guardar_filas_activas
from app.domain.usuario import Usuario

from ..db import get_db
from ..security import require_admin
from ..templating import templates

router = APIRouter()

_URL = "/administracion/posiciones"


def _pantalla(request: Request, db: Session, admin: Usuario, status_code=200, **extra):
    desactivadas = filas_desactivadas(db)
    filas = [(fila, fila not in desactivadas) for fila in range(FILAS, 0, -1)]  # de arriba hacia abajo, como el estante
    return templates.TemplateResponse(
        "admin/posiciones.html",
        {"request": request, "admin": admin, "filas": filas, **extra},
        status_code=status_code,
    )


@router.get(_URL, response_class=HTMLResponse)
def admin_posiciones(request: Request, db: Session = Depends(get_db), admin: Usuario = Depends(require_admin)):
    return _pantalla(request, db, admin)


@router.post(_URL, response_class=HTMLResponse)
def admin_posiciones_guardar(
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    fila: list[int] = Form(default=[]),
):
    try:
        guardar_filas_activas(db, set(fila))
    except SinFilasActivas as exc:
        return _pantalla(request, db, admin, status_code=400, error=str(exc))
    db.commit()
    return _pantalla(request, db, admin, guardado=True)
