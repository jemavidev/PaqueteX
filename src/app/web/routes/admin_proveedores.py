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
`proveedores_catalogo.CATALOGO` -- hoy los 4 (issue 289, pedido explícito
del cliente, revierte la decisión original de esconder WhatsApp/Llamadas
por completo). WhatsApp (`META`) es editable igual que SMS/Email pese a no
tener `Sender` real todavía -- deja el terreno de configuración listo.

**Pantalla en tabs (issue 290, pedido explícito del cliente: "unificar con
/residentes")** -- mismo patrón que `customers_manage/detail.html`
(`.tab-btn`/`.tab-panel`, JS plano sin framework, `?tab=` sincronizado por
`history.replaceState`, ancho de página `max-w-lg lg:max-w-2xl`). Todas las
tabs están siempre presentes, incluso Llamadas (`PXB`, `disponible=False`)
-- iterado varias veces en vivo con el cliente (se probó ocultarla del todo
y mostrar un estado vacío) hasta converger en esta versión: la tab existe,
pero al entrar se ven el toggle y los campos SIEMPRE deshabilitados
(`disabled` en HTML, badge "Próximamente", botón "Guardar" deshabilitado) --
visibles para dejar clara la forma futura de la integración, pero sin
poder tocarlos. La ruta POST igual descarta cualquier cambio a un proveedor
no disponible aunque llegue en la petición (defensa en profundidad -- el
`disabled` de HTML ya lo impide del lado navegador, pero un POST armado a
mano tampoco debe poder colarlo).

**Credenciales (issue 05, Fase 2)**: cada campo del catálogo se muestra
como un input de texto/enmascarado. Desde issue 291 (pedido explícito del
cliente, "por seguridad seria bueno solo ver la informacion necesaria pero
no toda") la pantalla SÍ lee el valor real de `.env` -- pero nunca lo manda
completo al navegador: un campo `secreto` configurado muestra un
enmascarado parcial (`_enmascarar_secreto`), uno no-secreto muestra el
valor real completo (ver `_valor_actual`). El `value=` del input sigue
SIEMPRE vacío -- lo que cambió es solo el `placeholder`. Un campo vacío al
guardar significa "no cambiar esa credencial" -- solo los campos con contenido
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
from app.domain.proveedores_catalogo import CATALOGO, CampoProveedor
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

_ETIQUETA_CANAL = {"SMS": "SMS", "EMAIL": "Email", "WHATSAPP": "WhatsApp", "LLAMADA": "Llamadas"}


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


_CARACTERES_VISIBLES = 4
_PUNTOS_MASCARA = "•" * 8


def _enmascarar_secreto(valor: str) -> str:
    """Revela el inicio y el final de `valor`, con un número FIJO de puntos
    enmascarando el resto -- NO un punto por caracter oculto, para no
    filtrar cuántos caracteres tiene el secreto real. Valores cortos (largo
    <= 2×`_CARACTERES_VISIBLES`) se enmascaran por completo: revelar 4+4 de
    un valor de, digamos, 6 caracteres dejaría casi todo expuesto."""
    if len(valor) <= _CARACTERES_VISIBLES * 2:
        return _PUNTOS_MASCARA
    return valor[:_CARACTERES_VISIBLES] + _PUNTOS_MASCARA + valor[-_CARACTERES_VISIBLES:]


def _valor_actual(campo: CampoProveedor, valor_real: str | None) -> str | None:
    """`None` si `campo` no está configurado en `.env` -- si lo está,
    enmascarado (issue 291, pedido explícito del cliente: "por seguridad
    seria bueno solo ver la informacion necesaria pero no toda") si
    `campo.secreto`, o el valor REAL sin tocar si no lo es (`AWS_REGION`,
    `SMTP_HOST`, los booleanos, etc. -- nunca fueron secretos, mostrarlos
    completos es justamente "la información necesaria"). Reemplaza el
    antiguo `bool` "configurado" -- este valor YA sirve para saber si está
    configurado (`is not None`) y qué mostrar, sin una segunda bandera."""
    if not valor_real:
        return None
    return _enmascarar_secreto(valor_real) if campo.secreto else valor_real


def _filas_proveedores(db: Session) -> list[dict]:
    """Un `dict` por canal del catálogo (`canal`, `etiqueta_canal`,
    `multiples` -- gobierna si se muestra el campo de orden, solo tiene
    sentido con 2+ proveedores --, `proveedores`: lista de `dict` con
    `clave`/`etiqueta`/`habilitado`/`orden`/`campos` vigentes -- cada
    `campo` es `variable_env`/`etiqueta`/`secreto`/`tipo`/`valor_actual`.

    `valor_actual` (issue 291) es `None` si el campo no está configurado, o
    -- si lo está -- el valor real (campos NO secretos) o una versión
    enmascarada de él (campos secretos, ver `_enmascarar_secreto`) -- NUNCA
    el valor real completo de un campo secreto. El HTML sigue sin precargar
    ESTE valor en el `value=` del input (`_CambioCredencial`/"vacío = no
    cambiar" no cambia con esto) -- `valor_actual` solo alimenta el
    `placeholder`, texto de solo lectura que el navegador nunca manda de
    vuelta al hacer submit.

    Sin fila en `ProveedorConfig` para un proveedor del catálogo: mismo
    fallback que `proveedor_config_service.armar_candidatos` -- las dos
    comparten `habilitado_orden_efectivos()` como única fuente de verdad,
    para que la pantalla NUNCA muestre un estado distinto al que la cadena
    de envío real usaría. Excepción: un proveedor `disponible=False` (hoy
    solo Llamadas/`PXB`) siempre se muestra `habilitado=False` sin importar
    lo que diga BD -- mostrarlo "encendido" mentiría sobre una capacidad que
    el código todavía no tiene.

    Cada canal SIEMPRE aparece en el resultado, incluso uno sin ningún
    proveedor `disponible=True` (issue 289/290, iterado varias veces en
    vivo con el cliente hasta esta versión final: la tab queda presente,
    pero sus campos se muestran deshabilitados -- ver `disponible` en cada
    `dict` de `proveedores`, que la plantilla usa para el atributo HTML
    `disabled` y el badge "Próximamente")."""
    resultado = []
    for canal_str, proveedores_catalogo in CATALOGO.items():
        if not proveedores_catalogo:
            continue
        canal_enum = CanalNotificacion(canal_str)
        config_por_clave = {c.proveedor: c for c in listar_config(db, canal_enum)}
        filas = []
        for p in proveedores_catalogo:
            habilitado, orden = habilitado_orden_efectivos(config_por_clave.get(p.clave))
            habilitado = habilitado and p.disponible
            campos = [
                {
                    "variable_env": campo.variable_env,
                    "etiqueta": campo.etiqueta,
                    "secreto": campo.secreto,
                    "tipo": campo.tipo,
                    "valor_actual": _valor_actual(campo, os.environ.get(campo.variable_env)),
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
                    "disponible": p.disponible,
                }
            )
        resultado.append(
            {
                "canal": canal_str,
                "etiqueta_canal": _ETIQUETA_CANAL.get(canal_str, canal_str.title()),
                "multiples": len(filas) > 1,
                "proveedores": filas,
                "todos_deshabilitados": not any(p.disponible for p in proveedores_catalogo),
            }
        )
    return resultado


def _tab_inicial(canales: list[dict], tab: str | None) -> str:
    """El primer canal del catálogo (orden de `CATALOGO`, hoy "SMS") si
    `tab` viene vacío o no calza con ningún canal real -- mismo criterio
    que `customers_manage.py::customers_manage_detail` con `?tab=`: un
    valor desconocido (link roto, canal renombrado) no debe romper la
    página, solo cae al default."""
    validos = {c["canal"] for c in canales}
    return tab if tab in validos else canales[0]["canal"]


@router.get("/administracion/proveedores", response_class=HTMLResponse)
def admin_proveedores_form(
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    tab: str | None = None,
):
    canales = _filas_proveedores(db)
    return templates.TemplateResponse(
        "admin/proveedores.html",
        {
            "request": request,
            "admin": admin,
            "canales": canales,
            "tab_inicial": _tab_inicial(canales, tab),
        },
    )


@router.post("/administracion/proveedores/{canal}", response_class=HTMLResponse)
async def admin_proveedores_guardar(
    canal: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
):
    def _error(mensaje: str):
        canales = _filas_proveedores(db)
        return templates.TemplateResponse(
            "admin/proveedores.html",
            {
                "request": request,
                "admin": admin,
                "canales": canales,
                "tab_inicial": _tab_inicial(canales, canal),
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
    # falla). `disponible=False` (issue 289/290, ej. Llamadas/PXB) se salta
    # por completo -- defensa en profundidad: sus campos ya viajan
    # `disabled` en el HTML (un input `disabled` ni se manda al hacer
    # submit), pero un POST armado a mano no debe poder colar un cambio a
    # un proveedor que la pantalla muestra bloqueado.
    for proveedor in proveedores_catalogo:
        if not proveedor.disponible:
            continue
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
        if proveedor.disponible
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

    canales = _filas_proveedores(db)
    return templates.TemplateResponse(
        "admin/proveedores.html",
        {
            "request": request,
            "admin": admin,
            "canales": canales,
            "tab_inicial": _tab_inicial(canales, canal),
            "guardado": True,
        },
    )
