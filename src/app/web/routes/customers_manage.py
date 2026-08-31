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
    agregar_telefono_a_persona_de_ocupante,
    agregar_whatsapp_a_persona_de_ocupante,
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
    canal_evento_editable,
    eventos_bloqueados_para,
    guardar_matriz_preferencias,
    matriz_preferencias,
)
from app.domain.telefono import normalizar_telefono
from app.domain.texto import normalizar_nombre
from app.domain.usuario import RolUsuario, Usuario

from ..db import get_db
from ..security import current_staff, require_admin
from ..templating import templates

router = APIRouter()

# Mismas 2 constantes de presentación que `customer_verify.py` (Llamada/
# WhatsApp sin proveedor conectado todavía) -- duplicadas a propósito, mismo
# patrón que `_blank_to_none` ya duplicado entre ambos archivos de ruta.
_CANALES_SIN_PROVEEDOR = {CanalNotificacion.LLAMADA}
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
        # Email (issue 251 seguimiento, .scratch/pendientes-cliente): el
        # modal "Editar" de la tab Residentes ahora también edita Email.
        o.email = persona.email if persona else None
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


_ESQUEMA_APARTAMENTO_RE = re.compile(r"^apt\s*(\d+)$", re.IGNORECASE)


def _buscar_residentes(db: Session, termino: str) -> list[Persona]:
    """Búsqueda extendida (Grupo 17, Ronda 2): teléfono, WhatsApp, email o
    nombre de la Persona misma, o torre/apartamento de su unidad.
    Resultados únicos, sin duplicar si varios criterios coinciden con la
    misma Persona.

    Issue 170 (.scratch/pendientes-cliente): ya no busca por
    `segundo_contacto` -- ese campo se eliminó por completo, ningún flujo
    real lo usaba.

    Issue 176 (.scratch/pendientes-cliente, seguimiento a [[175]]): ya NO
    busca por nombre de Ocupante resolviendo al Principal de su unidad --
    pedido explícito: "no aparezcan las personas que estan relacionadas
    con ese apartamento, solo la persona que busco", ahora que "Agrupar
    por apartamento" ([[174]]) cubre ese caso de uso (ver a todos los
    relacionados de una unidad) sin que la búsqueda de texto tenga que
    inferirlo. Efecto secundario aceptado: un Ocupante SIN teléfono/
    WhatsApp propio (sin ficha propia) ya no se puede encontrar por su
    nombre -- antes resolvía al Principal como sustituto, ahora no hay
    sustituto.

    Issue 177 (.scratch/pendientes-cliente): teléfono PARCIAL también
    matchea, no solo completo -- si `termino` no normaliza a un teléfono
    completo/válido pero son solo dígitos (ej. "3001", los últimos 4), se
    compara por coincidencia parcial contra el teléfono canónico guardado
    en vez de exigir el número exacto.

    Issue 178 (.scratch/pendientes-cliente): 2 cambios más, pedido
    explícito --
    (1) apartamento ahora se busca con el esquema `apt<número>` (ej.
    "apt302", espacio opcional, sin distinguir mayúsculas) -- match EXACTO
    contra el número, en CUALQUIER torre. Reemplaza el match parcial
    anterior contra el número de apartamento (que sin querer también
    encontraba unidades como "1302" al buscar "302") -- dígitos sueltos,
    SIN el prefijo `apt`, ya no buscan apartamento en absoluto. Torre sigue
    igual que antes (parcial, sin prefijo, no fue parte del pedido).
    (2) nuevos frentes por `whatsapp_usuario` y `email` de la Persona,
    mismo criterio parcial que el nombre."""
    encontradas: dict = {}  # id -> Persona, dedup preservando orden de hallazgo

    def _agregar_todas(personas):
        for p in personas:
            encontradas.setdefault(p.id, p)

    try:
        telefono = normalizar_telefono(termino)
    except ValueError:
        telefono = None

    filtros_persona = [
        Persona.nombre.ilike(f"%{termino}%"),
        Persona.whatsapp_usuario.ilike(f"%{termino}%"),
        Persona.email.ilike(f"%{termino}%"),
    ]
    if telefono is not None:
        filtros_persona.append(Persona.telefono == telefono)
    elif termino.strip().isdigit():
        filtros_persona.append(Persona.telefono.ilike(f"%{termino.strip()}%"))
    _agregar_todas(db.query(Persona).filter(or_(*filtros_persona)).all())

    match_apto = _ESQUEMA_APARTAMENTO_RE.match(termino.strip())
    if match_apto:
        apartamentos_match = (
            db.query(Apartamento).filter(Apartamento.apartamento == match_apto.group(1)).all()
        )
    else:
        apartamentos_match = (
            db.query(Apartamento).filter(Apartamento.torre.ilike(f"%{termino}%")).all()
        )
    if apartamentos_match:
        apto_ids = [a.id for a in apartamentos_match]
        _agregar_todas(
            db.query(Persona).filter(Persona.apartamento_actual_id.in_(apto_ids)).all()
        )

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


