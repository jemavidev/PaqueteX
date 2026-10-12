"""
Vista "Avisos especiales" (`.scratch/avisos-especiales`): el ADMIN avisa a mano, por SMS o WhatsApp, a los clientes
con algo pendiente que ciertos días no hay servicio de paquetes. Solo ADMIN (`require_admin`). Las reglas viven en
`app.domain.aviso_especial_service`; esta capa solo arma la pantalla.

El mensaje elegido en el selector viaja en la URL (`?mensaje=<id>`): se conserva al recargar, buscar y filtrar.
"""

import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.domain.aviso_especial_service import (
    AvisoNoEnviable,
    conteos_por_estado,
    elegir_mensaje,
    enlaces_whatsapp,
    enviar_sms,
    filtrar_clientes,
    listar_clientes,
    listar_mensajes,
)
from app.domain.notification_sender import NotificationSender
from app.domain.paquete import EstadoPaquete
from app.domain.usuario import Usuario

from ..db import get_db
from ..notifications import get_notification_sender, sms_configurado
from ..security import require_admin
from ..templating import templates

router = APIRouter()


def _uuid_o_none(valor: str | None):
    try:
        return uuid.UUID(valor) if valor else None
    except ValueError:
        return None


_POR_PAGINA = 30
_ESTADOS_FILTRO = (EstadoPaquete.ANUNCIADO.value, EstadoPaquete.RECIBIDO.value)


def _contexto(
    request: Request, db: Session, admin: Usuario, mensaje_id: str | None, q: str = "", estado: str = "", pagina: int = 1
) -> dict:
    mensaje = elegir_mensaje(db, _uuid_o_none(mensaje_id))
    estado = estado if estado in _ESTADOS_FILTRO else ""
    buscados = filtrar_clientes(listar_clientes(db), q=q)
    filtrados = filtrar_clientes(buscados, estado=estado)
    total_paginas = max(1, -(-len(filtrados) // _POR_PAGINA))
    pagina = min(max(1, pagina), total_paginas)
    clientes = filtrados[(pagina - 1) * _POR_PAGINA : pagina * _POR_PAGINA]
    return {
        "request": request,
        "admin": admin,
        "clientes": clientes,
        "mensajes": listar_mensajes(db),
        "mensaje": mensaje,
        "whatsapp": {c.persona.id: enlaces_whatsapp(c, mensaje) for c in clientes} if mensaje else {},
        "filtro_q": q or "",
        "filtro_estado": estado,
        "conteos": conteos_por_estado(buscados),
        "pagina_actual": pagina,
        "total_paginas": total_paginas,
        "params_paginacion": {"mensaje": str(mensaje.id) if mensaje else None, "q": q or None, "estado": estado or None},
    }


# Resultado de un envío sin JS (Post/Redirect/Get): el POST redirige con `?resultado=` y la vista muestra el aviso.
# Solo códigos conocidos: la URL nunca trae texto libre que la vista muestre (un enlace armado no puede poner un
# mensaje falso en pantalla).
_AVISOS_RESULTADO = {
    "enviado": ("SMS enviado.", "success"),
    "fallo": ("No se pudo enviar el SMS. Intenta de nuevo.", "danger"),
    "no-configurado": ("El SMS no está configurado todavía (Administración → Proveedores).", "danger"),
    "invalido": ("Datos de envío inválidos.", "danger"),
    "no-enviable": ("No se pudo intentar el envío: el mensaje o el cliente ya no están disponibles.", "danger"),
}


@router.get("/administracion/avisos-especiales", response_class=HTMLResponse)
def avisos_especiales(
    request: Request,
    mensaje: str = None,
    q: str = "",
    estado: str = "",
    pagina: str = "1",
    resultado: str = None,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
):
    """La vista. Con `X-Requested-With: fetch` (búsqueda en vivo, ticket 07) responde solo la lista."""
    contexto = _contexto(request, db, admin, mensaje, q=q, estado=estado, pagina=int(pagina) if pagina.isdigit() else 1)
    if request.headers.get("X-Requested-With") == "fetch":
        return templates.TemplateResponse("admin/_avisos_especiales_resultados.html", contexto)
    if resultado in _AVISOS_RESULTADO:
        contexto["aviso"], contexto["aviso_variante"] = _AVISOS_RESULTADO[resultado]
    return templates.TemplateResponse("admin/avisos_especiales.html", contexto)


@router.post("/administracion/avisos-especiales/sms", response_class=HTMLResponse)
def avisos_especiales_enviar_sms(
    request: Request,
    persona_id: str = Form(None),
    mensaje: str = Form(None),
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    sender: NotificationSender = Depends(get_notification_sender),
):
    """Envía por SMS el mensaje elegido a UN cliente (ticket 05). Síncrono: espera la respuesta real del proveedor.

    Con JS (`X-Requested-With: fetch`) responde la fila de ese cliente ya actualizada (nuevo "Último SMS" o el aviso
    rojo de falla) para reemplazarla sin recargar; un error que impide intentar el envío responde 400 con el texto.
    Sin JS, redirige a la vista con el resultado (Post/Redirect/Get)."""
    es_fetch = request.headers.get("X-Requested-With") == "fetch"
    mensaje_id, pid = _uuid_o_none(mensaje), _uuid_o_none(persona_id)

    def _error(texto: str, codigo: str):
        if es_fetch:
            return HTMLResponse(texto, status_code=400)
        return _volver(mensaje, resultado=codigo)

    if not sms_configurado():
        return _error(_AVISOS_RESULTADO["no-configurado"][0], "no-configurado")
    if mensaje_id is None or pid is None:
        return _error(_AVISOS_RESULTADO["invalido"][0], "invalido")
    try:
        resultado = enviar_sms(db, pid, mensaje_id, sender, usuario_id=admin.id)
    except AvisoNoEnviable as exc:
        return _error(str(exc), "no-enviable")

    if not es_fetch:
        return _volver(mensaje, resultado="enviado" if resultado.enviado else "fallo")
    mensaje_actual = elegir_mensaje(db, mensaje_id)
    return templates.TemplateResponse(
        "admin/_aviso_cliente.html",
        {
            "request": request,
            "cliente": resultado.cliente,
            "mensaje": mensaje_actual,
            "whatsapp": {resultado.cliente.persona.id: enlaces_whatsapp(resultado.cliente, mensaje_actual)},
            "fallo_sms": None if resultado.enviado else resultado.cliente.persona.id,
        },
    )


def _volver(mensaje: str | None, **params) -> RedirectResponse:
    consulta = urlencode({k: v for k, v in {"mensaje": mensaje, **params}.items() if v})
    return RedirectResponse(f"/administracion/avisos-especiales?{consulta}", status_code=303)
