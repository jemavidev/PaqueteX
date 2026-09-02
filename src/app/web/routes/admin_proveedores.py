# -*- coding: utf-8 -*-
"""
Ruta `/administracion/proveedores` — habilitar/deshabilitar y reordenar
proveedores de notificación (`.scratch/administracion-proveedores/spec.md`,
issue 03). Pantalla separada de `/administracion/notificaciones` a
propósito: esa edita el TEXTO de los mensajes, esta edita la plomería de
CÓMO se envían.

Solo la parte de habilitado/orden (Fase 1) — las credenciales reales siguen
viviendo únicamente en `.env` del servidor (Fase 2, issue 04/05); esta
pantalla no las muestra ni las edita todavía.

Protegida por `require_admin`, mismo patrón que
`app/web/routes/admin.py::admin_conjunto_form`/`admin_conjunto_guardar`: GET
renderiza, POST valida + guarda + re-renderiza (nunca redirige) con un flag
`guardado`/`error`.

Solo aparecen los canales con al menos un proveedor en
`proveedores_catalogo.CATALOGO` -- hoy SMS y Email. WhatsApp/Llamadas no
tienen proveedor real todavía, así que no aparecen (sin sección
"próximamente" -- ver spec, decisión explícita).
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.domain.preferencia_notificacion import CanalNotificacion
from app.domain.proveedores_catalogo import CATALOGO
from app.domain.proveedor_config_service import (
    guardar_habilitado_orden,
    habilitado_orden_efectivos,
    listar_config,
)
from app.domain.usuario import Usuario

from ..db import get_db
from ..security import require_admin
from ..templating import templates

router = APIRouter()

_ETIQUETA_CANAL = {"SMS": "SMS", "EMAIL": "Email"}


def _filas_proveedores(db: Session) -> list[dict]:
    """Un `dict` por canal del catálogo (`canal`, `etiqueta_canal`,
    `multiples` -- gobierna si se muestra el campo de orden, solo tiene
    sentido con 2+ proveedores --, `proveedores`: lista de `dict` con
    `clave`/`etiqueta`/`habilitado`/`orden` vigentes).

    Sin fila en `ProveedorConfig` para un proveedor del catálogo: mismo
    fallback que `proveedor_config_service.armar_candidatos` -- las dos
    comparten `habilitado_orden_efectivos()` como única fuente de verdad,
    para que la pantalla NUNCA muestre un estado distinto al que la cadena
    de envío real usaría."""
    resultado = []
    for canal_str, proveedores_catalogo in CATALOGO.items():
        if not proveedores_catalogo:
            continue
        canal_enum = CanalNotificacion(canal_str)
        config_por_clave = {c.proveedor: c for c in listar_config(db, canal_enum)}
        filas = []
        for p in proveedores_catalogo:
            habilitado, orden = habilitado_orden_efectivos(config_por_clave.get(p.clave))
            filas.append({"clave": p.clave, "etiqueta": p.etiqueta, "habilitado": habilitado, "orden": orden})
        resultado.append(
            {
                "canal": canal_str,
                "etiqueta_canal": _ETIQUETA_CANAL.get(canal_str, canal_str.title()),
                "multiples": len(filas) > 1,
                "proveedores": filas,
            }
        )
    return resultado


@router.get("/administracion/proveedores", response_class=HTMLResponse)
def admin_proveedores_form(
    request: Request, db: Session = Depends(get_db), admin: Usuario = Depends(require_admin)
):
    return templates.TemplateResponse(
        "admin/proveedores.html",
        {"request": request, "admin": admin, "canales": _filas_proveedores(db)},
    )


@router.post("/administracion/proveedores/{canal}", response_class=HTMLResponse)
async def admin_proveedores_guardar(
    canal: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
):
    try:
        canal_enum = CanalNotificacion(canal)
    except ValueError:
        return templates.TemplateResponse(
            "admin/proveedores.html",
            {
                "request": request,
                "admin": admin,
                "canales": _filas_proveedores(db),
                "error": "Canal inválido.",
            },
            status_code=400,
        )

    proveedores_catalogo = CATALOGO.get(canal, ())
    if not proveedores_catalogo:
        return templates.TemplateResponse(
            "admin/proveedores.html",
            {
                "request": request,
                "admin": admin,
                "canales": _filas_proveedores(db),
                "error": "Ese canal no tiene proveedores.",
            },
            status_code=400,
        )

    form = await request.form()
    for proveedor in proveedores_catalogo:
        habilitado = form.get(f"{proveedor.clave}_habilitado") is not None
        orden_bruto = (form.get(f"{proveedor.clave}_orden") or "").strip()
        orden = int(orden_bruto) if orden_bruto.isdigit() else None
        guardar_habilitado_orden(
            db, canal_enum, proveedor.clave, habilitado, orden, usuario_id=admin.id
        )

    return templates.TemplateResponse(
        "admin/proveedores.html",
        {
            "request": request,
            "admin": admin,
            "canales": _filas_proveedores(db),
            "guardado": True,
        },
    )
