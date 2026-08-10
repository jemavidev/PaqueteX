# -*- coding: utf-8 -*-
"""
Ruta `/announce` — identificar y anunciar un paquete, rápido (staff).

Rediseño completo (`.scratch/announce-rapido`, ticket 04): reemplaza el
formulario viejo de 3 bloques (Apartamento/Residentes/Anunciar,
desconectados entre sí) por UN campo de texto único con detección de
formato y resolución en vivo. "Declarar apartamento sin anunciar nada"
se retiró de esta ruta -- vive en `/residentes` (enlace visible desde acá).

Gated por `current_staff` (cualquier rol), igual que antes.

Detección de formato del campo único (re-aplicada SIEMPRE en el servidor --
`/announce/identificar` es la única fuente de verdad, el cliente no
clasifica nada, solo dispara la petición tras un debounce):
  - empieza en `3`, todo dígitos → Teléfono.
  - empieza con una letra → usuario de WhatsApp.
  - empieza en `0`/`1`, todo dígitos → Torre+Apartamento (ticket 05,
    todavía no resuelve nada acá).
  - cualquier otro caso → nada que resolver.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.domain.notification_sender import NotificationSender
from app.domain.notificacion_service import preparar_notificacion
from app.domain.paquete import EstadoPaquete
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona_service import buscar_persona_por_telefono, buscar_persona_por_whatsapp
from app.domain.usuario import Usuario

from ..db import get_db
from ..notifications import enviar_en_segundo_plano, get_notification_sender
from ..security import current_staff
from ..templating import templates

router = APIRouter()


def _clasificar(valor: str) -> str:
    """'telefono' | 'whatsapp' | 'torre_apto' | 'ninguno' -- ver docstring
    del módulo. Única función que decide esto; tanto `/announce/identificar`
    como `/announce` (POST) la usan, para que nunca diverjan.

    Exige el valor COMPLETO, no cualquier prefijo -- encontrado en
    code-review antes de desplegar: sin este mínimo, el primer dígito/letra
    tecleado ya clasificaba como candidato, disparando de inmediato la
    tarjeta "no encontramos a nadie" (con foco automático al campo Nombre)
    en CADA tecleo mientras el staff todavía estaba escribiendo -- le robaba
    el foco al campo principal a mitad de un número de teléfono real. Un
    celular colombiano son SIEMPRE 10 dígitos (`normalizar_telefono`), así
    que ese es el umbral exacto para Teléfono; WhatsApp no tiene largo fijo,
    así que se usa el mínimo de `WHATSAPP_USUARIO_RE` (3) -- reduce el
    problema bastante aunque no lo elimina del todo para nombres de usuario
    largos (por eso el Nombre del fragmento YA NO lleva `autofocus`, ver
    `_identificar.html`: ese es el fix que cierra el caso completo)."""
    valor = (valor or "").strip()
    if not valor:
        return "ninguno"
    primero = valor[0]
    if primero == "3" and valor.isdigit():
        return "telefono" if len(valor) == 10 else "ninguno"
    if primero in ("0", "1") and valor.isdigit():
        return "torre_apto"
    if primero.isalpha():
        return "whatsapp" if len(valor) >= 3 else "ninguno"
    return "ninguno"


@router.get("/announce", response_class=HTMLResponse)
def announce_form(request: Request, staff: Usuario = Depends(current_staff)):
    return templates.TemplateResponse("announce_new/form.html", {"request": request, "staff": staff})


@router.get("/announce/identificar", response_class=HTMLResponse)
def announce_identificar(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
):
    tipo = _clasificar(q)
    if tipo not in ("telefono", "whatsapp"):
        # Torre+Apartamento (ticket 05) y "ninguno" -- nada que mostrar todavía.
        return HTMLResponse("")

    persona = (
        buscar_persona_por_telefono(db, q)
        if tipo == "telefono"
        else buscar_persona_por_whatsapp(db, q)
    )
    return templates.TemplateResponse(
        "announce_new/_identificar.html",
        {"request": request, "tipo": tipo, "valor": q, "persona": persona},
    )


@router.post("/announce", response_class=HTMLResponse)
def announce_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    sender: NotificationSender = Depends(get_notification_sender),
    telefono: str = Form(None),
    whatsapp_usuario: str = Form(None),
    nombre: str = Form(None),
):
    def _error(mensaje: str, valor_q: str = None):
        # Repuebla el campo principal con lo ya identificado (issue
        # encontrado en code-review) -- sin esto, cualquier error de
        # validación borraba el Teléfono/WhatsApp que el staff ya había
        # resuelto, obligando a retipearlo desde cero. No repuebla el
        # fragmento de abajo (Anunciar/Recibir) -- toca al staff volver a
        # tocar el campo para que la búsqueda en vivo lo re-resuelva.
        contexto = {"request": request, "staff": staff, "error": mensaje}
        if valor_q:
            contexto["valor_q"] = valor_q
        return templates.TemplateResponse(
            "announce_new/form.html", contexto, status_code=400
        )

    tiene_telefono = bool((telefono or "").strip())
    tiene_whatsapp = bool((whatsapp_usuario or "").strip())
    valor_original = telefono if tiene_telefono else (whatsapp_usuario if tiene_whatsapp else None)

    if tiene_telefono and tiene_whatsapp:
        # No debería pasar viniendo del fragmento de /announce/identificar
        # (siempre fija exactamente uno) -- se corta acá ANTES del lookup de
        # "ya_registrada" de abajo, que necesita saber cuál de los dos usar
        # sin ambigüedad. El caso "ninguno de los dos" no necesita este
        # guard: cae directo al ValueError de `announce()` más abajo, sin
        # duplicar ese mismo chequeo acá.
        return _error("Identifica a la persona antes de anunciar.")

    if tiene_telefono or tiene_whatsapp:
        ya_registrada = (
            buscar_persona_por_telefono(db, telefono)
            if tiene_telefono
            else buscar_persona_por_whatsapp(db, whatsapp_usuario)
        )
        if ya_registrada is None and not (nombre or "").strip():
            return _error("Escribe el nombre para registrar a esta persona.", valor_original)

    try:
        paquete = announce(
            db,
            anunciante_telefono=telefono if tiene_telefono else None,
            anunciante_nombre=nombre,
            destinatario=Destinatario.yo_mismo(),
            staff_actor=staff,
            anunciante_whatsapp=whatsapp_usuario if tiene_whatsapp else None,
        )
    except ValueError as exc:
        return _error(str(exc), valor_original)

    resultado = preparar_notificacion(db, paquete, EstadoPaquete.ANUNCIADO)
    if resultado is not None:
        background_tasks.add_task(enviar_en_segundo_plano, sender, *resultado)

    return templates.TemplateResponse(
        "announce_new/form.html",
        {"request": request, "staff": staff, "paquete_creado": paquete},
    )
