# -*- coding: utf-8 -*-
"""
Ruta `/anunciar` — anunciar un paquete (vista pública, sin privilegios).

Simplificada (Grupo 1 de `ajustes-post-referencia-funcional/REQUERIMIENTOS.md`):
Teléfono + Términos y Condiciones son SIEMPRE obligatorios; el cliente no
elige "a nombre de quién llega". El campo Nombre es CONDICIONAL
(`.scratch/anunciar-atajo-telefono-conocido`, pedido explícito del cliente):
si el Teléfono ya tiene al menos 1 paquete `ENTREGADO` histórico ("cliente
conocido" -- reusa `es_primera_entrega_a_telefono`, issue 314, negada), se
anuncia directo con `Destinatario.yo_mismo()` (nombre YA REGISTRADO), sin
pedirlo nunca. Si no es conocido, el campo Nombre aparece (`mostrar_nombre`
en el contexto de la plantilla) y el flujo sigue igual que siempre --
`Destinatario.declarado_por_cliente(nombre)`, guardado tal cual; si no
coincide con el nombre ya registrado del Anunciante, el staff lo verá
señalado en `/paquetes` y lo resuelve desde `/announce` (rebanada aparte).
Sin captura de guía del transportador (la captura el staff al recibir).

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
  Aplica igual para el atajo de cliente conocido y para el flujo completo.
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.domain.notification_sender import NotificationSender
from app.domain.notificacion_service import preparar_notificacion
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_service import (
    MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO,
    Destinatario,
    announce,
    contar_anunciados_activos_de_telefono,
    es_primera_entrega_a_telefono,
)
from app.domain.telefono import normalizar_telefono

from ..config import public_base_url_relaxed
from ..db import get_db
from ..notifications import enviar_en_segundo_plano, get_notification_sender
from ..templating import templates

router = APIRouter()


@router.get("/anunciar", response_class=HTMLResponse)
def announce_form(request: Request):
    return templates.TemplateResponse(
        "announce/form.html",
        {"request": request, "mostrar_nombre": False, "acepta_tyc": False},
    )


@router.get("/anunciar/confirmacion", response_class=HTMLResponse)
def announce_confirmacion(request: Request, id: str, db: Session = Depends(get_db)):
    """Post/Redirect/Get -- bug real reportado en vivo: antes el POST de
    `/anunciar` renderizaba esta misma confirmación como respuesta directa,
    así que recargar la página reenviaba el formulario y anunciaba OTRO
    paquete con código nuevo. `announce_submit` ahora redirige acá con el
    `id` (UUID interno) del Paquete recién creado; recargar este GET solo
    vuelve a buscarlo, nunca crea nada.

    Se usa `id`, NO `access_code`, como llave de esta URL (pedido explícito
    del cliente: el código de acceso nunca debe ser visible/circular en una
    vista pública no autenticada -- ni en pantalla ni en la propia URL, que
    queda en historial del navegador y en logs de acceso del servidor. El
    código real solo llega por SMS/WhatsApp/Email, vía `preparar_notificacion`
    en `announce_submit`). `id` no sirve como llave en ningún otro endpoint
    público (`/consultar` solo busca por `access_code`/`guide_number`), así
    que no reemplaza ni debilita esa llave.

    Id inexistente/inválido (URL manipulada a mano) -> de vuelta al
    formulario, sin más explicación (mismo criterio que cualquier otro
    estado imposible de esta vista pública)."""
    try:
        paquete_id = uuid.UUID(id)
    except (ValueError, TypeError, AttributeError):
        return RedirectResponse("/anunciar", status_code=303)
    paquete = db.query(Paquete).filter(Paquete.id == paquete_id).one_or_none()
    if paquete is None:
        return RedirectResponse("/anunciar", status_code=303)
    return templates.TemplateResponse(
        "announce/confirmacion.html",
        {
            "request": request,
            "nombre": paquete.recipient_name,
            "telefono": paquete.announced_by_phone,
            "snapshot_conjunto": paquete.snapshot_conjunto,
            "snapshot_torre": paquete.snapshot_torre,
            "snapshot_apartamento": paquete.snapshot_apartamento,
        },
    )


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
    mostrar_nombre: str = Form(None),
):
    # Valores para re-renderizar conservando lo que el usuario escribió --
    # incluye `acepta_tyc` (bug real reportado en vivo: el checkbox NUNCA
    # se preservaba en un re-render con error, así que pedir el Nombre tras
    # aceptar Términos lo mostraba destildado otra vez, aunque la
    # aceptación ya había pasado la validación de arriba).
    valores = {
        "nombre": nombre or "",
        "telefono": telefono or "",
        "acepta_tyc": bool(acepta_tyc),
    }

    # "Pegajoso" una vez que el campo Nombre aparece (ver `mostrar_nombre`
    # oculto en el template): sigue en `True` en cualquier resubmit
    # posterior de ESTA misma vuelta, aunque el cliente tropiece con OTRO
    # campo (ej. destildó Términos) antes de llegar a escribir su nombre --
    # nunca "desaparece" un campo que el cliente ya empezó a llenar, ni
    # aunque todavía no haya tecleado nada en él.
    mostrar_nombre = bool((nombre or "").strip()) or bool(mostrar_nombre)

    def _error(mensaje: str, campo: str = None):
        # `campo` marca el input específico en rojo (retroalimentación en
        # vivo 2026-08-02: antes solo se veía el toast genérico arriba, sin
        # señalar cuál campo tenía el problema) -- `None` para errores sin
        # un campo natural al que anclarse (hoy no hay ninguno acá, pero el
        # parámetro se deja simétrico con el resto de las rutas). Cierra
        # sobre `mostrar_nombre` de más arriba -- ningún call site puede
        # "olvidarse" de pasarlo y ocultar por error un campo ya revelado.
        errores = {"error_nombre": None, "error_telefono": None, "error_tyc": None}
        if campo:
            errores[f"error_{campo}"] = mensaje
        return templates.TemplateResponse(
            "announce/form.html",
            {
                "request": request,
                "error": mensaje,
                "mostrar_nombre": mostrar_nombre,
                **valores,
                **errores,
            },
            status_code=400,
        )

    # --- Validación de campos SIEMPRE obligatorios --------------------------- #
    if not (telefono or "").strip():
        return _error("El teléfono es obligatorio.", campo="telefono")
    if not acepta_tyc:
        return _error("Debes aceptar los Términos y Condiciones.", campo="tyc")

    try:
        telefono_canonico = normalizar_telefono(telefono)
    except ValueError as exc:
        return _error(str(exc), campo="telefono")

    # --- Atajo de cliente conocido (.scratch/anunciar-atajo-telefono-
    # conocido, ver docstring del módulo) ------------------------------------ #
    conocido = not es_primera_entrega_a_telefono(db, telefono_canonico)
    if not conocido and not mostrar_nombre:
        mostrar_nombre = True
        return _error("Ingresa tu nombre para continuar.", campo="nombre")

    # --- Límite de anuncios activos (ver docstring del módulo) -------------- #
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
    destinatario = (
        Destinatario.yo_mismo() if conocido else Destinatario.declarado_por_cliente(nombre)
    )
    try:
        paquete = announce(db, telefono, nombre, destinatario)
    except ValueError as exc:
        db.rollback()
        return _error(str(exc), campo="telefono")

    resultado = preparar_notificacion(db, paquete, EstadoPaquete.ANUNCIADO, public_base_url_relaxed())
    if resultado is not None:
        background_tasks.add_task(enviar_en_segundo_plano, sender, *resultado)

    # Post/Redirect/Get (bug real reportado en vivo): antes esta respuesta
    # renderizaba `announce/confirmacion.html` directo, así que recargar la
    # página reenviaba el POST y anunciaba OTRO paquete. Redirige a
    # `GET /anunciar/confirmacion` (arriba), que reconstruye la misma
    # pantalla a partir del `id` -- recargar el GET nunca crea nada nuevo.
    # `id`, no `access_code`, a propósito (ver docstring de
    # `announce_confirmacion`): el código nunca debe circular en una vista
    # pública ni en su URL.
    return RedirectResponse(f"/anunciar/confirmacion?id={paquete.id}", status_code=303)
