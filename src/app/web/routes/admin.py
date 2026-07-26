# -*- coding: utf-8 -*-
"""
Ruta `/administracion/personal` — alta de cuentas de staff.

Protegida por `require_admin`: la única puerta real a `create_staff` (dominio,
ya probado). El actor de la creación sale de la sesión (`require_admin`), nunca
de un campo del formulario. Reutiliza `create_staff`/`RolUsuario` sin cambios.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.domain.notificacion_service import guardar_plantilla, obtener_texto_actual
from app.domain.paquete import EstadoPaquete, MotivoCancelacion
from app.domain.staff_service import create_staff
from app.domain.usuario import RolUsuario, Usuario

from ..db import get_db
from ..security import require_admin
from ..templating import templates

router = APIRouter()

_EVENTOS_SIN_MOTIVO = (EstadoPaquete.ANUNCIADO, EstadoPaquete.RECIBIDO, EstadoPaquete.ENTREGADO)


@router.get("/administracion/personal", response_class=HTMLResponse)
def admin_staff_form(request: Request, admin: Usuario = Depends(require_admin)):
    return templates.TemplateResponse(
        "admin/staff.html", {"request": request, "admin": admin, "roles": list(RolUsuario)}
    )


@router.post("/administracion/personal", response_class=HTMLResponse)
def admin_staff_submit(
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    email: str = Form(None),
    nombre: str = Form(None),
    password: str = Form(None),
    rol: str = Form(None),
):
    def _error(mensaje: str):
        return templates.TemplateResponse(
            "admin/staff.html",
            {
                "request": request,
                "admin": admin,
                "roles": list(RolUsuario),
                "error": mensaje,
                "email": email or "",
                "nombre": nombre or "",
            },
            status_code=400,
        )

    if not (email or "").strip() or not (nombre or "").strip() or not (password or ""):
        return _error("Email, nombre y contraseña son obligatorios.")

    try:
        rol_enum = RolUsuario(rol)
    except ValueError:
        return _error("Selecciona un rol válido.")

    try:
        creado = create_staff(db, admin, email, nombre, password, rol_enum)
    except (PermissionError, ValueError) as exc:
        return _error(str(exc))

    return templates.TemplateResponse(
        "admin/staff.html",
        {
            "request": request,
            "admin": admin,
            "roles": list(RolUsuario),
            "creado": creado,
        },
    )


def _filas_plantillas(db: Session):
    """Una fila por evento (sin motivo), más una fila por cada
    `MotivoCancelacion` para `CANCELADO` — con su texto vigente (personalizado
    o el default)."""
    filas = [
        {
            "evento": e,
            "motivo": None,
            "texto": obtener_texto_actual(db, e),
        }
        for e in _EVENTOS_SIN_MOTIVO
    ]
    for m in MotivoCancelacion:
        filas.append(
            {
                "evento": EstadoPaquete.CANCELADO,
                "motivo": m.value,
                "texto": obtener_texto_actual(db, EstadoPaquete.CANCELADO, m.value),
            }
        )
    return filas


@router.get("/administracion/notificaciones", response_class=HTMLResponse)
def admin_notificaciones_form(
    request: Request, db: Session = Depends(get_db), admin: Usuario = Depends(require_admin)
):
    return templates.TemplateResponse(
        "admin/notificaciones.html",
        {"request": request, "admin": admin, "filas": _filas_plantillas(db)},
    )


@router.post("/administracion/notificaciones", response_class=HTMLResponse)
def admin_notificaciones_guardar(
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    evento: str = Form(None),
    motivo: str = Form(None),
    texto: str = Form(None),
):
    def _error(mensaje: str):
        return templates.TemplateResponse(
            "admin/notificaciones.html",
            {
                "request": request,
                "admin": admin,
                "filas": _filas_plantillas(db),
                "error": mensaje,
            },
            status_code=400,
        )

    try:
        evento_enum = EstadoPaquete(evento)
    except ValueError:
        return _error("Evento inválido.")

    if not (texto or "").strip():
        return _error("El texto no puede quedar vacío.")

    guardar_plantilla(db, evento_enum, motivo or None, texto)

    return templates.TemplateResponse(
        "admin/notificaciones.html",
        {
            "request": request,
            "admin": admin,
            "filas": _filas_plantillas(db),
            "guardado": True,
        },
    )
