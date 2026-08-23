# -*- coding: utf-8 -*-
"""
Ruta `/residentes` — buscar + ver/editar residente (staff).

Buscar y editar son operativos, abiertos a CUALQUIER rol de staff (a diferencia
de eliminar, gated por `require_admin` en el módulo de la acción destructiva).
Reutiliza `update_datos_personales`/`cambiar_telefono_propio` de
`persona_service`, operando sobre la Persona de OTRO (no la propia sesión, a
diferencia de `/customer/verify`).

La ficha (`/residentes/{id}`, issues 67/68) se organiza en 4 tabs -- Datos,
Dirección, Notificaciones, Residentes -- controladas del lado del cliente
(mismo patrón de `customer/verify.html`); el servidor sigue recibiendo un POST
por sección (`/residentes/{id}`, `/residentes/{id}/apartamento`,
`/residentes/{id}/notificaciones`, `/residentes/{id}/ocupantes/...`), no un
único formulario gigante.
"""

import re
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.apartamento import Apartamento
from app.domain.apartamento_service import listar_catalogo_por_torre, resolver_apartamento
from app.domain.ocupante import Ocupante
from app.domain.contacto import clasificar_contacto
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
    hay_otro_ocupante_activo,
    identificar_contacto_para_unidad,
    listar_ocupantes,
    mensaje_ya_ocupante_activo,
    mover_ocupante,
    ocupante_activo_de_persona,
    ocupante_activo_por_contacto,
    ocupantes_activos_de_personas,
    promover_a_principal,
    reasignar_apartamento,
    residentes_por_torre_apartamento,
)
from app.domain.persona import Persona
from app.domain.persona_service import (
    WHATSAPP_USUARIO_RE,
    anonimizar_persona,
    cambiar_telefono_propio,
    set_autoriza_recepcion_automatica,
    update_datos_personales,
    url_llamada,
    url_whatsapp,
)
from app.domain.preferencia_notificacion import CanalNotificacion
from app.domain.preferencia_notificacion_service import (
    EVENTOS,
    guardar_matriz_preferencias,
    matriz_preferencias,
)
from app.domain.telefono import normalizar_telefono
from app.domain.usuario import Usuario

from ..db import get_db
from ..security import current_staff, require_admin
from ..templating import templates

router = APIRouter()

# Mismas 2 constantes de presentación que `customer_verify.py` (Llamada/
# WhatsApp sin proveedor conectado todavía) -- duplicadas a propósito, mismo
# patrón que `_blank_to_none` ya duplicado entre ambos archivos de ruta.
_CANALES_SIN_PROVEEDOR = {CanalNotificacion.LLAMADA, CanalNotificacion.WHATSAPP}
_ETIQUETA_CANAL = {
    CanalNotificacion.SMS: "SMS",
    CanalNotificacion.EMAIL: "Email",
    CanalNotificacion.LLAMADA: "Llamada",
    CanalNotificacion.WHATSAPP: "WhatsApp",
}


def _blank_to_none(valor):
    valor = (valor or "").strip()
    return valor or None


def _apartamento_actual(db: Session, persona: Persona):
    if persona.apartamento_actual_id is None:
        return None
    return db.get(Apartamento, persona.apartamento_actual_id)


def _ocupantes_de(db: Session, apartamento):
    if apartamento is None:
        return []
    ocupantes = listar_ocupantes(db, apartamento)
    for o in ocupantes:
        # Atributos transitorios (no persistidos) — Ocupante no tiene
        # relationship ORM a Persona, solo el FK crudo `persona_id`.
        persona = db.get(Persona, o.persona_id) if o.persona_id else None
        o.telefono = persona.telefono if persona else None
        # WhatsApp (.scratch/ocupante-principal-escenarios, ticket 06).
        o.whatsapp_usuario = persona.whatsapp_usuario if persona else None
    return ocupantes


def _get_persona_o_404(db: Session, persona_id: str) -> Persona:
    try:
        pid = uuid.UUID(persona_id)
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Residente no encontrado")
    persona = db.get(Persona, pid)
    if persona is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Residente no encontrado")
    return persona


def _buscar_residentes(db: Session, termino: str) -> list[Persona]:
    """Búsqueda extendida (Grupo 17, Ronda 2): teléfono o nombre de la
    Persona principal, torre/apartamento de su unidad, o nombre de
    cualquier Ocupante (con o sin teléfono propio) de su misma unidad — un
    match por Ocupante resuelve a la Persona **principal** de ese
    Apartamento (los Ocupantes sin teléfono no tienen ficha propia).
    Resultados únicos, sin duplicar si varios criterios coinciden con la
    misma Persona.

    Issue 170 (.scratch/pendientes-cliente): ya no busca por
    `segundo_contacto` -- ese campo se eliminó por completo, ningún flujo
    real lo usaba."""
    encontradas: dict = {}  # id -> Persona, dedup preservando orden de hallazgo

    def _agregar_todas(personas):
        for p in personas:
            encontradas.setdefault(p.id, p)

    try:
        telefono = normalizar_telefono(termino)
    except ValueError:
        telefono = None

    filtros_persona = [Persona.nombre.ilike(f"%{termino}%")]
    if telefono is not None:
        filtros_persona.append(Persona.telefono == telefono)
    _agregar_todas(db.query(Persona).filter(or_(*filtros_persona)).all())

    apartamentos_match = (
        db.query(Apartamento)
        .filter(
            or_(
                Apartamento.torre.ilike(f"%{termino}%"),
                Apartamento.apartamento.ilike(f"%{termino}%"),
            )
        )
        .all()
    )
    if apartamentos_match:
        apto_ids = [a.id for a in apartamentos_match]
        _agregar_todas(
            db.query(Persona).filter(Persona.apartamento_actual_id.in_(apto_ids)).all()
        )

    ocupantes_match = db.query(Ocupante).filter(Ocupante.nombre.ilike(f"%{termino}%")).all()
    if ocupantes_match:
        apto_ids_de_ocupantes = {o.apartamento_id for o in ocupantes_match}
        principales = (
            db.query(Ocupante)
            .filter(
                Ocupante.apartamento_id.in_(apto_ids_de_ocupantes),
                Ocupante.es_principal.is_(True),
            )
            .all()
        )
        persona_ids = [o.persona_id for o in principales if o.persona_id is not None]
        if persona_ids:
            _agregar_todas(db.query(Persona).filter(Persona.id.in_(persona_ids)).all())

    return sorted(encontradas.values(), key=lambda p: p.nombre or "")