def _todos_los_residentes_activos(db: Session) -> list[Persona]:
    """TODOS los residentes activos, SIN paginar (issue 174, .scratch/
    pendientes-cliente) -- a diferencia de `_listar_todos_los_residentes`,
    que sí pagina a nivel de base de datos para el listado plano de
    siempre. La usa `customers_manage_search` SOLO como insumo para
    `_agrupar_por_apartamento` cuando no hay término de búsqueda: hace
    falta el universo completo de Personas para saber qué Apartamentos
    agrupar ANTES de paginar por apartamento, no por persona."""
    return db.query(Persona).filter(Persona.eliminado_en.is_(None)).order_by(Persona.nombre).all()


def _listar_principales(db: Session, pagina: int = 1):
    """Como `_listar_todos_los_residentes`, pero SOLO Personas que son
    Residente Principal activo de su unidad (issue 174 -- botón "Listar
    principales"). Join contra `Ocupante` para filtrar A NIVEL DE BASE DE
    DATOS antes de paginar, no un filtro en Python sobre una página ya
    recortada (que perdería principales que hubieran caído en otra
    página)."""
    query = (
        db.query(Persona)
        .join(Ocupante, Ocupante.persona_id == Persona.id)
        .filter(
            Persona.eliminado_en.is_(None),
            Ocupante.es_principal.is_(True),
            Ocupante.desvinculado_en.is_(None),
        )
        .order_by(Persona.nombre)
    )
    total = query.count()
    total_paginas = max(1, -(-total // _POR_PAGINA))
    pagina = max(1, min(pagina, total_paginas))
    personas = query.offset((pagina - 1) * _POR_PAGINA).limit(_POR_PAGINA).all()
    return personas, pagina, total_paginas


def _buscar_principales(db: Session, termino: str) -> list[Persona]:
    """Como `_buscar_residentes`, filtrado a Residente Principal activo
    (issue 174). Reusa `_buscar_residentes` + `ocupantes_activos_de_personas`
    (ya existente, mismo helper que usa `_adjuntar_ocupante`) en vez de
    duplicar la búsqueda extendida -- el resultado de una búsqueda con
    término ya es un conjunto chico, filtrar en Python acá no repite el
    problema de paginación que sí tiene `_listar_principales`."""
    candidatos = _buscar_residentes(db, termino)
    por_persona = ocupantes_activos_de_personas(db, [p.id for p in candidatos])
    return [p for p in candidatos if (por_persona.get(p.id) and por_persona[p.id].es_principal)]


def _agrupar_por_apartamento(db: Session, personas_en_alcance: list[Persona], pagina: int = 1):
    """Agrupa por Apartamento a TODOS los residentes activos de cada unidad
    referenciada por al menos una Persona en `personas_en_alcance` (issue
    174 -- botón "Agrupar por apartamento", pedido explícito: "incluso si
    ya se realizo una busqueda, con el fin de saber todos los integrantes
    de un mismo apartamento"). El grupo trae a TODOS los compañeros de
    unidad -- no solo a quien matcheó la búsqueda -- vía `_ocupantes_de`,
    ya existente (mismo helper que arma la tab "Residentes" de la ficha).

    Paginado por APARTAMENTO, no por persona -- mismo `_POR_PAGINA` que el
    resto de la vista, pero contando grupos en vez de filas.

    Personas sin apartamento asignado no arman grupo (nada que agrupar) --
    se devuelven aparte en `sin_apartamento`, SOLO en la página 1 (no tiene
    sentido paginarla junto a los grupos: son dos universos con su propia
    cuenta, mezclarlos en la misma paginación numérica confundiría más de
    lo que ayuda)."""
    apartamento_ids = {p.apartamento_actual_id for p in personas_en_alcance if p.apartamento_actual_id}
    sin_apartamento = [p for p in personas_en_alcance if not p.apartamento_actual_id]
    apartamentos = []
    if apartamento_ids:
        apartamentos = (
            db.query(Apartamento)
            .filter(Apartamento.id.in_(apartamento_ids))
            .order_by(Apartamento.torre, Apartamento.apartamento)
            .all()
        )
    total = len(apartamentos)
    total_paginas = max(1, -(-total // _POR_PAGINA))
    pagina = max(1, min(pagina, total_paginas))
    # `_ocupantes_de` hace 1 query por Ocupante para resolver su Persona
    # (N+1 ya existente, no introducido acá) -- tolerable donde antes se
    # llamaba (la ficha de UN Apartamento a la vez, ≤`MAX_OCUPANTES_ACTIVOS`
    # filas), acá se llama hasta `_POR_PAGINA` veces por página. Sin
    # reescribirlo a una consulta batched: esta vista es staff-only, de bajo
    # tráfico, y `_POR_PAGINA` acota el peor caso -- no vale la pena el
    # riesgo de tocar un helper compartido por este pedido puntual.
    pagina_apartamentos = apartamentos[(pagina - 1) * _POR_PAGINA : pagina * _POR_PAGINA]
    grupos = [{"apartamento": a, "ocupantes": _ocupantes_de(db, a)} for a in pagina_apartamentos]
    return grupos, (sin_apartamento if pagina == 1 else []), pagina, total_paginas


_VISTAS_VALIDAS = {"principales", "agrupado"}


def _peticion_en_vivo(request: Request) -> bool:
    """True si la petición viene del fetch en vivo de la barra de búsqueda
    (issue 173, .scratch/pendientes-cliente) -- mismo mecanismo que ya usa
    `packages._peticion_en_vivo`: el JS de `_busqueda_filtros.html` marca
    cada petición en segundo plano con este header."""
    return request.headers.get("X-Requested-With") == "fetch"


@router.get("/residentes", response_class=HTMLResponse)
def customers_manage_search(
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    q: str = None,
    pagina: int = 1,
    vista: str = None,
):
    termino = _blank_to_none(q)
    vista = vista if vista in _VISTAS_VALIDAS else None

    grupos = sin_apartamento = resultados = None
    if vista == "agrupado":
        # Alcance = la búsqueda activa, o TODOS los activos si no hay `q`
        # (issue 174, pedido explícito: agrupar debe funcionar incluso sin
        # haber buscado antes, no solo como refinamiento de una búsqueda).
        personas_en_alcance = _buscar_residentes(db, termino) if termino else _todos_los_residentes_activos(db)
        grupos, sin_apartamento, pagina_actual, total_paginas = _agrupar_por_apartamento(
            db, personas_en_alcance, pagina
        )
    else:
        if vista == "principales":
            if termino:
                resultados = _buscar_principales(db, termino)
                pagina_actual, total_paginas = 1, 1
            else:
                resultados, pagina_actual, total_paginas = _listar_principales(db, pagina)
        elif termino:
            resultados = _buscar_residentes(db, termino)
            pagina_actual, total_paginas = 1, 1
        else:
            resultados, pagina_actual, total_paginas = _listar_todos_los_residentes(db, pagina)
        _adjuntar_apartamentos(db, resultados)
        _adjuntar_ocupante(db, resultados)
        _adjuntar_comparte_apartamento(db, resultados)

    # Fetch en vivo (issue 173): devuelve SOLO el fragmento (paginación +
    # tabla/tarjetas), sin el layout completo -- mismo patrón que
    # `packages._render_lista`.
    plantilla = "customers_manage/_resultados.html" if _peticion_en_vivo(request) else "customers_manage/search.html"
    return templates.TemplateResponse(
        plantilla,
        {
            "request": request,
            "staff": staff,
            "q": termino or "",
            "vista": vista or "",
            "resultados": resultados,
            "grupos": grupos,
            "sin_apartamento": sin_apartamento,
            "pagina_actual": pagina_actual,
            "total_paginas": total_paginas,
            "url_whatsapp": url_whatsapp,
            "url_llamada": url_llamada,
            "etiqueta_torre_apto": _etiqueta_torre_apto,
            "nombre_mobile": _nombre_mobile,
        },
    )


_NUMERO_TORRE_RE = re.compile(r"(\d+)")


def _etiqueta_torre_apto(apartamento, fallback: str, *, compacto: bool = False) -> str:
    """Referencia compacta de una unidad (ej. "T 05 - APT 102", issue 69/70)
    -- `fallback` si no hay Apartamento asignado (distinto en la ficha,
    "Residentes", que en la tabla de la lista, "No Asignado").

    `compacto=True` (issue 277, mobile en la tabla de `/residentes`): sin
    espacios ni "APT" ("T05-102") -- misma info, ~la mitad de caracteres."""
    if apartamento is None:
        return fallback
    numero = _NUMERO_TORRE_RE.search(apartamento.torre)
    torre_corta = f"T {int(numero.group()):02d}" if numero else apartamento.torre
    if compacto:
        return f"{torre_corta.replace(' ', '')}-{apartamento.apartamento}"
    return f"{torre_corta} - APT {apartamento.apartamento}"


_NOMBRE_MOBILE_MAX_PALABRAS = 3
_NOMBRE_MOBILE_MAX_CHARS = 20


def _nombre_mobile(nombre: str) -> str:
    """Nombre acotado a palabras completas para la columna Nombre en
    mobile (issue 280, pedido explícito: "solo permite maximo 2 o 3
    palabras" -- nunca corta a mitad de palabra como el `truncate`/"…"
    que tenía antes, ver [[279]]). No modifica `nombre` en base de
    datos, solo lo que se muestra acá.

    2 palabras o menos: se muestra completo. 3 o más: las primeras 3 si
    esas 3 juntas no pasan de `_NOMBRE_MOBILE_MAX_CHARS`, si no las
    primeras 2 -- "según corresponda" del pedido original."""
    palabras = nombre.split()
    if len(palabras) <= 2:
        return nombre
    primeras_tres = " ".join(palabras[:_NOMBRE_MOBILE_MAX_PALABRAS])
    if len(primeras_tres) <= _NOMBRE_MOBILE_MAX_CHARS:
        return primeras_tres
    return " ".join(palabras[:2])


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
        # (`customer_verify.py`). Orden de columnas (issue 223, .scratch/
        # pendientes-cliente): WhatsApp inmediatamente a la derecha de SMS,
        # igual que `/mis-datos` desde el issue 221 -- distinto del orden
        # canónico del enum.
        "canales": [
            CanalNotificacion.SMS,
            CanalNotificacion.WHATSAPP,
            CanalNotificacion.EMAIL,
            CanalNotificacion.LLAMADA,
        ],
        "canales_sin_proveedor": _CANALES_SIN_PROVEEDOR,
        "etiqueta_canal": _ETIQUETA_CANAL,
        "eventos": EVENTOS,
        "matriz": matriz_preferencias(db, persona.id),
        # 2026-08-26 (pedido del cliente): SMS fuera de ANUNCIADO es
        # exclusivo de un ADMIN -- vacío (matriz completa editable) cuando
        # `staff` lo es, ver `canal_evento_editable`.
        "eventos_bloqueados": eventos_bloqueados_para(es_admin=staff.rol == RolUsuario.ADMIN),
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
        # residentes`).
        "residentes_por_unidad": residentes_por_torre_apartamento(db),
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
    # "" explícito (no None -- issue 69, extendido a email por issue 261):
    # este formulario SIEMPRE manda estos campos, así que acá "vacío" tiene
    # que poder significar "bórralo", no "no lo toques" (con `_blank_to_none`
    # nunca se podía vaciar una vez tenía un valor -- bug real reportado en
    # vivo). Ver el contrato de 3 estados en
    # `persona_service.update_datos_personales`.
    whatsapp_v = (whatsapp_usuario or "").strip()
    email_v = (email or "").strip()

    try:
        update_datos_personales(
            db,
            persona,
            nombre=_blank_to_none(nombre),
            email=email_v,
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
    para una forma variable así de limpia).

    2026-08-26 (pedido del cliente): un Operador tiene la misma restricción
    de SMS que un Residente -- solo ANUNCIADO (ver `canal_evento_editable`);
    un ADMIN edita la matriz completa. `combinaciones` excluye del todo las
    filas que este `staff` no puede tocar, para no pisarlas a `False` por
    simple omisión (ej. un ADMIN dejó SMS×Recibido activo, un Operador
    guarda otro cambio de la misma ficha sin querer tocar eso)."""
    persona = _get_persona_o_404(db, persona_id)
    form = await request.form()
    es_admin = staff.rol == RolUsuario.ADMIN

    combinaciones_editables = {
        (canal.value, evento.value)
        for canal in CanalNotificacion
        for evento in EVENTOS
        if canal not in _CANALES_SIN_PROVEEDOR
        and canal_evento_editable(canal, evento, es_admin=es_admin)
    }
    activos = {
        clave
        for clave in combinaciones_editables
        if form.get(f"pref_{clave[0]}_{clave[1]}") is not None
    }
    guardar_matriz_preferencias(db, persona.id, activos, combinaciones=combinaciones_editables)

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
        elif db.get(Persona, ocupante.persona_id).telefono is not None:
            # Editar un teléfono YA asociado (pedido del cliente,
            # `.scratch/pendientes-cliente/issues/35`) -- el principal se
            # sigue excluyendo (ver `editar_telefono_ocupante`); no hay hoy
            # una vía de staff para renombrar el teléfono PROPIO de un
            # principal, mismo estado que antes de este pedido.
            editar_telefono_ocupante(db, ocupante, telefono_v)
        else:
            # Persona ya vinculada por WhatsApp, sin Teléfono todavía --
            # AGREGA el canal sobre la MISMA Persona (issue 224, .scratch/
            # pendientes-cliente -- mismo fix de 217/213 en /mis-datos).
            agregar_telefono_a_persona_de_ocupante(db, ocupante, telefono_v)
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
        elif db.get(Persona, ocupante.persona_id).whatsapp_usuario is not None:
            editar_whatsapp_ocupante(db, ocupante, whatsapp_v)
        else:
            # Persona ya vinculada por Teléfono, sin WhatsApp todavía --
            # AGREGA el canal sobre la MISMA Persona (issue 224, .scratch/
            # pendientes-cliente -- mismo fix de 217/213 en /mis-datos).
            agregar_whatsapp_a_persona_de_ocupante(db, ocupante, whatsapp_v)
    except ValueError as exc:
        return _render_detalle_con_error(request, db, staff, persona, str(exc), tab_inicial="residentes")
    return RedirectResponse(
        f"/residentes/{persona.id}?ocupante_guardado=1", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post(
    "/residentes/{persona_id}/ocupantes/{ocupante_id}/editar", response_class=HTMLResponse
)
def customers_manage_ocupante_editar(
    persona_id: str,
    ocupante_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    nombre: str = Form(None),
    email: str = Form(None),
    telefono: str = Form(None),
    whatsapp_usuario: str = Form(None),
):
    """Guardado unificado -- Nombre, Email, Teléfono y WhatsApp de un
    Ocupante en un solo submit -- issue 251 (.scratch/pendientes-cliente,
    pedido explícito del cliente tras comparar con /mis-datos): mismo
    patrón que `customer_ocupante_editar` (issue 228), reemplaza los
    botones sueltos (✕/+ Teléfono, ✕/+ WhatsApp, "Actualizar") que tenía
    esta tab. Nombre/Email se agregaron en un seguimiento del mismo issue
    (primer intento los excluía a propósito por vivir también en la ficha
    propia del residente -- el cliente pidió incluirlos igual). Las rutas
    `/telefono`/`/whatsapp`/`/contacto` se quedan intactas para quien las
    use directo.

    Issue 263 (.scratch/pendientes-cliente, pedido explícito del cliente,
    "que se hable un mismo idioma siempre"): ya NO exige contacto previo
    -- si `ocupante.persona_id` es `None`, Teléfono/WhatsApp acá agregan
    el PRIMER contacto (`asociar_telefono_a_ocupante`/`asociar_whatsapp_
    a_ocupante`, mismas funciones que ya usaba el form suelto "Teléfono o
    WhatsApp / Agregar", ahora retirado de la vista -- un solo lugar para
    gestionar todo). Nombre sigue editable aunque no haya contacto
    (columna propia de `Ocupante`, `agregar_ocupante` ya lo documenta);
    Email no tiene dónde vivir sin una Persona -- si el Ocupante sigue
    sin contacto al final de este submit, cualquier Email tecleado se
    descarta en silencio (nada que romper: no había Persona antes, no la
    hay después).

    Se re-consulta la Persona ANTES de cada paso porque `editar_telefono_
    ocupante`/`editar_whatsapp_ocupante` pueden re-ligar `ocupante.
    persona_id` a una Persona distinta (issue 35) -- Nombre/Email deben
    aplicarse a la Persona VIGENTE al final, mismo criterio que
    `customer_ocupante_editar`."""
    persona = _get_persona_o_404(db, persona_id)
    ocupante = _ocupante_o_404(db, ocupante_id)

    nombre_v = _blank_to_none(nombre)
    # "" explícito (no None -- issue 261): este modal SIEMPRE manda este
    # campo, así que "vacío" tiene que poder significar "bórralo", mismo
    # contrato de 3 estados de `update_datos_personales` (issue 69 para
    # WhatsApp, extendido acá). NOTA: FastAPI's `Form(None)` ya colapsa un
    # "" enviado a `None` (mismo valor que "campo ausente") ANTES de que
    # el body de la ruta lo vea -- por eso acá, igual que en `whatsapp_v`
    # arriba, no se intenta distinguir "ausente" de "vacío" (no se puede a
    # esta altura): `(email or "").strip()` trata ambos como "bórralo",
    # que es lo correcto porque este modal siempre manda el campo.
    email_v = (email or "").strip()
    telefono_v = _blank_to_none(telefono)
    whatsapp_v = _blank_to_none(whatsapp_usuario)

    try:
        if ocupante.persona_id is None:
            # Issue 263: primer contacto -- `asociar_*_a_ocupante` exige
            # justamente esto (persona_id todavía `None`), mismas funciones
            # que ya usaba el form suelto retirado. Teléfono manda si vienen
            # los dos (mismo criterio que `agregar_ocupante`).
            if telefono_v is not None:
                asociar_telefono_a_ocupante(db, ocupante, telefono_v)
                if whatsapp_v is not None:
                    agregar_whatsapp_a_persona_de_ocupante(db, ocupante, whatsapp_v)
            elif whatsapp_v is not None:
                asociar_whatsapp_a_ocupante(db, ocupante, whatsapp_v)
        else:
            if telefono_v is not None:
                ocupante_persona = db.get(Persona, ocupante.persona_id)
                if ocupante_persona.telefono is None:
                    agregar_telefono_a_persona_de_ocupante(db, ocupante, telefono_v)
                else:
                    editar_telefono_ocupante(db, ocupante, telefono_v)

            if whatsapp_v is not None:
                ocupante_persona = db.get(Persona, ocupante.persona_id)
                if ocupante_persona.whatsapp_usuario is None:
                    agregar_whatsapp_a_persona_de_ocupante(db, ocupante, whatsapp_v)
                else:
                    editar_whatsapp_ocupante(db, ocupante, whatsapp_v)

        if ocupante.persona_id is not None:
            if nombre_v is not None or email_v is not None:
                ocupante_persona = db.get(Persona, ocupante.persona_id)
                update_datos_personales(db, ocupante_persona, nombre=nombre_v, email=email_v)
        elif nombre_v is not None:
            # Sin Persona (ni antes ni después de este submit) -- Nombre
            # sigue siendo columna propia de `Ocupante`, editable igual
            # (`agregar_ocupante` ya documenta este caso). Email no tiene
            # dónde vivir sin Persona -- se descarta en silencio.
            ocupante.nombre = normalizar_nombre(nombre_v)
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
    """Issue 259/260 (.scratch/pendientes-cliente, pedido explícito del
    cliente): a diferencia del autoservicio (`customer_ocupante_salir`),
    acá el staff SÍ puede eliminar al Principal aunque queden otros
    Ocupantes activos -- promueve automáticamente al más antiguo de ellos
    con Teléfono o WhatsApp propio (`created_at` ascendente) ANTES de dar
    de baja, mismo patrón/orden que `mover_ocupante` (issue 159), también
    exclusivo de staff. `dar_de_baja_ocupante` mantiene su guard estricto
    para el resto de sus llamadores (ver su docstring)."""
    persona = _get_persona_o_404(db, persona_id)
    ocupante = _ocupante_o_404(db, ocupante_id)
    if ocupante.es_principal:
        candidato = (
            db.query(Ocupante)
            .filter(
                Ocupante.apartamento_id == ocupante.apartamento_id,
                Ocupante.id != ocupante.id,
                Ocupante.desvinculado_en.is_(None),
                Ocupante.persona_id.isnot(None),
            )
            .order_by(Ocupante.created_at.asc())
            .first()
        )
        if candidato is not None:
            promover_a_principal(db, candidato)  # degrada a `ocupante` en el acto
        elif hay_otro_ocupante_activo(db, ocupante.apartamento_id, ocupante.id):
            return _render_detalle_con_error(
                request, db, staff, persona,
                "Es Principal y ninguno de los otros Residentes activos de su "
                "unidad tiene Teléfono ni WhatsApp propio para sucederlo -- "
                "agregale contacto a alguno desde tab Residentes antes de "
                "eliminarlo.",
                tab_inicial="residentes",
            )
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
