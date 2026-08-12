# -*- coding: utf-8 -*-
"""
Ruta `/mis-datos` — tablero de autoedición del cliente.

Protegida por `current_customer`. El residente edita sus datos ampliables
(`update_datos_personales`, actualización parcial). Torre/Apartamento/Conjunto
son de **solo lectura** acá (`.scratch/pendientes-cliente`, ajuste posterior a
`apartamento-catalogo-confirmacion`): la asignación la hace exclusivamente el
personal de Papyrus desde `/residentes/{id}` — el residente ya no puede
declarar ni cambiar su propia unidad.

Validación "todo o nada por request": cualquier error (email inválido, etc.)
hace `rollback` antes de re-mostrar el formulario, de modo que ningún cambio
del envío queda a medias.

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

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.apartamento import Apartamento
from app.domain.configuracion_conjunto_service import obtener_nombre_conjunto
from app.domain.contacto import clasificar_contacto
from app.domain.ocupante import Ocupante
from app.domain.ocupante_service import (
    MAX_OCUPANTES_ACTIVOS,
    agregar_ocupante,
    asociar_telefono_a_ocupante,
    asociar_whatsapp_a_ocupante,
    confirmar_ocupante,
    dar_de_baja_ocupante,
    desvincular_telefono_ocupante,
    desvincular_whatsapp_ocupante,
    editar_telefono_ocupante,
    editar_whatsapp_ocupante,
    listar_ocupantes,
    ocupante_activo_de_persona,
    promover_a_principal,
)
from app.domain.notificacion_service import es_cliente_verificado
from app.domain.persona import Persona
from app.domain.persona_service import (
    cambiar_telefono_propio,
    desvincular_telefono_propio,
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


def _hay_otro_ocupante_activo(db: Session, apartamento_id, ocupante_id) -> bool:
    return (
        db.query(Ocupante)
        .filter(
            Ocupante.apartamento_id == apartamento_id,
            Ocupante.id != ocupante_id,
            Ocupante.desvinculado_en.is_(None),
        )
        .first()
        is not None
    )


def _contexto_base(db: Session, persona: Persona) -> dict:
    """Contexto común a la vista GET y a cualquier re-render tras un error —
    incluye el rol de la Persona (principal, Ocupante no-principal, o ninguno)
    y, si aplica, el roster de Ocupantes de su Apartamento. Un Ocupante
    no-principal (ticket 05) ve el MISMO roster que el principal, pero de
    solo lectura (nombre y teléfono de todos -- sin restricción de
    visibilidad dentro del propio apartamento, a diferencia de la gestión,
    que sigue siendo exclusiva del principal).

    Confirmación (`.scratch/apartamento-catalogo-confirmacion`, ticket 08):
    "no soy principal" YA NO equivale a "soy de solo lectura" -- un Ocupante
    recién auto-declarado (pending, único en su Apartamento) debe seguir
    viendo el formulario editable de siempre (nadie más lo gestiona
    todavía), no el mensaje de "esto lo gestiona el principal de tu unidad",
    que no tiene sentido cuando ese principal ni existe. La distinción real
    NO es "¿ya hay un principal confirmado?" (un segundo Ocupante agregado
    por alguien más, ambos pending, igual debe quedar de solo lectura -- lo
    agregó otra persona, no se auto-declaró) sino "¿hay algún OTRO Ocupante
    activo en mi unidad?", esté o no esté confirmado. `mi_reclamo_pending` es
    independiente de esa distinción: informa si la propia asociación sigue
    sin confirmarse, sin bloquear nada (ticket 06 -- pending no pierde
    funcionalidad)."""
    mi_ocupante = ocupante_activo_de_persona(db, persona.id)
    es_principal = mi_ocupante is not None and mi_ocupante.es_principal
    hay_otro_ocupante = (
        _hay_otro_ocupante_activo(db, mi_ocupante.apartamento_id, mi_ocupante.id)
        if mi_ocupante is not None and not es_principal
        else False
    )
    es_ocupante_no_principal = (
        mi_ocupante is not None and not es_principal and hay_otro_ocupante
    )
    ocupantes = []
    personas_telefono = {}
    personas_whatsapp = {}
    if mi_ocupante is not None:
        apto_ocupante = db.get(Apartamento, mi_ocupante.apartamento_id)
        ocupantes = listar_ocupantes(db, apto_ocupante)
        # Teléfonos/WhatsApp de los Ocupantes-con-contacto del roster, para
        # que la vista de un Ocupante no-principal (ticket 05, "ve todo")
        # pueda mostrarlos sin que la plantilla haga sus propias consultas.
        # WhatsApp: .scratch/ocupante-principal-escenarios, ticket 07.
        ids_persona = [o.persona_id for o in ocupantes if o.persona_id is not None]
        if ids_persona:
            personas = db.query(Persona).filter(Persona.id.in_(ids_persona)).all()
            personas_telefono = {p.id: p.telefono for p in personas}
            personas_whatsapp = {p.id: p.whatsapp_usuario for p in personas}
    return {
        "persona": persona,
        "apartamento": _apartamento_actual(db, persona),
        "nombre_conjunto": obtener_nombre_conjunto(db),
        "canales": list(CanalNotificacion),
        "canales_sin_proveedor": _CANALES_SIN_PROVEEDOR,
        "etiqueta_canal": _ETIQUETA_CANAL,
        "eventos": EVENTOS,
        "matriz": matriz_preferencias(db, persona.id),
        "es_principal_de_apartamento": es_principal,
        "es_ocupante_no_principal": es_ocupante_no_principal,
        "mi_reclamo_pending": mi_ocupante is not None and mi_ocupante.confirmado_en is None,
        "ocupantes": ocupantes,
        "personas_telefono": personas_telefono,
        "personas_whatsapp": personas_whatsapp,
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

    def _error(mensaje: str, campos: list[str] = None):
        db.rollback()  # "todo o nada": deshace cualquier mutación de este request
        contexto = _contexto_base(db, persona)
        contexto.update(
            {
                "request": request,
                "error": mensaje,
                "error_email": mensaje if "email" in (campos or []) else None,
                "error_telefono": mensaje if "telefono" in (campos or []) else None,
            }
        )
        return templates.TemplateResponse(
            "customer/verify.html", contexto, status_code=400
        )

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


@router.post("/mis-datos/desvincular-telefono", response_class=HTMLResponse)
def customer_desvincular_telefono(
    request: Request,
    persona: Persona = Depends(current_customer),
    db: Session = Depends(get_db),
    confirmar: str = Form(None),
):
    """Quita el propio Teléfono (`.scratch/ocupante-principal-escenarios`,
    ticket 14) -- acción separada del `<form>` general de "Datos
    personales", con su propia confirmación explícita (checkbox
    `confirmar`, exigido también acá server-side, no solo `required` en el
    HTML). A diferencia de `cambiar_telefono_propio` (que reabre una
    verificación OTP al número nuevo), acá no hay a dónde reverificar: el
    número desaparece, así que la sesión se cierra directo."""
    gate = _gate_no_verificado(request, db, persona)
    if gate is not None:
        return gate

    if not confirmar:
        return _render_con_error(
            request, db, persona,
            "Confirma que entiendes que perderás el acceso, antes de continuar.",
        )

    try:
        desvincular_telefono_propio(db, persona)
    except ValueError as exc:
        return _render_con_error(request, db, persona, str(exc))

    request.session.pop(CUSTOMER_SESSION_KEY, None)
    request.session.pop(CUSTOMER_NOMBRE_SESSION_KEY, None)
    return RedirectResponse("/otp?telefono_desvinculado=1", status_code=303)


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
    contacto = (form.get("contacto") or "").strip()
    if not nombre:
        return _render_con_error(request, db, persona, "El nombre del Ocupante es obligatorio.")

    kwargs_contacto = {}
    if contacto:
        tipo_contacto = clasificar_contacto(contacto)
        if tipo_contacto == "telefono":
            kwargs_contacto["telefono"] = contacto
        elif tipo_contacto == "whatsapp":
            kwargs_contacto["whatsapp_usuario"] = contacto
        else:
            return _render_con_error(
                request, db, persona,
                "Ese contacto no parece un Teléfono ni un usuario de WhatsApp "
                "válido -- revísalo, o déjalo vacío.",
            )

    apto = db.get(Apartamento, mi_ocupante.apartamento_id)
    try:
        agregar_ocupante(db, apto, nombre, **kwargs_contacto)
    except ValueError as exc:
        return _render_con_error(request, db, persona, str(exc))

    return RedirectResponse("/mis-datos?ocupante_guardado=1", status_code=303)


@router.post("/mis-datos/ocupantes/{ocupante_id}/contacto", response_class=HTMLResponse)
async def customer_ocupante_asociar_contacto(
    ocupante_id: str,
    request: Request,
    persona: Persona = Depends(current_customer),
    db: Session = Depends(get_db),
):
    """Asocia el PRIMER contacto propio de un Ocupante que hoy no tiene
    ninguno -- input único autoclasificado (`.scratch/ocupante-principal-
    escenarios`, ticket 07), mismo criterio que "agregar Residente"."""
    gate = _gate_no_verificado(request, db, persona)
    if gate is not None:
        return gate

    ocupante = _ocupante_gestionable_por(db, persona, ocupante_id)
    form = await request.form()
    contacto = (form.get("contacto") or "").strip()
    if not contacto:
        return _render_con_error(request, db, persona, "El contacto es obligatorio.")

    tipo_contacto = clasificar_contacto(contacto)
    try:
        if tipo_contacto == "telefono":
            asociar_telefono_a_ocupante(db, ocupante, contacto)
        elif tipo_contacto == "whatsapp":
            asociar_whatsapp_a_ocupante(db, ocupante, contacto)
        else:
            return _render_con_error(
                request, db, persona,
                "Ese contacto no parece un Teléfono ni un usuario de WhatsApp "
                "válido -- revísalo.",
            )
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


@router.post("/mis-datos/ocupantes/{ocupante_id}/whatsapp", response_class=HTMLResponse)
async def customer_ocupante_asociar_whatsapp(
    ocupante_id: str,
    request: Request,
    persona: Persona = Depends(current_customer),
    db: Session = Depends(get_db),
):
    """Asociar/editar WhatsApp de un Ocupante -- mismo patrón que
    `customer_ocupante_asociar_telefono` (`.scratch/ocupante-principal-
    escenarios`, ticket 07)."""
    gate = _gate_no_verificado(request, db, persona)
    if gate is not None:
        return gate

    ocupante = _ocupante_gestionable_por(db, persona, ocupante_id)
    form = await request.form()
    whatsapp_usuario = _blank_to_none(form.get("whatsapp_usuario"))
    if not whatsapp_usuario:
        return _render_con_error(request, db, persona, "El WhatsApp es obligatorio.")

    try:
        if ocupante.persona_id is None:
            asociar_whatsapp_a_ocupante(db, ocupante, whatsapp_usuario)
        else:
            editar_whatsapp_ocupante(db, ocupante, whatsapp_usuario)
    except ValueError as exc:
        return _render_con_error(request, db, persona, str(exc))

    return RedirectResponse("/mis-datos?ocupante_guardado=1", status_code=303)


@router.post(
    "/mis-datos/ocupantes/{ocupante_id}/desvincular-whatsapp", response_class=HTMLResponse
)
def customer_ocupante_desvincular_whatsapp(
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
        desvincular_whatsapp_ocupante(db, ocupante)
    except ValueError as exc:
        return _render_con_error(request, db, persona, str(exc))

    return RedirectResponse("/mis-datos?ocupante_guardado=1", status_code=303)


@router.post("/mis-datos/ocupantes/{ocupante_id}/confirmar", response_class=HTMLResponse)
def customer_ocupante_confirmar(
    ocupante_id: str,
    request: Request,
    persona: Persona = Depends(current_customer),
    db: Session = Depends(get_db),
):
    """Confirma a `ocupante_id` -- solo el principal YA CONFIRMADO de la
    misma unidad puede hacerlo (`.scratch/apartamento-catalogo-confirmacion`,
    ticket 08). `_ocupante_gestionable_por` ya exige exactamente eso, mismo
    guard que el resto de esta gestión."""
    gate = _gate_no_verificado(request, db, persona)
    if gate is not None:
        return gate

    ocupante = _ocupante_gestionable_por(db, persona, ocupante_id)
    try:
        confirmar_ocupante(db, ocupante, persona)
    except (PermissionError, ValueError) as exc:
        return _render_con_error(request, db, persona, str(exc))
    except IntegrityError:
        # Carrera real (dos confirmaciones/promociones a la vez sobre el
        # mismo Apartamento) -- el índice único parcial de Ocupante ya la
        # bloqueó a nivel de BD, esto solo evita que la transacción
        # perdedora vea un 500 crudo en vez de un mensaje claro.
        return _render_con_error(
            request, db, persona,
            "Alguien más ya hizo un cambio en este apartamento -- "
            "actualiza la página e intenta de nuevo.",
        )

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
    except IntegrityError:
        return _render_con_error(
            request, db, persona,
            "Alguien más ya hizo un cambio en este apartamento -- "
            "actualiza la página e intenta de nuevo.",
        )

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
