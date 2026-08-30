# -*- coding: utf-8 -*-
"""
Ruta `/anunciar` — anunciar un paquete (vista pública, sin privilegios).

Simplificada (Grupo 1 de `ajustes-post-referencia-funcional/REQUERIMIENTOS.md`):
el cliente solo declara Nombre + Teléfono + Términos y Condiciones — no elige
"a nombre de quién llega". El nombre declarado se guarda tal cual
(`Destinatario.declarado_por_cliente`); si no coincide con el nombre ya
registrado del Anunciante, el staff lo verá señalado en `/paquetes` y lo
resuelve desde `/announce` (rebanada aparte). Sin captura de guía del
transportador (la captura el staff al recibir).

Límite de anuncios activos por Teléfono (`.scratch/pendientes-cliente`,
grillado con el cliente): evita que un error o abuso dispare una ráfaga de
notificaciones SMS reales. Modelo de 2 umbrales sobre `contar_anunciados_
activos_de_telefono` (cuenta SOLO `ANUNCIADO`, la cola real):
  - 0 activos: se anuncia normal, sin interrupción.
  - 1..MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO - 1: pantalla intermedia
    ("ya tienes N, ¿quieres anunciar otro?") -- el cliente puede confirmar y
    seguir (`confirmar_multiple=1` en el resubmit). NUNCA menciona los
    códigos de acceso de esos anuncios existentes, solo el conteo.
  - >= MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO: tope duro, no hay confirmación
    que lo supere -- mismo espíritu que `MAX_OCUPANTES_ACTIVOS`.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.domain.notification_sender import NotificationSender
from app.domain.notificacion_service import preparar_notificacion
from app.domain.paquete import EstadoPaquete
from app.domain.paquete_service import (
    MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO,
    Destinatario,
    announce,
    contar_anunciados_activos_de_telefono,
)
from app.domain.telefono import normalizar_telefono

from ..config import public_base_url_relaxed
from ..db import get_db
from ..notifications import enviar_en_segundo_plano, get_notification_sender
from ..templating import templates

router = APIRouter()


@router.get("/anunciar", response_class=HTMLResponse)
def announce_form(request: Request):
    return templates.TemplateResponse("announce/form.html", {"request": request})


@router.post("/anunciar", response_class=HTMLResponse)
def announce_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    sender: NotificationSender = Depends(get_notification_sender),
    nombre: str = Form(None),
    telefono: str = Form(None),
    acepta_tyc: str = Form(None),
    confirmar_multiple: str = Form(None),
):
    # Valores para re-renderizar conservando lo que el usuario escribió.
    valores = {"nombre": nombre or "", "telefono": telefono or ""}

    def _error(mensaje: str, campo: str = None):
        # `campo` marca el input específico en rojo (retroalimentación en
        # vivo 2026-08-02: antes solo se veía el toast genérico arriba, sin
        # señalar cuál campo tenía el problema) -- `None` para errores sin
        # un campo natural al que anclarse (hoy no hay ninguno acá, pero el
        # parámetro se deja simétrico con el resto de las rutas).
        errores = {"error_nombre": None, "error_telefono": None, "error_tyc": None}
        if campo:
            errores[f"error_{campo}"] = mensaje
        return templates.TemplateResponse(
            "announce/form.html",
            {"request": request, "error": mensaje, **valores, **errores},
            status_code=400,
        )

    # --- Validación de campos obligatorios --------------------------------- #
    if not (nombre or "").strip():
        return _error("El nombre es obligatorio.", campo="nombre")
    if not (telefono or "").strip():
        return _error("El teléfono es obligatorio.", campo="telefono")
    if not acepta_tyc:
        return _error("Debes aceptar los Términos y Condiciones.", campo="tyc")

    # --- Límite de anuncios activos (ver docstring del módulo) -------------- #
    try:
        telefono_canonico = normalizar_telefono(telefono)
    except ValueError as exc:
        return _error(str(exc), campo="telefono")

    activos = contar_anunciados_activos_de_telefono(db, telefono_canonico)
    if activos >= MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO:
        return _error(
            f"Ya tienes el máximo de {MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO} "
            "paquetes anunciados pendientes de recibir -- espera a que al "
            "menos uno sea recibido antes de anunciar otro.",
            campo="telefono",
        )
    if activos >= 1 and not confirmar_multiple:
        return templates.TemplateResponse(
            "announce/confirmar_multiple.html",
            {"request": request, "activos": activos, **valores},
        )

    # --- Anunciar ----------------------------------------------------------- #
    try:
        paquete = announce(
            db, telefono, nombre, Destinatario.declarado_por_cliente(nombre)
        )
    except ValueError as exc:
        db.rollback()
        return _error(str(exc), campo="telefono")

    resultado = preparar_notificacion(db, paquete, EstadoPaquete.ANUNCIADO, public_base_url_relaxed())
    if resultado is not None:
        background_tasks.add_task(enviar_en_segundo_plano, sender, *resultado)

    return templates.TemplateResponse(
        "announce/confirmacion.html",
        {
            "request": request,
            "nombre": paquete.recipient_name,
            "telefono": paquete.announced_by_phone,
            "access_code": paquete.access_code,
            "snapshot_conjunto": paquete.snapshot_conjunto,
            "snapshot_torre": paquete.snapshot_torre,
            "snapshot_apartamento": paquete.snapshot_apartamento,
        },
    )
