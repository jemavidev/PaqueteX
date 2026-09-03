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
SIEMPRE vacío -- lo que cambió es solo el `placeholder`. Un campo de TEXTO
vacío al guardar significa "no cambiar esa credencial" -- solo los campos
con contenido nuevo se mandan a `app/infra/deploy_ssh.py::
aplicar_credenciales_proveedor` (issue 04). Un campo BOOLEANO (issue 294) no
tiene esa opción -- un `<input type=checkbox>` real siempre manda su
posición actual, nunca "no cambiar" -- así que ahí se compara contra el
valor YA presente en `.env` y solo se manda si de verdad difiere (ver
`_campo_cambio`). Esa llamada es SÍNCRONA a propósito: la ruta espera su
confirmación real (éxito/fallo) antes de responder, igual que
`admin.py::admin_notificaciones_probar` -- nunca un "guardado" optimista.
Si falla, el toggle/orden del mismo formulario YA se guardó (son
operaciones independientes; ver `admin_proveedores_guardar`) pero ninguna
credencial cambia, y no queda auditoría de un cambio que en realidad no
pasó.

**Campos ocultos sincronizados con el toggle (issue 293)**: un proveedor
puede declarar `sincroniza_habilitado_con` (ej. `AWS_SNS_SMS_ENABLED` en
`AWS_SNS`) -- corrección en vivo del cliente, que encontró confuso tener
dos controles de "encendido" en la misma tarjeta (el toggle `habilitado` de
BD, y un segundo campo booleano de `.env` heredado de antes de este
feature). Ese campo (`CampoProveedor.oculto=True`) deja de mostrarse; el
toggle lo mantiene en sync solo, y SOLO cuando `habilitado` cambia de
valor -- nunca en cada guardado, para no reiniciar el servidor sin
necesidad.
"""

import os
from typing import NamedTuple

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from starlette.datastructures import FormData
from sqlalchemy.orm import Session

from app.domain.preferencia_notificacion import CanalNotificacion
from app.domain.proveedores_catalogo import CATALOGO, CampoProveedor, ProveedorInfo
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


def _campo_cambio(proveedor: ProveedorInfo, campo: CampoProveedor, form: FormData) -> _CambioCredencial | None:
    """`None` si `campo` no cambió en este submit -- si cambió, el
    `_CambioCredencial` a aplicar.

    Texto: "vacío = no cambiar" (ticket 05) -- un valor no vacío siempre
    cuenta como cambio, sin comparar contra lo que ya había (ese contrato
    no cambia acá).

    Booleano (issue 294, pedido explícito del cliente: "crea un toggle
    para cada uno"): un `<input type=checkbox>` real no tiene forma de
    decir "no cambiar" -- siempre manda su posición actual (o nada, si está
    apagado). Por eso se compara esa posición contra el valor YA presente
    en `.env` (no contra lo que se cargó al abrir el formulario) -- mismo
    criterio que la sincronización de `sincroniza_habilitado_con` (issue
    293): solo cuenta como cambio si de verdad difiere, para no reaplicar
    (y reiniciar el servidor) en cada guardado que no tocó el switch. Sin
    configurar en `.env` se trata como "false" para esta comparación --
    mismo default que ya usa la plantilla para dibujar el switch apagado
    (`campo.valor_actual` vacío -> `checked=False`) -- si no, el primer
    guardado de un campo nunca antes tocado se vería como "cambio" aunque
    el switch se haya dejado tal cual estaba (apagado)."""
    if campo.tipo == "booleano":
        nuevo = "true" if form.get(campo.variable_env) is not None else "false"
        actual = (os.environ.get(campo.variable_env) or "false").strip().lower()
        if nuevo == actual:
            return None
        return _CambioCredencial(proveedor.clave, campo.variable_env, nuevo)
    valor_nuevo = (form.get(campo.variable_env) or "").strip()
    if not valor_nuevo:
        return None
    return _CambioCredencial(proveedor.clave, campo.variable_env, valor_nuevo)


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


def _config_por_clave(db: Session, canal_enum: CanalNotificacion) -> dict[str, object]:
    """`{proveedor.clave: ProveedorConfig}` de `canal_enum` -- compartido
    por `_filas_proveedores` (qué mostrar) y `admin_proveedores_guardar`
    (el `habilitado` efectivo ANTES de guardar, para detectar si
    `sincroniza_habilitado_con` debe dispararse)."""
    return {c.proveedor: c for c in listar_config(db, canal_enum)}


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
    `disabled` y el badge "Próximamente").

    Un `CampoProveedor.oculto=True` (issue 293) NUNCA llega a `campos` --
    sigue en el allowlist SSH (`variables_permitidas()`, derivado
    directamente del catálogo, no de esta función), pero
    `admin_proveedores_guardar` lo sincroniza solo con el toggle
    `habilitado` en vez de pedírselo al admin como campo aparte."""
    resultado = []
    for canal_str, proveedores_catalogo in CATALOGO.items():
        if not proveedores_catalogo:
            continue
        canal_enum = CanalNotificacion(canal_str)
        config_por_clave = _config_por_clave(db, canal_enum)
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
                if not campo.oculto
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
    #
    # `sincroniza_habilitado_con` (issue 293, pedido explícito del cliente:
    # "el toggle debe hacer las 2 cosas"): además de guardar en BD, si el
    # proveedor declara una variable de entorno para sincronizar Y su
    # `habilitado` CAMBIÓ de valor (nunca en cada guardado -- aplicar una
    # credencial reinicia el contenedor, no hay que pagar ese costo si el
    # toggle ni se tocó), se agrega a la MISMA lista de `_CambioCredencial`
    # de más abajo -- una sola llamada a `aplicar_credenciales_proveedor`,
    # un solo reinicio, para todo lo que cambió en este submit.
    config_por_clave = _config_por_clave(db, canal_enum)
    sincronizaciones: list[_CambioCredencial] = []
    for proveedor in proveedores_catalogo:
        if not proveedor.disponible:
            continue
        habilitado_anterior, _orden_anterior = habilitado_orden_efectivos(
            config_por_clave.get(proveedor.clave)
        )
        habilitado = form.get(f"{proveedor.clave}_habilitado") is not None
        orden_bruto = (form.get(f"{proveedor.clave}_orden") or "").strip()
        orden = int(orden_bruto) if orden_bruto.isdigit() else None
        guardar_habilitado_orden(
            db, canal_enum, proveedor.clave, habilitado, orden, usuario_id=admin.id
        )
        if proveedor.sincroniza_habilitado_con and habilitado != habilitado_anterior:
            sincronizaciones.append(
                _CambioCredencial(
                    proveedor.clave,
                    proveedor.sincroniza_habilitado_con,
                    "true" if habilitado else "false",
                )
            )

    # Credenciales: `_campo_cambio` decide qué cuenta como cambio (texto:
    # vacío = no cambiar; booleano -- issue 294 -- comparado contra el
    # valor real de `.env`). Una sola lista de `_CambioCredencial`
    # (arrancando con las sincronizaciones de arriba); `cambios`/la
    # auditoría son vistas derivadas de ella, nunca colecciones separadas.
    credenciales_cambiadas = sincronizaciones + [
        cambio
        for proveedor in proveedores_catalogo
        if proveedor.disponible
        for campo in proveedor.campos
        if not campo.oculto
        if (cambio := _campo_cambio(proveedor, campo, form)) is not None
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
