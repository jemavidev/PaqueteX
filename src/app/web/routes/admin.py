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

from app.domain.staff_service import create_staff
from app.domain.usuario import RolUsuario, Usuario

from ..db import get_db
from ..security import require_admin
from ..templating import templates

router = APIRouter()


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
