# -*- coding: utf-8 -*-
"""
Ruta `/administracion/proveedores` — habilitar/deshabilitar, reordenar, y
editar credenciales de proveedores de notificación (`.scratch/
administracion-proveedores/spec.md`, issues 03 y 05). Pantalla separada de
`/administracion/notificaciones` a propósito: esa edita el TEXTO de los
mensajes, esta edita la plomería de CÓMO se envían.

Protegida por `require_admin`, mismo patrón que
`app/web/routes/admin.py::admin_conjunto_form`/`admin_conjunto_guardar`: GET
renderiza, POST valida + guarda + re-renderiza (nunca redirige) con un flag
`guardado`/`error`.

Solo aparecen los canales con al menos un proveedor en
`proveedores_catalogo.CATALOGO` -- hoy SMS y Email. WhatsApp/Llamadas no
tienen proveedor real todavía, así que no aparecen (sin sección
"próximamente" -- ver spec, decisión explícita).

**Credenciales (issue 05, Fase 2)**: cada campo del catálogo se muestra
como un input de texto/enmascarado -- NUNCA el valor real (ni la propia
pantalla lo lee; basta con `bool(os.environ.get(variable))` para saber que
ya está seteado y mostrar el placeholder). Un campo vacío al guardar
significa "no cambiar esa credencial" -- solo los campos con contenido
nuevo se mandan a `app/infra/deploy_ssh.py::aplicar_credenciales_proveedor`
(issue 04). Esa llamada es SÍNCRONA a propósito: la ruta espera su
confirmación real (éxito/fallo) antes de responder, igual que
`admin.py::admin_notificaciones_probar` -- nunca un "guardado" optimista.
Si falla, el toggle/orden del mismo formulario YA se guardó (son
operaciones independientes; ver `admin_proveedores_guardar`) pero ninguna
credencial cambia, y no queda auditoría de un cambio que en realidad no
pasó.
"""

import os
from typing import NamedTuple

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.domain.preferencia_notificacion import CanalNotificacion
from app.domain.proveedores_catalogo import CATALOGO
from app.domain.proveedor_config_service import (
    guardar_habilitado_orden,
    habilitado_orden_efectivos,
    listar_config,
    registrar_cambio_credencial,
)
from app.domain.usuario import Usuario
from app.infra.deploy_ssh import ErrorAplicandoCredenciales, aplicar_credenciales_proveedor

from ..db import get_db
from ..security import require_admin
from ..templating import templates

router = APIRouter()

_ETIQUETA_CANAL = {"SMS": "SMS", "EMAIL": "Email"}


class _CambioCredencial(NamedTuple):
    """Un campo de credencial con contenido nuevo -- `proveedor`/
    `variable_env` viajan siempre junto a `valor` (nunca por separado, ver
    code review issue 05); `cambios` (el `dict` que espera `app/infra/
    deploy_ssh.py`) y `campos_cambiados` (lo que necesita la auditoría) son
    ambos vistas derivadas de la MISMA lista, nunca dos colecciones
    construidas por separado."""

    proveedor: str
    variable_env: str
    valor: str


def _filas_proveedores(db: Session) -> list[dict]:
    """Un `dict` por canal del catálogo (`canal`, `etiqueta_canal`,
    `multiples` -- gobierna si se muestra el campo de orden, solo tiene
    sentido con 2+ proveedores --, `proveedores`: lista de `dict` con
    `clave`/`etiqueta`/`habilitado`/`orden`/`campos` vigentes -- cada
    `campo` es `variable_env`/`etiqueta`/`secreto`/`configurado`, NUNCA el
    valor real de la credencial).

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
            campos = [
                {
                    "variable_env": campo.variable_env,
                    "etiqueta": campo.etiqueta,
                    "secreto": campo.secreto,
                    "tipo": campo.tipo,
                    "configurado": bool(os.environ.get(campo.variable_env)),
                }
                for campo in p.campos
            ]
            filas.append(
                {
                    "clave": p.clave,
                    "etiqueta": p.etiqueta,
                    "habilitado": habilitado,
                    "orden": orden,
                    "campos": campos,
                }
            )
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
    def _error(mensaje: str):
        return templates.TemplateResponse(
            "admin/proveedores.html",
            {
                "request": request,
                "admin": admin,
                "canales": _filas_proveedores(db),
                "error": mensaje,
            },
            status_code=400,
        )

    try:
        canal_enum = CanalNotificacion(canal)
    except ValueError:
        return _error("Canal inválido.")

    proveedores_catalogo = CATALOGO.get(canal, ())
    if not proveedores_catalogo:
        return _error("Ese canal no tiene proveedores.")

    form = await request.form()

    # Habilitado/orden: BD, instantáneo, siempre se aplica (ver docstring
    # del módulo -- independiente de si la parte de credenciales de abajo
    # falla).
    for proveedor in proveedores_catalogo:
        habilitado = form.get(f"{proveedor.clave}_habilitado") is not None
        orden_bruto = (form.get(f"{proveedor.clave}_orden") or "").strip()
        orden = int(orden_bruto) if orden_bruto.isdigit() else None
        guardar_habilitado_orden(
            db, canal_enum, proveedor.clave, habilitado, orden, usuario_id=admin.id
        )

    # Credenciales: solo los campos con contenido nuevo -- vacío = no
    # cambiar. Una sola lista de `_CambioCredencial`; `cambios`/la auditoría
    # son vistas derivadas de ella, nunca dos colecciones separadas.
    credenciales_cambiadas = [
        _CambioCredencial(proveedor.clave, campo.variable_env, valor_nuevo)
        for proveedor in proveedores_catalogo
        for campo in proveedor.campos
        if (valor_nuevo := (form.get(campo.variable_env) or "").strip())
    ]

    if credenciales_cambiadas:
        cambios = {c.variable_env: c.valor for c in credenciales_cambiadas}
        try:
            aplicar_credenciales_proveedor(cambios)
        except ErrorAplicandoCredenciales as exc:
            return _error(f"No se pudo aplicar la credencial: {exc}")
        for cambio in credenciales_cambiadas:
            registrar_cambio_credencial(
                db, canal_enum, cambio.proveedor, cambio.variable_env, usuario_id=admin.id
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