_POR_PAGINA = 20


def _apartamentos_por_id(db: Session, personas: list[Persona]) -> dict:
    """Resuelve el Apartamento de cada Persona en UN solo query (auditoría de
    base de datos, .scratch/pendientes-cliente) -- una consulta por Persona
    dentro del loop de la plantilla sería el mismo patrón N+1 ya corregido en
    `/paquetes`/`/mis-paquetes`."""
    ids = {p.apartamento_actual_id for p in personas if p.apartamento_actual_id}
    if not ids:
        return {}
    apartamentos = db.query(Apartamento).filter(Apartamento.id.in_(ids)).all()
    return {a.id: a for a in apartamentos}


def _adjuntar_apartamentos(db: Session, personas: list[Persona]) -> list[Persona]:
    por_id = _apartamentos_por_id(db, personas)
    for p in personas:
        # Atributo transitorio (no persistido), mismo patrón que
        # `packages.py` (`p.advertencia_nombre`, `p.actor_ultima_accion`).
        p.apartamento = por_id.get(p.apartamento_actual_id)
    return personas


def _adjuntar_ocupante(db: Session, personas: list[Persona]) -> list[Persona]:
    """Badge de Principal/Secundario en la lista (issue 68) -- adjunta el
    Ocupante activo de cada Persona (o `None` si nunca "declaró unidad"/se
    agregó como Residente, caso en el que el badge simplemente no aplica)."""
    por_persona = ocupantes_activos_de_personas(db, [p.id for p in personas])
    for p in personas:
        p.ocupante = por_persona.get(p.id)
    return personas


def _adjuntar_comparte_apartamento(db: Session, personas: list[Persona]) -> list[Persona]:
    """Ícono 👫 en Acciones (issue 156, .scratch/pendientes-cliente) -- marca
    si el Residente comparte su unidad con al menos otro Ocupante ACTIVO.
    Un solo GROUP BY para todo el listado (mismo patrón anti-N+1 que
    `_adjuntar_apartamentos`/`_adjuntar_ocupante`), no una consulta por fila."""
    apto_ids = {p.apartamento_actual_id for p in personas if p.apartamento_actual_id}
    conteos = {}
    if apto_ids:
        filas = (
            db.query(Ocupante.apartamento_id, func.count(Ocupante.id))
            .filter(
                Ocupante.apartamento_id.in_(apto_ids),
                Ocupante.desvinculado_en.is_(None),
            )
            .group_by(Ocupante.apartamento_id)
            .all()
        )
        conteos = dict(filas)
    for p in personas:
        # Atributo transitorio (no persistido), mismo patrón que `p.apartamento`/
        # `p.ocupante` de arriba.
        p.comparte_apartamento = conteos.get(p.apartamento_actual_id, 0) > 1
    return personas


