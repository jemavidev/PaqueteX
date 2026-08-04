# -*- coding: utf-8 -*-
"""
Ruta `/mis-datos` — tablero de autoedición del cliente.

Protegida por `current_customer`. El residente edita sus datos ampliables
(`update_datos_personales`, actualización parcial) y puede **declarar su
Apartamento** — reutilizando `get_or_create_apartamento` + `declare_unit` sin
cambios, pasando UN solo miembro (él mismo): es la forma correcta de "declarar a
propósito" desde esta vista (§6.4), no un "a nombre de" casual.

Validación "todo o nada por request": cualquier error (email inválido, o
Apartamento con campos incompletos) hace `rollback` antes de re-mostrar el
formulario, de modo que ningún cambio del envío queda a medias.

La preferencia de notificaciones es una matriz Canal × Evento (Grupo 13,
Ronda 2) — 16 checkboxes con nombre `pref_{CANAL}_{EVENTO}`, leídos vía
`request.form()` (mismo patrón que `announce_new.py` para formularios con
forma variable, `Form(...)` no da para 16 campos declarativos limpios).

Gestión de Ocupantes (.scratch/mis-datos, ticket 03): si la Persona logueada
es el Ocupante PRINCIPAL de un Apartamento, ve y gestiona el resto de
Ocupantes de esa unidad (crear, asociar/desvincular teléfono, dar de baja) —
`_ocupante_gestionable_por` exige esa condición en cada acción, 403 si no
aplica. Un Ocupante no-principal (ticket 05) NO ve este bloque.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.domain.apartamento import Apartamento
from app.domain.apartamento_service import declare_unit, get_or_create_apartamento
from app.domain.ocupante import Ocupante
from app.domain.ocupante_service import (
    MAX_OCUPANTES_ACTIVOS,
    agregar_ocupante,
    asociar_telefono_a_ocupante,
    dar_de_baja_ocupante,
    desvincular_telefono_ocupante,
    editar_telefono_ocupante,
    listar_ocupantes,
    ocupante_activo_de_persona,
    ocupante_de_persona,
    promover_a_principal,
)
from app.domain.notificacion_service import es_cliente_verificado
from app.domain.persona import Persona
from app.domain.persona_service import (
    cambiar_telefono_propio,
    set_autoriza_recepcion_automatica,
    update_datos_personales,
)
from app.domain.preferencia_notificacion import CanalNotificacion
from app.domain.preferencia_notificacion_service import (
    EVENTOS,
    guardar_matriz_preferencias,
    matriz_preferencias,
)

from ..db import get_db
from ..security import CUSTOMER_NOMBRE_SESSION_KEY, CUSTOMER_SESSION_KEY, current_customer
from ..templating import templates

_CANALES_SIN_PROVEEDOR = {CanalNotificacion.LLAMADA, CanalNotificacion.WHATSAPP}

router = APIRouter()

_ETIQUETA_CANAL = {
    CanalNotificacion.SMS: "SMS",
    CanalNotificacion.EMAIL: "Email",
    CanalNotificacion.LLAMADA: "Llamada",
    CanalNotificacion.WHATSAPP: "WhatsApp",
}


def _blank_to_none(valor: str):
    valor = (valor or "").strip()
    return valor or None


def _apartamento_actual(db: Session, persona: Persona):
    if persona.apartamento_actual_id is None:
        return None
    return db.get(Apartamento, persona.apartamento_actual_id)


def _contexto_base(db: Session, persona: Persona) -> dict:
    """Contexto común a la vista GET y a cualquier re-render tras un error —
    incluye el rol de la Persona (principal, Ocupante no-principal, o ninguno)
    y, si aplica, el roster de Ocupantes de su Apartamento. Un Ocupante
    no-principal (ticket 05) ve el MISMO roster que el principal, pero de
    solo lectura (nombre y teléfono de todos -- sin restricción de
    visibilidad dentro del propio apartamento, a diferencia de la gestión,
    que sigue siendo exclusiva del principal)."""
    mi_ocupante = ocupante_activo_de_persona(db, persona.id)
    es_principal = mi_ocupante is not None and mi_ocupante.es_principal
    ocupantes = []
    personas_telefono = {}
    if mi_ocupante is not None:
        apto_ocupante = db.get(Apartamento, mi_ocupante.apartamento_id)
        ocupantes = listar_ocupantes(db, apto_ocupante)
        # Teléfonos de los Ocupantes-con-teléfono del roster, para que la
        # vista de un Ocupante no-principal (ticket 05, "ve todo") pueda
        # mostrarlos sin que la plantilla haga sus propias consultas.
        ids_persona = [o.persona_id for o in ocupantes if o.persona_id is not None]
        if ids_persona:
            personas_telefono = {
                p.id: p.telefono
                for p in db.query(Persona).filter(Persona.id.in_(ids_persona)).all()
            }
    return {
        "persona": persona,
        "apartamento": _apartamento_actual(db, persona),
        "canales": list(CanalNotificacion),
        "canales_sin_proveedor": _CANALES_SIN_PROVEEDOR,
        "etiqueta_canal": _ETIQUETA_CANAL,
        "eventos": EVENTOS,
        "matriz": matriz_preferencias(db, persona.id),
        "es_principal_de_apartamento": es_principal,
        "es_ocupante_no_principal": mi_ocupante is not None and not es_principal,
        "ocupantes": ocupantes,
        "personas_telefono": personas_telefono,
        "limite_ocupantes": MAX_OCUPANTES_ACTIVOS,
    }


def _gate_no_verificado(request: Request, db: Session, persona: Persona) -> HTMLResponse | None:
    """`None` si `persona` puede ver/editar `/mis-datos` (y sus sub-rutas de
    Ocupantes); si no, la pantalla informativa (.scratch/mis-datos, ticket
    11) en vez de lo que sea que la ruta iba a hacer. Nunca se llama desde
    `/otp/solicitar` ni `/otp/verificar` (ver `es_cliente_verificado`)."""
    if es_cliente_verificado(db, persona):
        return None
    return templates.TemplateResponse(
        "customer/no_verificado.html", {"request": request}, status_code=403
    )


def _render_con_error(
    request: Request, db: Session, persona: Persona, mensaje: str
) -> HTMLResponse:
    db.rollback()  # "todo o nada": deshace cualquier mutación de este request
    contexto = _contexto_base(db, persona)
    contexto["request"] = request
    contexto["error"] = mensaje
    return templates.TemplateResponse(
        "customer/verify.html", contexto, status_code=400
    )


def _ocupante_gestionable_por(db: Session, persona: Persona, ocupante_id: str) -> Ocupante:
    """El Ocupante `ocupante_id`, solo si `persona` es el Ocupante PRINCIPAL
    activo de ese mismo Apartamento — 404 si no existe, 403 si `persona` no
    tiene permiso sobre él."""
    ocupante = db.get(Ocupante, ocupante_id)
    if ocupante is None:
        raise HTTPException(status_code=404)
    mi_ocupante = ocupante_activo_de_persona(db, persona.id)
    if (
        mi_ocupante is None
        or not mi_ocupante.es_principal
        or mi_ocupante.apartamento_id != ocupante.apartamento_id
    ):
        raise HTTPException(status_code=403)
    return ocupante


@router.get("/mis-datos", response_class=HTMLResponse)
def customer_verify_form(
    request: Request,
    persona: Persona = Depends(current_customer),
    db: Session = Depends(get_db),
):
    gate = _gate_no_verificado(request, db, persona)
    if gate is not None:
        return gate
    contexto = _contexto_base(db, persona)
    contexto["request"] = request
    contexto["guardado"] = request.query_params.get("guardado") == "1"
    contexto["ocupante_guardado"] = request.query_params.get("ocupante_guardado") == "1"
    return templates.TemplateResponse("customer/verify.html", contexto)


@router.post("/mis-datos", response_class=HTMLResponse)
async def customer_verify_submit(
    request: Request,
    persona: Persona = Depends(current_customer),
    db: Session = Depends(get_db),
):
    gate = _gate_no_verificado(request, db, persona)
    if gate is not None:
        return gate
    form = await request.form()
    nombre = form.get("nombre")
    email = form.get("email")
    telefono_nuevo = _blank_to_none(form.get("telefono"))
    torre = form.get("torre")
    apartamento = form.get("apartamento")

    def _error(mensaje: str, campos: list[str] = None):
        db.rollback()  # "todo o nada": deshace cualquier mutación de este request
        contexto = _contexto_base(db, persona)
        contexto.update(
            {
                "request": request,
                "error": mensaje,
                "error_email": mensaje if "email" in (campos or []) else None,
                "error_telefono": mensaje if "telefono" in (campos or []) else None,
                "error_torre": mensaje if "torre" in (campos or []) else None,
                "error_apartamento": mensaje if "apartamento" in (campos or []) else None,
            }
        )
        return templates.TemplateResponse(
            "customer/verify.html", contexto, status_code=400
        )

    # Un Ocupante no-principal (ticket 05) ve Torre/Apartamento/Conjunto de
    # SOLO LECTURA -- no gestiona su Apartamento, eso es del principal. Se
    # ignora POR COMPLETO cualquier valor que venga en el formulario (el
    # campo ni se muestra habilitado en su vista, pero el servidor no confía
    # en eso solo) tratando `partes_apto` como si nada se hubiese enviado,
    # para no disparar ninguna validación de Apartamento que no le aplica.
    mi_ocupante_actual = ocupante_activo_de_persona(db, persona.id)
    es_ocupante_no_principal = (
        mi_ocupante_actual is not None and not mi_ocupante_actual.es_principal
    )

    if es_ocupante_no_principal:
        conjunto_v = torre_v = apartamento_v = None
    else:
        # El Conjunto NUNCA lo escribe el cliente (Grupo 12, Ronda 2) — solo
        # el staff lo asigna. Se toma tal cual del apartamento ya asignado,
        # si hay alguno; nunca de lo que venga en el formulario.
        apartamento_existente = _apartamento_actual(db, persona)
        conjunto_v = apartamento_existente.conjunto if apartamento_existente else None
        torre_v = _blank_to_none(torre)
        apartamento_v = _blank_to_none(apartamento)

        if (torre_v or apartamento_v) and conjunto_v is None:
            # Sin campo que marcar (retroalimentación en vivo 2026-08-02): el
            # bloque Torre/Apartamento ni siquiera se renderiza en este caso (el
            # template solo lo muestra si `apartamento` ya existe) -- el toast
            # es la única vía posible acá.
            return _error(
                "Tu conjunto todavía no ha sido asignado por el staff — "
                "avísales en portería antes de declarar torre y apartamento."
            )

    partes_apto = [conjunto_v, torre_v, apartamento_v]
    if any(partes_apto) and not all(partes_apto):
        campos_vacios = [c for c, v in [("torre", torre_v), ("apartamento", apartamento_v)] if not v]
        return _error("Completa Torre y Apartamento, o deja los dos vacíos.", campos=campos_vacios)

    try:
        update_datos_personales(
            db,
            persona,
            nombre=_blank_to_none(nombre),
            email=_blank_to_none(email),
        )
    except ValueError as exc:
        return _error(str(exc), campos=["email"])

    # Refresca el nombre cacheado en sesión (ver NOMBRE_SESSION_KEY) para que
    # el avatar del header no quede mostrando el nombre viejo hasta el
    # próximo login -- mismo dato, misma sesión, costo de mantenerlo al día
    # es una línea.
    request.session[CUSTOMER_NOMBRE_SESSION_KEY] = persona.nombre

    # Checkbox (ticket 12): presente (marcado) = True, igual que la matriz de
    # preferencias -- su ausencia SÍ significa "desactivar", a diferencia de
    # nombre/email (cuya ausencia es "no tocar").
    set_autoriza_recepcion_automatica(
        db, persona, form.get("autoriza_recepcion_automatica") is not None
    )

    # Matriz de checkboxes: presente (marcado) = activo. Distinto del resto de
    # campos, cuya ausencia significa "no tocar" — la matriz completa siempre
    # representa su estado actual (como cualquier checkbox HTML). Llamada y
    # WhatsApp no tienen proveedor conectado (pedido del cliente,
    # `.scratch/pendientes-cliente/issues/36`) -- la plantilla ya los muestra
    # deshabilitados, pero el servidor tampoco confía solo en eso.
    activos = {
        (canal.value, evento.value)
        for canal in CanalNotificacion
        for evento in EVENTOS
        if canal not in _CANALES_SIN_PROVEEDOR
        and form.get(f"pref_{canal.value}_{evento.value}") is not None
    }
    guardar_matriz_preferencias(db, persona.id, activos)

    if all(partes_apto):
        apto = get_or_create_apartamento(db, conjunto_v, torre_v, apartamento_v)
        # Un solo miembro (el propio cliente): declaración a propósito, no agrupa
        # a nadie más que a sí mismo.
        declare_unit(db, apto, [(persona.telefono, persona.nombre)])
        # Además de apartamento_actual_id (arriba), esta declaración también
        # alimenta el padrón de Ocupantes -- ticket 01 de .scratch/mis-datos:
        # el primer Ocupante de un Apartamento queda principal automáticamente
        # (agregar_ocupante). Guardia de idempotencia: reenviar el mismo
        # Apartamento sin cambios (p.ej. re-Guardar sin tocar Torre/Apto) no
        # debe crear un Ocupante duplicado.
        if ocupante_de_persona(db, apto, persona.id) is None:
            # Un teléfono solo puede ser Ocupante activo de un Apartamento a
            # la vez (ticket 02) -- si esta Persona ya lo es de OTRO, moverse
            # exige primero liberar el anterior (mismo verbo "dar de baja" que
            # cualquier desvinculación; falla si era principal con otros
            # Ocupantes activos todavía dependiendo de ella).
            mi_ocupante_actual = ocupante_activo_de_persona(db, persona.id)
            if mi_ocupante_actual is not None:
                try:
                    dar_de_baja_ocupante(db, mi_ocupante_actual)
                except ValueError:
                    # Mensaje propio (`.scratch/pendientes-cliente/issues/38`)
                    # -- el de `dar_de_baja_ocupante` habla de "darte de baja",
                    # un concepto que quien solo quería corregir su Torre o
                    # Apartamento nunca invocó a propósito.
                    return _error(
                        "No puedes cambiar de Torre/Apartamento mientras tengas "
                        "otros Ocupantes activos en tu unidad actual -- "
                        "promueve a alguno como principal primero, o dales de "
                        "baja a todos antes de mudarte.",
                        campos=["torre", "apartamento"],
                    )
            agregar_ocupante(db, apto, persona.nombre, persona.telefono)

    # Teléfono propio (pedido del cliente,
    # `.scratch/pendientes-cliente/issues/35`): se procesa AL FINAL, después
    # de que todo lo demás ya se aplicó usando el teléfono VIEJO de forma
    # consistente -- un cambio exitoso cierra la sesión y exige una
    # verificación OTP nueva al número nuevo (confirma que de verdad lo
    # controla, no solo que lo escribió).
    if telefono_nuevo is not None:
        try:
            anterior = persona.telefono
            cambiar_telefono_propio(db, persona, telefono_nuevo)
        except ValueError as exc:
            return _error(str(exc), campos=["telefono"])
        if persona.telefono != anterior:
            request.session.pop(CUSTOMER_SESSION_KEY, None)
            request.session.pop(CUSTOMER_NOMBRE_SESSION_KEY, None)
            return RedirectResponse("/otp?telefono_actualizado=1", status_code=303)

    return RedirectResponse("/mis-datos?guardado=1", status_code=303)


@router.post("/mis-datos/ocupantes", response_class=HTMLResponse)
async def customer_ocupante_crear(
    request: Request,
    persona: Persona = Depends(current_customer),
    db: Session = Depends(get_db),
):
    gate = _gate_no_verificado(request, db, persona)
    if gate is not None:
        return gate

    mi_ocupante = ocupante_activo_de_persona(db, persona.id)
    if mi_ocupante is None or not mi_ocupante.es_principal:
        raise HTTPException(status_code=403)

    form = await request.form()
    nombre = _blank_to_none(form.get("nombre"))
    telefono = _blank_to_none(form.get("telefono"))
    if not nombre:
        return _render_con_error(request, db, persona, "El nombre del Ocupante es obligatorio.")

    apto = db.get(Apartamento, mi_ocupante.apartamento_id)
    try:
        agregar_ocupante(db, apto, nombre, telefono)
    except ValueError as exc:
        return _render_con_error(request, db, persona, str(exc))

    return RedirectResponse("/mis-datos?ocupante_guardado=1", status_code=303)


@router.post("/mis-datos/ocupantes/{ocupante_id}/telefono", response_class=HTMLResponse)
async def customer_ocupante_asociar_telefono(
    ocupante_id: str,
    request: Request,
    persona: Persona = Depends(current_customer),
    db: Session = Depends(get_db),
):
    gate = _gate_no_verificado(request, db, persona)
    if gate is not None:
        return gate

    ocupante = _ocupante_gestionable_por(db, persona, ocupante_id)
    form = await request.form()
    telefono = _blank_to_none(form.get("telefono"))
    if not telefono:
        return _render_con_error(request, db, persona, "El teléfono es obligatorio.")

    try:
        if ocupante.persona_id is None:
            asociar_telefono_a_ocupante(db, ocupante, telefono)
        else:
            # Editar un teléfono YA asociado (pedido del cliente,
            # `.scratch/pendientes-cliente/issues/35`) -- mismo formulario,
            # la rama la decide si el Ocupante ya tenía uno o no.
            editar_telefono_ocupante(db, ocupante, telefono)
    except ValueError as exc:
        return _render_con_error(request, db, persona, str(exc))

    return RedirectResponse("/mis-datos?ocupante_guardado=1", status_code=303)


@router.post(
    "/mis-datos/ocupantes/{ocupante_id}/desvincular-telefono", response_class=HTMLResponse
)
def customer_ocupante_desvincular_telefono(
    ocupante_id: str,
    request: Request,
    persona: Persona = Depends(current_customer),
    db: Session = Depends(get_db),
):
    gate = _gate_no_verificado(request, db, persona)
    if gate is not None:
        return gate

    ocupante = _ocupante_gestionable_por(db, persona, ocupante_id)
    try:
        desvincular_telefono_ocupante(db, ocupante)
    except ValueError as exc:
        return _render_con_error(request, db, persona, str(exc))

    return RedirectResponse("/mis-datos?ocupante_guardado=1", status_code=303)


@router.post("/mis-datos/ocupantes/{ocupante_id}/baja", response_class=HTMLResponse)
def customer_ocupante_dar_de_baja(
    ocupante_id: str,
    request: Request,
    persona: Persona = Depends(current_customer),
    db: Session = Depends(get_db),
):
    gate = _gate_no_verificado(request, db, persona)
    if gate is not None:
        return gate

    ocupante = _ocupante_gestionable_por(db, persona, ocupante_id)
    try:
        dar_de_baja_ocupante(db, ocupante)
    except ValueError as exc:
        return _render_con_error(request, db, persona, str(exc))

    return RedirectResponse("/mis-datos?ocupante_guardado=1", status_code=303)


@router.post("/mis-datos/ocupantes/{ocupante_id}/promover", response_class=HTMLResponse)
def customer_ocupante_promover(
    ocupante_id: str,
    request: Request,
    persona: Persona = Depends(current_customer),
    db: Session = Depends(get_db),
):
    """Promueve a `ocupante_id` como nuevo principal de su Apartamento —
    wiring de ruta/UI sobre `promover_a_principal` (.scratch/mis-datos,
    ticket 04), que ya exige teléfono y degrada al principal anterior."""
    gate = _gate_no_verificado(request, db, persona)
    if gate is not None:
        return gate

    ocupante = _ocupante_gestionable_por(db, persona, ocupante_id)
    try:
        promover_a_principal(db, ocupante)
    except ValueError as exc:
        return _render_con_error(request, db, persona, str(exc))

    return RedirectResponse("/mis-datos?ocupante_guardado=1", status_code=303)


@router.post("/mis-datos/ocupantes/salir", response_class=HTMLResponse)
def customer_ocupante_salir(
    request: Request,
    persona: Persona = Depends(current_customer),
    db: Session = Depends(get_db),
):
    """Autoservicio (.scratch/mis-datos, ticket 05): el propio Ocupante se da
    de baja de su Apartamento -- a diferencia de las demás acciones, opera
    sobre el Ocupante del que llama, no uno elegido por id (no hace falta
    `_ocupante_gestionable_por`: cualquiera puede darse de baja a sí mismo,
    sea principal -solo si es el último activo- o no)."""
    gate = _gate_no_verificado(request, db, persona)
    if gate is not None:
        return gate

    mi_ocupante = ocupante_activo_de_persona(db, persona.id)
    if mi_ocupante is None:
        raise HTTPException(status_code=404)
    try:
        dar_de_baja_ocupante(db, mi_ocupante)
    except ValueError as exc:
        return _render_con_error(request, db, persona, str(exc))

    return RedirectResponse("/mis-datos?ocupante_guardado=1", status_code=303)
