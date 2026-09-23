# -*- coding: utf-8 -*-
"""
Rutas de autenticación de cliente — OTP por teléfono.

`/otp` (pedir OTP) → `/otp/verificar` (confirmar) abre
una **sesión de cliente independiente** de la de staff (`CUSTOMER_SESSION_KEY`).
Mensajes de error GENÉRICOS (no distingue causa del rechazo).

Corrección en vivo 2026-08-02: `/otp/solicitar` responde SIEMPRE igual
(genérico "revisa tu teléfono"), sin importar si el teléfono es elegible
(existe + tiene un Paquete Recibido, ver `otp_service.elegible_para_otp`) —
no revela por contenido ni por tiempo de respuesta si un teléfono específico
está registrado. El envío real se difiere a un `BackgroundTask` (mismo
patrón que las notificaciones de evento de paquete). El caso de éxito ahora
REDIRIGE a `GET /otp/verificar` (antes respondía directo en `/otp/solicitar`,
lo que causaba el aviso de "reenviar formulario" al recargar) -- el caso de
error se queda igual (sin redirigir), consistente con el resto del sitio.
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, sessionmaker

from app.domain.otp_sender import OtpSender
from app.domain.otp_service import preparar_otp, verify_otp
from app.domain.persona import Persona
from app.domain.telefono import normalizar_telefono

from ..db import get_db, get_session_factory
from ..otp import enviar_en_segundo_plano, get_otp_sender
from ..rate_limit import RateLimiter, get_rate_limiter, rate_limit
from ..security import CUSTOMER_NOMBRE_SESSION_KEY, CUSTOMER_SESSION_KEY, current_customer
from ..templating import templates

router = APIRouter()

_MENSAJE_RATE_LIMIT = "Demasiados intentos. Espera un momento e inténtalo de nuevo."
# Issue 384: topes de códigos por teléfono -- 3 por hora y 6 por día.
_TOPES_POR_TELEFONO = (("otp_telefono_hora", 3, 60 * 60), ("otp_telefono_dia", 6, 24 * 60 * 60))
_MENSAJE_TOPE_POR_TELEFONO = (
    "Ya pediste varios códigos para este teléfono. Por seguridad, espera un rato antes de pedir otro "
    "(hasta una hora). Si necesitas ayuda, acércate a portería."
)


def _dentro_del_tope_por_telefono(limiter: RateLimiter, telefono_canonico: str) -> bool:
    """Cuenta este pedido en las dos ventanas; `False` si alguna ya se pasó. Fail-open, igual que `rate_limit`: si el
    contador falla, el login no se cae por eso."""
    permitido = True
    for nombre, limite, ventana in _TOPES_POR_TELEFONO:
        try:
            permitido = limiter.permitir(f"{nombre}:{telefono_canonico}", limite, ventana) and permitido
        except Exception:
            pass
    return permitido


@router.get("/otp", response_class=HTMLResponse)
def customer_login_form(request: Request, db: Session = Depends(get_db)):
    # Issue 262 (.scratch/pendientes-cliente, pedido explícito del cliente):
    # si ya hay sesión de cliente ACTIVA (mismo chequeo que `current_
    # customer`, sin lanzar 401 -- acá solo interesa saber si hay una
    # válida), redirige directo a /mis-datos en vez de mostrar el login de
    # nuevo -- mismo destino que `/otp/verificar` tras un login exitoso.
    raw = request.session.get(CUSTOMER_SESSION_KEY)
    if raw:
        try:
            persona_id = uuid.UUID(str(raw))
        except (ValueError, TypeError):
            persona_id = None
        if persona_id is not None and db.get(Persona, persona_id) is not None:
            return RedirectResponse("/mis-datos", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse("auth/customer_login.html", {"request": request})


@router.post("/otp/solicitar", response_class=HTMLResponse)
def customer_request_otp(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    sender: OtpSender = Depends(get_otp_sender),
    session_factory: sessionmaker = Depends(get_session_factory),
    permitido: bool = Depends(rate_limit("customer_request_otp", 5, 60)),
    limiter: RateLimiter = Depends(get_rate_limiter),
    telefono: str = Form(None),
):
    if not permitido:
        return templates.TemplateResponse(
            "auth/customer_login.html",
            {"request": request, "error": _MENSAJE_RATE_LIMIT},
            status_code=429,
        )

    # Estos dos casos son validación de formato (no de elegibilidad, que se
    # queda silenciosa a propósito) -- sí es seguro marcar el campo
    # específico, retroalimentación en vivo 2026-08-02.
    if not (telefono or "").strip():
        mensaje = "El teléfono es obligatorio."
        return templates.TemplateResponse(
            "auth/customer_login.html",
            {"request": request, "error": mensaje, "error_telefono": mensaje},
            status_code=400,
        )

    # Issue 384 (.scratch/pendientes-cliente): tope de códigos POR TELÉFONO (el de arriba es por IP), contado para
    # CUALQUIER teléfono -- sea cliente o no --, así el mensaje al llegar al tope no revela quién es cliente.
    try:
        telefono_canonico = normalizar_telefono(telefono)
    except ValueError:
        telefono_canonico = None
    if telefono_canonico is not None and not _dentro_del_tope_por_telefono(limiter, telefono_canonico):
        return templates.TemplateResponse(
            "auth/customer_login.html",
            {"request": request, "error": _MENSAJE_TOPE_POR_TELEFONO},
            status_code=429,
        )

    try:
        resultado = preparar_otp(db, telefono)
    except ValueError:
        mensaje = "Teléfono inválido."
        return templates.TemplateResponse(
            "auth/customer_login.html",
            {"request": request, "error": mensaje, "error_telefono": mensaje},
            status_code=400,
        )

    # `resultado` es None si el teléfono no es elegible (no existe, o no
    # tiene ningún paquete Recibido) -- se responde EXACTAMENTE igual que si
    # sí lo fuera, para no revelar elegibilidad. Solo se difiere el envío
    # real cuando sí hay algo que enviar.
    if resultado is not None:
        background_tasks.add_task(
            enviar_en_segundo_plano, sender, *resultado, session_factory=session_factory
        )

    return RedirectResponse(
        f"/otp/verificar?telefono={normalizar_telefono(telefono)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/otp/verificar", response_class=HTMLResponse)
def customer_verify_form(request: Request, telefono: str = None):
    return templates.TemplateResponse(
        "auth/customer_verify.html", {"request": request, "telefono": telefono or ""}
    )


@router.post("/otp/verificar")
def customer_verify_otp(
    request: Request,
    db: Session = Depends(get_db),
    telefono: str = Form(None),
    codigo: str = Form(None),
):
    def _error():
        return templates.TemplateResponse(
            "auth/customer_verify.html",
            {
                "request": request,
                "telefono": telefono or "",
                "error": "Código inválido o expirado.",
            },
            status_code=400,
        )

    if not (telefono or "").strip() or not (codigo or "").strip():
        return _error()

    try:
        persona = verify_otp(db, telefono, codigo)
    except ValueError:
        return _error()

    request.session[CUSTOMER_SESSION_KEY] = str(persona.id)
    request.session[CUSTOMER_NOMBRE_SESSION_KEY] = persona.nombre
    return RedirectResponse("/mis-datos", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/otp/salir")
def customer_logout(request: Request):
    # pop, no clear: no debe cerrar la sesión de staff si coexiste.
    request.session.pop(CUSTOMER_SESSION_KEY, None)
    request.session.pop(CUSTOMER_NOMBRE_SESSION_KEY, None)
    return RedirectResponse(
        "/otp", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/otp/perfil", response_class=HTMLResponse)
def customer_me(request: Request, persona: Persona = Depends(current_customer)):
    """Redirige a `/mis-datos` (issue 205, .scratch/pendientes-cliente) --
    los datos del cliente se editan ahí, esta ruta ya no tiene contenido
    propio (`auth/customer_me.html` queda intacta, sin caller)."""
    return RedirectResponse("/mis-datos", status_code=status.HTTP_303_SEE_OTHER)