def _listar_todos_los_residentes(db: Session, pagina: int = 1):
    """Sin término de búsqueda: TODOS los residentes ACTIVOS, paginados (pedido
    del cliente, .scratch/pendientes-cliente -- antes `/residentes` no
    mostraba nada hasta buscar). La búsqueda con término (`_buscar_residentes`)
    no se pagina -- ya es un subconjunto acotado por el propio filtro.

    Excluye eliminados (issue 67): ya están anonimizados (nombre/teléfono
    reales borrados por `anonimizar_persona`), así que no aportan nada al
    día a día del staff -- y de todos modos casi nunca calzarían con una
    búsqueda por su nombre/teléfono real."""
    query = db.query(Persona).filter(Persona.eliminado_en.is_(None)).order_by(Persona.nombre)
    total = query.count()
    total_paginas = max(1, -(-total // _POR_PAGINA))  # ceil sin importar float
    pagina = max(1, min(pagina, total_paginas))
    personas = query.offset((pagina - 1) * _POR_PAGINA).limit(_POR_PAGINA).all()
    return personas, pagina, total_paginas


@router.get("/residentes", response_class=HTMLResponse)
def customers_manage_search(
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    q: str = None,
    pagina: int = 1,
):
    termino = _blank_to_none(q)
    if termino:
        resultados = _buscar_residentes(db, termino)
        pagina_actual, total_paginas = 1, 1
    else:
        resultados, pagina_actual, total_paginas = _listar_todos_los_residentes(db, pagina)
    _adjuntar_apartamentos(db, resultados)
    _adjuntar_ocupante(db, resultados)
    _adjuntar_comparte_apartamento(db, resultados)
    return templates.TemplateResponse(
        "customers_manage/search.html",
        {
            "request": request,
            "staff": staff,
            "q": termino or "",
            "resultados": resultados,
            "pagina_actual": pagina_actual,
            "total_paginas": total_paginas,
            "url_whatsapp": url_whatsapp,
            "url_llamada": url_llamada,
            "etiqueta_torre_apto": _etiqueta_torre_apto,
        },
    )


_NUMERO_TORRE_RE = re.compile(r"(\d+)")


def _etiqueta_torre_apto(apartamento, fallback: str) -> str:
    """Referencia compacta de una unidad (ej. "T 05 - APT 102", issue 69/70)
    -- `fallback` si no hay Apartamento asignado (distinto en la ficha,
    "Residentes", que en la tabla de la lista, "No Asignado")."""
    if apartamento is None:
        return fallback
    numero = _NUMERO_TORRE_RE.search(apartamento.torre)
    torre_corta = f"T {int(numero.group()):02d}" if numero else apartamento.torre
    return f"{torre_corta} - APT {apartamento.apartamento}"


def _etiqueta_tab_residentes(apartamento) -> str:
    """"Residentes" por default; con apartamento asignado, la referencia
    exacta -- así se ve de una a cuál unidad pertenecen sin tener que abrir
    la tab."""
    return _etiqueta_torre_apto(apartamento, fallback="Residentes")


def _aviso_reasignacion_bloqueada(db: Session, mi_ocupante) -> str | None:
    """Explica de antemano (issue 69) por qué el picker de Dirección va a
    rechazar el guardado -- antes el staff solo se enteraba después de
    intentarlo (ver el guard real en `ocupante_service.reasignar_apartamento`,
    que bloquea SIEMPRE que la Persona sea Ocupante activo de algún
    Apartamento, para no desincronizar `Persona.apartamento_actual_id` de su
    `Ocupante.apartamento_id`: primero hay que darla de baja como Residente,
    o promover a otro principal, desde la tab "Residentes")."""
    if mi_ocupante is None:
        return None
    if mi_ocupante.es_principal and hay_otro_ocupante_activo(
        db, mi_ocupante.apartamento_id, mi_ocupante.id
    ):
        return (
            "Este residente es el Residente principal de su apartamento actual, que "
            "tiene otros Residentes activos -- para reasignarlo desde acá, primero "
            "convierte a otro en principal o dales de baja a todos en la tab "
            '"Residentes".'
        )
    return (
        "Este residente está registrado como Residente de su apartamento actual -- "
        'para reasignarlo desde acá, primero dalo de baja como Residente en la tab '
        '"Residentes".'
    )


def _contexto_detalle(db: Session, staff: Usuario, persona: Persona) -> dict:
    """Contexto común a la ficha de residente y a cualquier re-render tras un
    error o una acción sobre Ocupantes/Notificaciones (.scratch/mis-datos,
    ticket 10; issue 67)."""
    apto = _apartamento_actual(db, persona)
    mi_ocupante = ocupante_activo_de_persona(db, persona.id)
    return {
        "staff": staff,
        "persona": persona,
        "apartamento": apto,
        "catalogo_torres": listar_catalogo_por_torre(db),
        "ocupantes": _ocupantes_de(db, apto),
        # Badge de Principal/Secundario (issue 68), visible en el header de
        # la ficha sin importar la tab activa -- `None` si esta Persona
        # nunca "declaró unidad"/se agregó como Residente (no aplica).
        "mi_ocupante": mi_ocupante,
        "limite_ocupantes": MAX_OCUPANTES_ACTIVOS,
        "url_whatsapp": url_whatsapp,
        "url_llamada": url_llamada,
        # Qué tab queda activa al (re)mostrar la ficha (issue 67) -- 'datos'
        # por default; cada caller la sobrescribe cuando la acción que
        # disparó este render fue de otra tab (issue 68: 'direccion' para
        # Torre/Apartamento, separada de 'datos').
        "tab_inicial": "datos",
        # Matriz completa de notificaciones (issue 67) -- reemplaza el
        # toggle simplificado, mismos datos/funciones que `/mis-datos`
        # (`customer_verify.py`).
        "canales": list(CanalNotificacion),
        "canales_sin_proveedor": _CANALES_SIN_PROVEEDOR,
        "etiqueta_canal": _ETIQUETA_CANAL,
        "eventos": EVENTOS,
        "matriz": matriz_preferencias(db, persona.id),
        # Issue 147 (.scratch/pendientes-cliente): tab Dirección pasa a usar
        # `components/_picker_apartamento.html`, el mismo componente/flujo de
        # "Asignar apartamento" y "Recibir" en /paquetes (y compartido con
        # /announce) -- reemplaza el picker Torre->Piso->Apartamento propio
        # que tenía esta ficha, que nunca se actualizó cuando ese componente
        # se extrajo. Mismo dato (`residentes_por_torre_apartamento`) que ya
        # usan esas 2 vistas: informativo (nombres reales de quién vive en
        # cada unidad), NUNCA bloquea la selección en el cliente -- "mismo
        # criterio del resto de la app" (ver el propio comentario del picker
        # compartido). El bloqueo real de unidades ocupadas se sigue
        # aplicando SOLO server-side, en el POST de abajo (`ya_tiene_
        # residentes`) -- ver también `_aviso_reasignacion_bloqueada`, que
        # avisa de antemano si esta Persona en particular no se puede
        # reasignar (un caso distinto: ella ya es Ocupante de algo).
        "residentes_por_unidad": residentes_por_torre_apartamento(db),
        "aviso_reasignacion_bloqueada": _aviso_reasignacion_bloqueada(db, mi_ocupante),
        "etiqueta_tab_residentes": _etiqueta_tab_residentes(apto),
    }


def _render_detalle_con_error(
    request: Request, db: Session, staff: Usuario, persona: Persona, mensaje: str,
    tab_inicial: str = "datos",
) -> HTMLResponse:
    db.rollback()
    contexto = _contexto_detalle(db, staff, persona)
    contexto["request"] = request
    contexto["error"] = mensaje
    contexto["tab_inicial"] = tab_inicial
    return templates.TemplateResponse(
        "customers_manage/detail.html", contexto, status_code=400
    )


_TABS_VALIDAS = {"datos", "direccion", "notif", "residentes"}


@router.get("/residentes/{persona_id}", response_class=HTMLResponse)
def customers_manage_detail(
    persona_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    tab: str = None,
):
    persona = _get_persona_o_404(db, persona_id)
    contexto = _contexto_detalle(db, staff, persona)
    contexto["request"] = request
    # Vuelta desde una acción de Ocupantes (redirect 303, ver las rutas
    # `.../ocupantes/...`) -- reabre la tab de la que salió el staff en vez
    # de resetear a "Datos" (issue 67).
    if request.query_params.get("ocupante_guardado") == "1":
        contexto["tab_inicial"] = "residentes"
        contexto["ocupante_guardado"] = True
    # `?tab=` (conversación 2026-08-17, pedido explícito): un link externo
    # (ej. "Degradarlo" en "Corregir destinatario" de /paquetes, cuando el
    # contacto ya es Principal de otra unidad) puede entrar directo a la
    # tab correcta en vez de "Datos" -- se ignora silenciosamente un valor
    # desconocido (mismo criterio que "a medio teclear" en otros lados: un
    # link roto o mal armado no debe romper la página, solo cae al default).
    elif tab in _TABS_VALIDAS:
        contexto["tab_inicial"] = tab
    return templates.TemplateResponse("customers_manage/detail.html", contexto)


@router.post("/residentes/{persona_id}", response_class=HTMLResponse)
def customers_manage_update(
    persona_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    nombre: str = Form(None),
    telefono: str = Form(None),
    email: str = Form(None),
    whatsapp_usuario: str = Form(None),
    autoriza_recepcion_automatica: str = Form(None),
):
    """Datos del residente (tab "Datos", issue 67) -- nombre/email/usuario
    de WhatsApp/teléfono, todo o nada por request.

    `autoriza_recepcion_automatica` (issue 169, .scratch/pendientes-cliente):
    antes exclusivo de `/mis-datos` (autoservicio) -- staff no tenía ningún
    control para tocarlo, solo lo veía como badge de solo lectura. Mismo
    contrato que ese otro caller: un checkbox HTML solo manda su `name` en
    el form cuando está marcado, así que "ausente" ES "no autoriza" (no
    "no tocar") -- `is not None` es la forma correcta de leer un booleano
    así, no `_blank_to_none`."""
    persona = _get_persona_o_404(db, persona_id)
    # "" explícito (no None -- issue 69): este formulario SIEMPRE manda este
    # campo, así que acá "vacío" tiene que poder significar "bórralo", no
    # "no lo toques" (con `_blank_to_none` nunca se podía vaciar una vez
    # tenía un valor -- bug real reportado en vivo). Ver el contrato de
    # 3 estados en `persona_service.update_datos_personales`.
    whatsapp_v = (whatsapp_usuario or "").strip()

    try:
        update_datos_personales(
            db,
            persona,
            nombre=_blank_to_none(nombre),
            email=_blank_to_none(email),
            whatsapp_usuario=whatsapp_v,
        )
    except ValueError as exc:
        # Dos posibles orígenes ahora (ver persona_service.
        # update_datos_personales): email o usuario de WhatsApp -- se
        # revalida acá cuál de los dos es para marcar el campo correcto en
        # rojo (la excepción en sí no distingue de dónde vino).
        db.rollback()
        contexto = _contexto_detalle(db, staff, persona)
        whatsapp_sin_arroba = whatsapp_v.lstrip("@")
        es_whatsapp = bool(whatsapp_sin_arroba) and not WHATSAPP_USUARIO_RE.match(whatsapp_sin_arroba)
        campo = "whatsapp_usuario" if es_whatsapp else "email"
        contexto.update({"request": request, "error": str(exc), f"error_{campo}": str(exc)})
        return templates.TemplateResponse(
            "customers_manage/detail.html", contexto, status_code=400
        )

    # Teléfono (nuevo, issue 67 -- antes esta ficha no tenía forma de
    # cambiarlo): reusa la misma función que `/mis-datos` usa para el
    # autoservicio del cliente (valida formato y choque con otra Persona) --
    # la única parte de esa función que NO aplica acá es cerrar sesión y
    # reverificar por OTP, responsabilidad del *caller* según su propio
    # docstring, y que ahí es porque el cliente reautentica su PROPIO
    # número; la sesión de un cliente resuelve por `persona_id` en la
    # cookie (ver `security.py`), no por teléfono, así que cambiarlo desde
    # el staff no invalida ninguna sesión activa de ese residente.
    telefono_v = _blank_to_none(telefono)
    if telefono_v is not None:
        try:
            cambiar_telefono_propio(db, persona, telefono_v)
        except ValueError as exc:
            db.rollback()
            contexto = _contexto_detalle(db, staff, persona)
            contexto.update(
                {"request": request, "error": str(exc), "error_telefono": str(exc)}
            )
            return templates.TemplateResponse(
                "customers_manage/detail.html", contexto, status_code=400
            )

    set_autoriza_recepcion_automatica(db, persona, autoriza_recepcion_automatica is not None)

    contexto = _contexto_detalle(db, staff, persona)
    contexto["request"] = request
    contexto["guardado"] = True
    return templates.TemplateResponse("customers_manage/detail.html", contexto)


@router.post("/residentes/{persona_id}/notificaciones", response_class=HTMLResponse)
async def customers_manage_notificaciones(
    persona_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
):
    """Matriz completa Canal × Evento (tab "Notificaciones", issue 67) --
    reemplaza el toggle simplificado de SMS que tenía antes esta ficha.
    Mismo mecanismo que `/mis-datos` (`customer_verify.py`): 16 checkboxes
    `pref_{canal}_{evento}`, leídos vía `request.form()` (`Form(...)` no da
    para una forma variable así de limpia)."""
    persona = _get_persona_o_404(db, persona_id)
    form = await request.form()

    activos = {
        (canal.value, evento.value)
        for canal in CanalNotificacion
        for evento in EVENTOS
        if canal not in _CANALES_SIN_PROVEEDOR
        and form.get(f"pref_{canal.value}_{evento.value}") is not None
    }
    guardar_matriz_preferencias(db, persona.id, activos)

    contexto = _contexto_detalle(db, staff, persona)
    contexto["request"] = request
    contexto["guardado"] = True
    contexto["tab_inicial"] = "notif"
    return templates.TemplateResponse("customers_manage/detail.html", contexto)


@router.post("/residentes/{persona_id}/apartamento", response_class=HTMLResponse)
def customers_manage_asignar_apartamento(
    persona_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    torre: str = Form(None),
    apartamento: str = Form(None),
    mover_de_otra_unidad: str = Form(None),
):
    """Asigna, cambia o desvincula la Torre/Apartamento de un residente --
    única vía para tocar `apartamento_actual_id` ahora que `/mis-datos` es de
    solo lectura para el residente (.scratch/pendientes-cliente): la
    asignación es exclusiva del personal de Papyrus.

    Pasa siempre por el padrón de `Ocupante` (`ocupante_service.
    reasignar_apartamento`, `.scratch/announce-residente-correcto` ticket
    01) en vez de escribir `apartamento_actual_id` de forma aislada -- así
    ese campo queda SIEMPRE derivado del padrón real, nunca un residente
    "fantasma" invisible para el resto del sistema (incluido el camino
    Torre+Apartamento de `/announce`). El guard de "no reasignar mientras
    haya otros Residentes activos" ya no vive acá -- lo aplica
    `dar_de_baja_ocupante` (principal con otros Ocupantes activos).

    `mover_de_otra_unidad` (`.scratch/ocupante-principal-escenarios`,
    ticket 12): si `persona` ya es Ocupante activo no-principal de OTRA
    unidad, en vez de solo bloquear se ofrece moverla ahí mismo
    (`mover_ocupante`) cuando el staff marca la casilla. Un principal nunca
    se mueve así, sin excepción.

    Issue 158 (`.scratch/pendientes-cliente`, revierte el ticket 13 de
    `.scratch/ocupante-principal-escenarios`): tab Dirección YA NO exige que
    la unidad destino esté vacía -- staff con control total, mismo criterio
    que tab Residentes usa a diario (`reasignar_apartamento`/`mover_ocupante`
    ya soportan sumar/mover gente a una unidad ocupada sin romper nada: el
    invariante real de "como máximo un principal por unidad" lo siguen
    garantizando esas mismas funciones, no un guard aparte acá). El picker
    (issue 147) sigue siendo solo informativo -- muestra quién vive en cada
    unidad, nunca deshabilita la selección."""
    persona = _get_persona_o_404(db, persona_id)
    torre_v = _blank_to_none(torre)
    apartamento_v = _blank_to_none(apartamento)
    partes = [torre_v, apartamento_v]

    if any(partes) and not all(partes):
        return _render_detalle_con_error(
            request, db, staff, persona, "Completa Torre y Apartamento, o deja los dos vacíos.",
            tab_inicial="direccion",
        )

    nuevo_apto = None
    if all(partes):
        try:
            nuevo_apto = resolver_apartamento(db, torre_v, apartamento_v)
        except ValueError as exc:
            return _render_detalle_con_error(
                request, db, staff, persona, str(exc), tab_inicial="direccion"
            )

    if nuevo_apto is not None:
        # Issue 158 (.scratch/pendientes-cliente): staff con control total --
        # asignar desde acá a una unidad que YA tiene Residentes ya no se
        # bloquea (antes obligaba a ir a la ficha de alguien que ya viviera
        # ahí, [[157]]). `agregar_ocupante`/`confirmar_ocupante` ya soportan
        # sumar a alguien a una unidad ocupada sin romper nada -- queda
        # confirmado como NO principal, sin tocar a quien ya lo es (mismo
        # camino que usa a diario tab Residentes). El picker de arriba ya
        # avisa (informativo, issue 147) quién vive en cada unidad ANTES de
        # elegirla -- no hace falta un bloqueo server-side para eso. Sin
        # riesgo de un Ocupante "fantasma" desconectado de esta ficha por
        # falta de contacto -- `ck_personas_telefono_o_whatsapp` (ADR-0007)
        # garantiza que TODA Persona tiene Teléfono o WhatsApp, así que
        # `agregar_ocupante` siempre puede resolver esta misma Persona.
        conflicto = ocupante_activo_de_persona(db, persona.id)
        if conflicto is not None and conflicto.apartamento_id != nuevo_apto.id:
            # Issue 159 (.scratch/pendientes-cliente): un Principal ya no
            # bloquea acá -- `mover_ocupante` degrada automáticamente si
            # hace falta (ver su docstring). Mismo checkbox de siempre, un
            # Principal ya no necesita un paso manual aparte.
            if not mover_de_otra_unidad:
                return _render_detalle_con_error(
                    request, db, staff, persona,
                    mensaje_ya_ocupante_activo(db, conflicto), tab_inicial="direccion",
                )
            try:
                mover_ocupante(db, conflicto, nuevo_apto)
            except ValueError as exc:
                return _render_detalle_con_error(
                    request, db, staff, persona, str(exc), tab_inicial="direccion"
                )
            contexto = _contexto_detalle(db, staff, persona)
            contexto["request"] = request
            contexto["guardado"] = True
            contexto["tab_inicial"] = "direccion"
            return templates.TemplateResponse("customers_manage/detail.html", contexto)

    huerfano_detectado = (
        nuevo_apto is None
        and persona.apartamento_actual_id is not None
        and ocupante_activo_de_persona(db, persona.id) is None
    )

    try:
        reasignar_apartamento(db, persona, nuevo_apto, staff)
    except ValueError as exc:
        return _render_detalle_con_error(
            request, db, staff, persona, str(exc), tab_inicial="direccion"
        )

    contexto = _contexto_detalle(db, staff, persona)
    contexto["request"] = request
    contexto["guardado"] = True
    contexto["tab_inicial"] = "direccion"
    if huerfano_detectado:
        contexto["aviso_dato_huerfano"] = (
            "Este residente tenía un apartamento asignado sin ningún Residente "
            "real detrás -- se limpió ese dato inconsistente."
        )
    return templates.TemplateResponse("customers_manage/detail.html", contexto)


def _ocupante_o_404(db: Session, ocupante_id: str) -> Ocupante:
    try:
        oid = uuid.UUID(ocupante_id)
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ocupante no encontrado")
    ocupante = db.get(Ocupante, oid)
    if ocupante is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ocupante no encontrado")
    return ocupante


@router.get("/residentes/{persona_id}/ocupantes/identificar")
def customers_manage_ocupante_identificar(
    persona_id: str,
    contacto: str = "",
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
):
    """Vista previa en vivo para el campo "Teléfono o WhatsApp" de "+
    Agregar un nuevo Residente" (issue 154, .scratch/pendientes-cliente) --
    mismo mecanismo que ya tenía "+ Nuevo residente" en /paquetes
    (`nuevo_residente_identificar`), acá reusando la lógica compartida
    (`ocupante_service.identificar_contacto_para_unidad`) en vez de
    reimplementarla: mientras el staff escribe, avisa si el contacto YA es
    una Persona registrada y si ya es Ocupante activo de OTRA unidad
    (`agregar_ocupante` ya lo impide server-side de todos modos; esto es
    la vista previa, no el enforcement real).

    `apto_actual` acá es la unidad ACTUAL de `persona` (a diferencia de
    /paquetes, que la resuelve del snapshot del Paquete) -- mismo criterio
    que el resto de este archivo para decidir "unidad de referencia"."""
    persona = _get_persona_o_404(db, persona_id)
    apto_actual = _apartamento_actual(db, persona)
    return identificar_contacto_para_unidad(db, contacto, apto_actual)


@router.post("/residentes/{persona_id}/ocupantes", response_class=HTMLResponse)
def customers_manage_ocupante_crear(
    persona_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    nombre: str = Form(None),
    contacto: str = Form(None),
    mover_de_otra_unidad: str = Form(None),
):
    """Staff sin restricción (.scratch/mis-datos, ticket 10) — mismas
    funciones de dominio que `/mis-datos` (ticket 03), sin exigir que el
    staff sea "principal" de nada.

    `contacto` (.scratch/ocupante-principal-escenarios, ticket 06): un
    input único, autoclasificado (Teléfono o WhatsApp) igual que
    `/announce` -- ya no exige que el primer contacto sea Teléfono.

    `mover_de_otra_unidad` (ticket 12): si `contacto` ya es Ocupante activo
    no-principal de OTRA unidad, mueve a esa persona (con su identidad
    real, no un registro nuevo con el `nombre` recién tecleado) en vez de
    solo bloquear -- el `nombre` tecleado se ignora en ese caso."""
    persona = _get_persona_o_404(db, persona_id)
    apto = _apartamento_actual(db, persona)
    nombre_v = _blank_to_none(nombre)
    if apto is None or not nombre_v:
        return _render_detalle_con_error(
            request, db, staff, persona,
            "Este residente no tiene apartamento asignado, o falta el nombre." if apto is None
            else "El nombre del Ocupante es obligatorio.",
            tab_inicial="residentes",
        )

    contacto_v = (contacto or "").strip()
    kwargs_contacto = {}
    if contacto_v:
        tipo_contacto = clasificar_contacto(contacto_v)
        if tipo_contacto == "telefono":
            kwargs_contacto["telefono"] = contacto_v
        elif tipo_contacto == "whatsapp":
            kwargs_contacto["whatsapp_usuario"] = contacto_v
        else:
            return _render_detalle_con_error(
                request, db, staff, persona,
                "Ese contacto no parece un Teléfono ni un usuario de WhatsApp "
                "válido -- revísalo, o déjalo vacío.",
                tab_inicial="residentes",
            )

        conflicto = ocupante_activo_por_contacto(db, **kwargs_contacto)
        if conflicto is not None and conflicto.apartamento_id != apto.id:
            # Issue 159 (.scratch/pendientes-cliente): un Principal ya no
            # bloquea acá -- `mover_ocupante` degrada automáticamente si
            # hace falta (ver su docstring).
            if not mover_de_otra_unidad:
                return _render_detalle_con_error(
                    request, db, staff, persona,
                    mensaje_ya_ocupante_activo(db, conflicto), tab_inicial="residentes",
                )
            try:
                mover_ocupante(db, conflicto, apto)
            except ValueError as exc:
                return _render_detalle_con_error(
                    request, db, staff, persona, str(exc), tab_inicial="residentes"
                )
            return RedirectResponse(
                f"/residentes/{persona.id}?ocupante_guardado=1", status_code=status.HTTP_303_SEE_OTHER
            )

    try:
        agregar_ocupante(db, apto, nombre_v, **kwargs_contacto)
    except ValueError as exc:
        return _render_detalle_con_error(request, db, staff, persona, str(exc), tab_inicial="residentes")
    return RedirectResponse(
        f"/residentes/{persona.id}?ocupante_guardado=1", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post(
    "/residentes/{persona_id}/ocupantes/{ocupante_id}/telefono", response_class=HTMLResponse
)
def customers_manage_ocupante_asociar_telefono(
    persona_id: str,
    ocupante_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    telefono: str = Form(None),
):
    persona = _get_persona_o_404(db, persona_id)
    ocupante = _ocupante_o_404(db, ocupante_id)
    telefono_v = _blank_to_none(telefono)
    if not telefono_v:
        return _render_detalle_con_error(
            request, db, staff, persona, "El teléfono es obligatorio.", tab_inicial="residentes"
        )
    try:
        if ocupante.persona_id is None:
            asociar_telefono_a_ocupante(db, ocupante, telefono_v)
        else:
            # Editar un teléfono YA asociado (pedido del cliente,
            # `.scratch/pendientes-cliente/issues/35`) -- el principal se
            # sigue excluyendo (ver `editar_telefono_ocupante`); no hay hoy
            # una vía de staff para renombrar el teléfono PROPIO de un
            # principal, mismo estado que antes de este pedido.
            editar_telefono_ocupante(db, ocupante, telefono_v)
    except ValueError as exc:
        return _render_detalle_con_error(request, db, staff, persona, str(exc), tab_inicial="residentes")
    return RedirectResponse(
        f"/residentes/{persona.id}?ocupante_guardado=1", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post(
    "/residentes/{persona_id}/ocupantes/{ocupante_id}/desvincular-telefono",
    response_class=HTMLResponse,
)
def customers_manage_ocupante_desvincular_telefono(
    persona_id: str,
    ocupante_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
):
    persona = _get_persona_o_404(db, persona_id)
    ocupante = _ocupante_o_404(db, ocupante_id)
    try:
        desvincular_telefono_ocupante(db, ocupante)
    except ValueError as exc:
        return _render_detalle_con_error(request, db, staff, persona, str(exc), tab_inicial="residentes")
    return RedirectResponse(
        f"/residentes/{persona.id}?ocupante_guardado=1", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post(
    "/residentes/{persona_id}/ocupantes/{ocupante_id}/contacto", response_class=HTMLResponse
)
def customers_manage_ocupante_asociar_contacto(
    persona_id: str,
    ocupante_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    contacto: str = Form(None),
):
    """Asocia el PRIMER contacto propio de un Ocupante que hoy no tiene
    ninguno -- input único autoclasificado (`.scratch/ocupante-principal-
    escenarios`, ticket 06), mismo criterio que "agregar Residente" y que
    `/announce`. Una vez asociado, editarlo pasa por `/telefono` o
    `/whatsapp` (según cuál haya quedado), no por acá."""
    persona = _get_persona_o_404(db, persona_id)
    ocupante = _ocupante_o_404(db, ocupante_id)
    contacto_v = (contacto or "").strip()
    if not contacto_v:
        return _render_detalle_con_error(
            request, db, staff, persona, "El contacto es obligatorio.", tab_inicial="residentes"
        )
    tipo_contacto = clasificar_contacto(contacto_v)
    try:
        if tipo_contacto == "telefono":
            asociar_telefono_a_ocupante(db, ocupante, contacto_v)
        elif tipo_contacto == "whatsapp":
            asociar_whatsapp_a_ocupante(db, ocupante, contacto_v)
        else:
            return _render_detalle_con_error(
                request, db, staff, persona,
                "Ese contacto no parece un Teléfono ni un usuario de WhatsApp "
                "válido -- revísalo.",
                tab_inicial="residentes",
            )
    except ValueError as exc:
        return _render_detalle_con_error(request, db, staff, persona, str(exc), tab_inicial="residentes")
    return RedirectResponse(
        f"/residentes/{persona.id}?ocupante_guardado=1", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post(
    "/residentes/{persona_id}/ocupantes/{ocupante_id}/whatsapp", response_class=HTMLResponse
)
def customers_manage_ocupante_asociar_whatsapp(
    persona_id: str,
    ocupante_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    whatsapp_usuario: str = Form(None),
):
    """Asociar/editar WhatsApp de un Ocupante -- mismo patrón que
    `customers_manage_ocupante_asociar_telefono`
    (`.scratch/ocupante-principal-escenarios`, ticket 06)."""
    persona = _get_persona_o_404(db, persona_id)
    ocupante = _ocupante_o_404(db, ocupante_id)
    whatsapp_v = _blank_to_none(whatsapp_usuario)
    if not whatsapp_v:
        return _render_detalle_con_error(
            request, db, staff, persona, "El WhatsApp es obligatorio.", tab_inicial="residentes"
        )
    try:
        if ocupante.persona_id is None:
            asociar_whatsapp_a_ocupante(db, ocupante, whatsapp_v)
        else:
            editar_whatsapp_ocupante(db, ocupante, whatsapp_v)
    except ValueError as exc:
        return _render_detalle_con_error(request, db, staff, persona, str(exc), tab_inicial="residentes")
    return RedirectResponse(
        f"/residentes/{persona.id}?ocupante_guardado=1", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post(
    "/residentes/{persona_id}/ocupantes/{ocupante_id}/desvincular-whatsapp",
    response_class=HTMLResponse,
)
def customers_manage_ocupante_desvincular_whatsapp(
    persona_id: str,
    ocupante_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
):
    persona = _get_persona_o_404(db, persona_id)
    ocupante = _ocupante_o_404(db, ocupante_id)
    try:
        desvincular_whatsapp_ocupante(db, ocupante)
    except ValueError as exc:
        return _render_detalle_con_error(request, db, staff, persona, str(exc), tab_inicial="residentes")
    return RedirectResponse(
        f"/residentes/{persona.id}?ocupante_guardado=1", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/residentes/{persona_id}/ocupantes/{ocupante_id}/baja", response_class=HTMLResponse)
def customers_manage_ocupante_dar_de_baja(
    persona_id: str,
    ocupante_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
):
    persona = _get_persona_o_404(db, persona_id)
    ocupante = _ocupante_o_404(db, ocupante_id)
    try:
        dar_de_baja_ocupante(db, ocupante)
    except ValueError as exc:
        return _render_detalle_con_error(request, db, staff, persona, str(exc), tab_inicial="residentes")
    return RedirectResponse(
        f"/residentes/{persona.id}?ocupante_guardado=1", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post(
    "/residentes/{persona_id}/ocupantes/{ocupante_id}/confirmar", response_class=HTMLResponse
)
def customers_manage_ocupante_confirmar(
    persona_id: str,
    ocupante_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
):
    """Confirma un Ocupante pending (`.scratch/apartamento-catalogo-
    confirmacion`, ticket 07) — cualquier rol de staff, sin restricción
    (mismo patrón que el resto de esta gestión). Si es el primero de su
    Apartamento, queda como principal en el mismo acto (`confirmar_ocupante`)."""
    persona = _get_persona_o_404(db, persona_id)
    ocupante = _ocupante_o_404(db, ocupante_id)
    try:
        confirmar_ocupante(db, ocupante, staff)
    except (PermissionError, ValueError) as exc:
        return _render_detalle_con_error(request, db, staff, persona, str(exc), tab_inicial="residentes")
    except IntegrityError:
        # Carrera real (dos confirmaciones/promociones a la vez sobre el
        # mismo Apartamento) -- el índice único parcial de Ocupante ya la
        # bloqueó a nivel de BD, esto solo evita un 500 crudo.
        return _render_detalle_con_error(
            request, db, staff, persona,
            "Alguien más ya hizo un cambio en este apartamento -- "
            "actualiza la página e intenta de nuevo.",
            tab_inicial="residentes",
        )
    return RedirectResponse(
        f"/residentes/{persona.id}?ocupante_guardado=1", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post(
    "/residentes/{persona_id}/ocupantes/{ocupante_id}/promover", response_class=HTMLResponse
)
def customers_manage_ocupante_promover(
    persona_id: str,
    ocupante_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
):
    persona = _get_persona_o_404(db, persona_id)
    ocupante = _ocupante_o_404(db, ocupante_id)
    try:
        promover_a_principal(db, ocupante)
    except ValueError as exc:
        return _render_detalle_con_error(request, db, staff, persona, str(exc), tab_inicial="residentes")
    except IntegrityError:
        return _render_detalle_con_error(
            request, db, staff, persona,
            "Alguien más ya hizo un cambio en este apartamento -- "
            "actualiza la página e intenta de nuevo.",
            tab_inicial="residentes",
        )
    return RedirectResponse(
        f"/residentes/{persona.id}?ocupante_guardado=1", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/residentes/{persona_id}/eliminar")
def customers_manage_delete(
    persona_id: str,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
):
    """Elimina (anonimiza) un residente. **Solo ADMIN** — acción destructiva
    (ADR-0005); la ruta se protege server-side, la UI no es la única barrera."""
    persona = _get_persona_o_404(db, persona_id)
    anonimizar_persona(db, persona)
    return RedirectResponse(
        "/residentes?eliminado=1", status_code=status.HTTP_303_SEE_OTHER
    )
