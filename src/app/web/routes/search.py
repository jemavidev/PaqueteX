# -*- coding: utf-8 -*-
"""
Ruta `/consultar` — consultar el estado de un paquete (vista pública, sin
sesión).

Busca SOLO por `access_code` o `guide_number` exactos (Grupo 2 de
`ajustes-post-referencia-funcional/REQUERIMIENTOS.md`) — a propósito, NUNCA
por teléfono: el `access_code` únicamente lo conoce quien anunció, así que es
la única llave de consulta pública (la guía NO lo es: puede repetirse, ver el ticket 09 en
`renderizar_busqueda`). El timeline (con actor por hito, y
`dias_desde_recibido`) vive en `paquete_timeline_service` — compartido con
`/mis-paquetes`, que cuenta la misma historia del mismo paquete para el
cliente autenticado.

Botones "Entregar"/"Recibir" (issue 124/171, staff únicamente): esta vista
sigue sin `Depends(current_staff)` -- el gate real de AMBOS vive en el
endpoint que el form de cada modal termina llamando (`/paquetes/{id}/recibir`,
`/paquetes/{id}/entregar`, los mismos que usa `/paquetes`), no acá. El
contexto extra que necesita el modal "Recibir" (catálogo de torres, tipos,
condiciones, residentes de la unidad, candidatos de corrección) solo se
calcula cuando SÍ hay una sesión de staff activa -- evita ese trabajo de más
en la inmensa mayoría de las consultas, que son de residentes anónimos sin
sesión.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from fastapi.responses import HTMLResponse

from sqlalchemy import func, or_

from app.domain.apartamento_service import listar_catalogo_por_torre
from app.domain.cobro import Cobro
from app.domain.cobro_service import (
    calcular_cobro,
    listar_motivos_anulacion,
    obtener_tarifas_vigentes,
)
from app.domain.ocupante_service import residentes_por_torre_apartamento
from app.domain.paquete import CondicionPaquete, EstadoPaquete, Paquete, TipoPaquete
from app.domain.paquete_correccion_service import candidatos_correccion, fingerprint_candidatos
from app.domain.paquete_foto_service import listar_fotos
from app.domain.paquete_service import es_primera_entrega, persona_destinataria, primera_entrega_verificable
from app.domain.paquete_timeline_service import dias_desde_recibido, timeline_de_paquete
from app.domain.persona import Persona
from app.domain.saldo_contra_entrega_service import saldo_de_persona

from ..db import get_db
from ..rate_limit import RateLimiter, get_rate_limiter
from ..security import SESSION_KEY
from ..templating import templates

router = APIRouter()

_MENSAJE_RATE_LIMIT = "Demasiados intentos. Espera un momento e inténtalo de nuevo."


def _resolver_persona_destino(db: Session, paquete: Paquete):
    """Issue 393: antes un duplicado del algoritmo de `packages.py`; ahora la regla compartida vive en
    `paquete_service.persona_destinataria` (nunca el primero de varios homónimos)."""
    return persona_destinataria(db, paquete)


@router.get("/consultar", response_class=HTMLResponse)
def search(
    request: Request,
    q: str = None,
    db: Session = Depends(get_db),
    # .scratch/migracion-por-anio, ticket 02: 10/60s por IP, mismo mecanismo
    # genérico ya usado en OTP/login/restablecer contraseña -- mitigación
    # parcial del riesgo aceptado de reciclar access_code entre años (ver
    # spec.md).
    limiter: RateLimiter = Depends(get_rate_limiter),
):
    # Issue 386 (.scratch/pendientes-cliente): el staff con sesión no cuenta -- usa esta vista para recibir y
    # entregar, y como el contador es por IP, sus consultas le gastaban el cupo al público de la misma red.
    permitido = True
    if not request.session.get(SESSION_KEY):
        ip = request.client.host if request.client else "desconocido"
        try:
            permitido = limiter.permitir(f"consultar_publico:{ip}", 10, 60)
        except Exception:
            permitido = True  # fail-open, igual que `rate_limit`
    if not permitido:
        return templates.TemplateResponse(
            "search/form.html",
            {"request": request, "q": q or "", "error": _MENSAJE_RATE_LIMIT},
            status_code=429,
        )
    return renderizar_busqueda(request, db, q)


# Issue 387 (.scratch/pendientes-cliente): pasados estos días en Entregado o Cancelado, quien no es staff ve el paquete
# ofuscado. Anunciados y Recibidos se muestran siempre completos; el staff ve todo siempre.
_DIAS_HASTA_OFUSCAR = 15


def _consulta_ofuscada(request: Request, paquete: Paquete) -> bool:
    if request.session.get(SESSION_KEY):
        return False
    cerrado_en = {
        EstadoPaquete.ENTREGADO: paquete.delivered_at,
        EstadoPaquete.CANCELADO: paquete.cancelled_at,
    }.get(paquete.estado)
    return cerrado_en is not None and cerrado_en < datetime.now(timezone.utc) - timedelta(days=_DIAS_HASTA_OFUSCAR)


def _iniciales(nombre: str) -> str:
    """ "CATALINA PARRA" -> "C. P." """
    return " ".join(f"{palabra[0]}." for palabra in (nombre or "").split()) or "—"


def _telefono_enmascarado(telefono: str) -> str:
    """Solo los últimos 4 dígitos, para que el dueño reconozca su paquete sin exponer el número."""
    digitos = "".join(c for c in (telefono or "") if c.isdigit())
    return f"••• ••• {digitos[-4:]}" if len(digitos) >= 4 else "N/D"


def renderizar_busqueda(
    request: Request,
    db: Session,
    q: str,
    error: str = None,
    status_code: int = 200,
    entregar_error_motivo: bool = False,
    recibir_error_guia: str = None,
) -> HTMLResponse:
    """Cuerpo de `/consultar` (GET), extraído para reusarse desde
    `packages.py::deliver_action` (pedido explícito del cliente, reportado
    en vivo): antes, un error de validación al "Entregar" desde ESTA vista
    (ej. "Anular cobro" sin motivo) hacía un `RedirectResponse` puro de
    vuelta a `/consultar?q=...` -- perdía el error por completo (ni
    siquiera se mostraba) y el modal quedaba cerrado, la vista "se
    reiniciaba" sin ninguna pista de qué pasó. Ahora `deliver_action`
    llama esta función DIRECTO (sin redirect) pasando `error` +
    `entregar_error_motivo=True`, así el modal reabre con el error inline,
    igual que ya hace `/paquetes`."""
    termino = (q or "").strip()
    if not termino:
        return templates.TemplateResponse(
            "search/form.html", {"request": request, "q": ""}
        )

    # Ticket 09 (`.scratch/captura-guia-lector-camara`): "cero, uno o varios" en vez de "cero o uno".
    # La Guía es una referencia, no una llave (glosario): un envío de varias cajas la comparte, y antes
    # `.one_or_none()` reventaba con `MultipleResultsFound` (500) en cuanto dos paquetes la tenían. El
    # `access_code` SÍ es único, así que si el término es el código de un paquete, ese gana aunque otro
    # paquete tenga esa misma cadena como guía.
    # Issue 410: ninguna búsqueda distingue mayúsculas ("za9325" encuentra "ZA9325"), en el código ni en la guía.
    buscado = termino.upper()
    coincidencias = (
        db.query(Paquete)
        .filter(
            or_(func.upper(Paquete.access_code) == buscado, func.upper(Paquete.guide_number) == buscado)
        )
        .order_by(Paquete.announced_at.desc(), Paquete.id)
        .all()
    )
    paquete = next((c for c in coincidencias if (c.access_code or "").upper() == buscado), None)
    if paquete is None and len(coincidencias) == 1:
        paquete = coincidencias[0]
    if paquete is None and len(coincidencias) > 1:
        contexto = {"request": request, "q": termino, "varios_paquetes": True}
        # Con sesión de staff, la lista para elegir (código de acceso, destinatario, estado); sin sesión,
        # NADA de datos -- una guía repetida por error no debe exponer el paquete de otra persona.
        if request.session.get(SESSION_KEY):
            contexto["coincidencias"] = [
                {"access_code": c.access_code, "destinatario": c.recipient_name, "estado": c.estado}
                for c in coincidencias
            ]
        return templates.TemplateResponse("search/form.html", contexto)
    if paquete is not None and _consulta_ofuscada(request, paquete):
        # Issue 387: se sigue encontrando, pero solo con estado y fechas -- sin fotos, guía, apartamento, nombres del
        # staff ni datos completos del destinatario.
        return templates.TemplateResponse(
            "search/form.html",
            {
                "request": request,
                "q": termino,
                "paquete": paquete,
                "ofuscado": True,
                "nombre_visible": _iniciales(paquete.recipient_name),
                "telefono_visible": _telefono_enmascarado(paquete.recipient_phone or paquete.announced_by_phone),
                "timeline": [
                    {"titulo": h["titulo"], "cuando": h["cuando"], "motivo": None, "actor": None,
                     "tipo": None, "condicion": None, "guia": None}
                    for h in timeline_de_paquete(db, paquete)
                ],
                "fotos": [],
                "dias_desde_recibido": dias_desde_recibido(paquete),
            },
            status_code=status_code,
        )
    if paquete is not None:
        contexto = {
            "request": request,
            "q": termino,
            "paquete": paquete,
            "timeline": timeline_de_paquete(db, paquete),
            "fotos": listar_fotos(db, paquete),
            "dias_desde_recibido": dias_desde_recibido(paquete),
            "error": error,
            "entregar_error_motivo": entregar_error_motivo,
            # Ticket 04 (revisión): reabre el modal Recibir con el rechazo por guía larga DENTRO, igual que
            # `/paquetes` (`error_guia`); `None` en cualquier otro caso.
            "recibir_error_guia": recibir_error_guia,
        }
        # Issue 171 (.scratch/pendientes-cliente): mismo contexto que ya
        # arma `packages.py` para el modal `modal_recibir` compartido --
        # nada nuevo, solo reusado acá para el único Paquete de esta vista.
        if request.session.get(SESSION_KEY) and paquete.estado == EstadoPaquete.ANUNCIADO:
            contexto.update(
                {
                    "tipos": list(TipoPaquete),
                    "condiciones": list(CondicionPaquete),
                    "catalogo_torres": listar_catalogo_por_torre(db),
                    "residentes_por_unidad": residentes_por_torre_apartamento(db),
                    "candidatos_correccion": candidatos_correccion(db, paquete),
                }
            )
            # Análisis de diseño 2026-09-18: mismo criterio que
            # `packages.py::_listar` -- ver docstring de `fingerprint_candidatos`.
            contexto["candidatos_fingerprint"] = fingerprint_candidatos(
                contexto["candidatos_correccion"]
            )
            # .scratch/dinero-contra-entrega, ticket 03: mismo criterio que
            # `packages.py::_listar` (issue de paridad encontrado en
            # code-review) -- acá solo hay UN paquete, se resuelve directo
            # sin batch.
            persona_destino = _resolver_persona_destino(db, paquete)
            if persona_destino is not None:
                # Pedido explícito del cliente: "Saldo: $X" (a favor o en
                # contra) del propio destinatario, debajo de su nombre --
                # mismo criterio que `packages.py::_listar`.
                saldo = saldo_de_persona(db, persona_destino.id)
                if saldo != 0:
                    paquete.saldo_actual = saldo
                # Pedido explícito del cliente, reportado en vivo: mismo
                # criterio que `packages.py::_listar` -- la caja siempre se
                # habilita para el destinatario de ESTE paquete (tenga o no
                # historial/apartamento). "Descontar del saldo de" (elegir
                # a OTRA persona) se removió (pedido explícito) -- el monto
                # siempre se registra contra este mismo destinatario.
                contexto["persona_destino_saldo_id"] = persona_destino.id
        # Issue 314/316 (.scratch/pendientes-cliente): el modal Entregar de
        # esta vista es un DUPLICADO del de `/paquetes` (`packages.py::
        # _listar` calcula esto mismo en batch para su propia lista) -- acá
        # solo hay UN paquete, así que se resuelve directo, sin batch,
        # gated igual que el propio modal (staff + RECIBIDO) para no pagar
        # el query de más en la inmensa mayoría de consultas anónimas.
        if request.session.get(SESSION_KEY) and paquete.estado == EstadoPaquete.RECIBIDO:
            paquete.primera_entrega_a_telefono = es_primera_entrega(db, paquete)
            paquete.primera_entrega_no_verificable = not primera_entrega_verificable(paquete)
            # .scratch/cobro-bodegaje, ticket 02: mismo criterio que arriba
            # -- acá solo hay UN paquete, se resuelve directo sin batch.
            contexto["cobro_desglose"] = calcular_cobro(
                paquete,
                obtener_tarifas_vigentes(db),
                datetime.now(timezone.utc),
                paquete.primera_entrega_a_telefono,
            )
            contexto["motivos_anulacion_cobro"] = listar_motivos_anulacion(db)
            # .scratch/dinero-contra-entrega, ticket 04: mismo criterio que
            # arriba -- acá solo hay UN paquete, se resuelve directo sin
            # batch (issue de paridad encontrado en code-review).
            persona_destino = _resolver_persona_destino(db, paquete)
            if persona_destino is not None:
                saldo = saldo_de_persona(db, persona_destino.id)
                if saldo != 0:
                    # Pedido explícito del cliente: mismo dato que arriba
                    # (ANUNCIADO/Recibir), acá para Entregar.
                    paquete.saldo_actual = saldo
                if saldo < 0:
                    paquete.saldo_pendiente = -saldo
        # .scratch/cobro-bodegaje, ticket 05: "visible para cualquier
        # current_staff" (spec.md) -- gated a sesión de staff, igual que el
        # resto de este archivo, a propósito NUNCA visible en la consulta
        # pública/anónima ni en el portal del propio residente (issue de
        # paridad encontrado en code-review: /paquetes ya lo mostraba en su
        # modal "Ver", /consultar nunca lo mostró).
        if request.session.get(SESSION_KEY) and paquete.estado == EstadoPaquete.ENTREGADO:
            contexto["cobro"] = (
                db.query(Cobro).filter(Cobro.paquete_id == paquete.id).one_or_none()
            )
        return templates.TemplateResponse(
            "search/form.html", contexto, status_code=status_code
        )

    return templates.TemplateResponse(
        "search/form.html", {"request": request, "q": termino, "sin_resultados": True}
    )
