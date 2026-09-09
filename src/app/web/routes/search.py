# -*- coding: utf-8 -*-
"""
Ruta `/consultar` — consultar el estado de un paquete (vista pública, sin
sesión).

Busca SOLO por `access_code` o `guide_number` exactos (Grupo 2 de
`ajustes-post-referencia-funcional/REQUERIMIENTOS.md`) — a propósito, NUNCA
por teléfono: el `access_code` únicamente lo conoce quien anunció, así que es
la única llave de consulta pública. El timeline (con actor por hito, y
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

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from fastapi.responses import HTMLResponse

from sqlalchemy import or_

from app.domain.apartamento_service import listar_catalogo_por_torre
from app.domain.cobro import Cobro
from app.domain.cobro_service import (
    calcular_cobro,
    listar_motivos_anulacion,
    obtener_tarifas_vigentes,
)
from app.domain.ocupante_service import residentes_por_torre_apartamento
from app.domain.paquete import CondicionPaquete, EstadoPaquete, Paquete, TipoPaquete
from app.domain.paquete_correccion_service import candidatos_correccion
from app.domain.paquete_foto_service import listar_fotos
from app.domain.paquete_service import es_primera_entrega_a_telefono
from app.domain.paquete_timeline_service import dias_desde_recibido, timeline_de_paquete
from app.domain.persona import Persona
from app.domain.saldo_contra_entrega_service import (
    personas_con_historial_en_apartamento,
    saldo_de_persona,
)

from ..db import get_db
from ..rate_limit import rate_limit
from ..security import SESSION_KEY
from ..templating import templates

router = APIRouter()

_MENSAJE_RATE_LIMIT = "Demasiados intentos. Espera un momento e inténtalo de nuevo."


@router.get("/consultar", response_class=HTMLResponse)
def search(
    request: Request,
    q: str = None,
    db: Session = Depends(get_db),
    # .scratch/migracion-por-anio, ticket 02: 10/60s por IP, mismo mecanismo
    # genérico ya usado en OTP/login/restablecer contraseña -- mitigación
    # parcial del riesgo aceptado de reciclar access_code entre años (ver
    # spec.md).
    permitido: bool = Depends(rate_limit("consultar_publico", 10, 60)),
):
    if not permitido:
        return templates.TemplateResponse(
            "search/form.html",
            {"request": request, "q": q or "", "error": _MENSAJE_RATE_LIMIT},
            status_code=429,
        )

    termino = (q or "").strip()
    if not termino:
        return templates.TemplateResponse(
            "search/form.html", {"request": request, "q": ""}
        )

    paquete = (
        db.query(Paquete)
        .filter(
            or_(Paquete.access_code == termino, Paquete.guide_number == termino)
        )
        .one_or_none()
    )
    if paquete is not None:
        contexto = {
            "request": request,
            "q": termino,
            "paquete": paquete,
            "timeline": timeline_de_paquete(db, paquete),
            "fotos": listar_fotos(db, paquete),
            "dias_desde_recibido": dias_desde_recibido(paquete),
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
            # .scratch/dinero-contra-entrega, ticket 03: mismo criterio que
            # `packages.py::_listar` (issue de paridad encontrado en
            # code-review) -- acá solo hay UN paquete, se resuelve directo
            # sin batch.
            persona_destino = (
                db.query(Persona)
                .filter(Persona.telefono == paquete.recipient_phone)
                .one_or_none()
            )
            if persona_destino is not None:
                # Pedido explícito del cliente: "Saldo: $X" (a favor o en
                # contra) del propio destinatario, debajo de su nombre --
                # mismo criterio que `packages.py::_listar`.
                saldo = saldo_de_persona(db, persona_destino.id)
                if saldo != 0:
                    paquete.saldo_actual = saldo
                if persona_destino.apartamento_actual_id is not None:
                    contexto["personas_con_saldo"] = personas_con_historial_en_apartamento(
                        db, persona_destino.apartamento_actual_id
                    )
        # Issue 314/316 (.scratch/pendientes-cliente): el modal Entregar de
        # esta vista es un DUPLICADO del de `/paquetes` (`packages.py::
        # _listar` calcula esto mismo en batch para su propia lista) -- acá
        # solo hay UN paquete, así que se resuelve directo, sin batch,
        # gated igual que el propio modal (staff + RECIBIDO) para no pagar
        # el query de más en la inmensa mayoría de consultas anónimas.
        if request.session.get(SESSION_KEY) and paquete.estado == EstadoPaquete.RECIBIDO:
            paquete.primera_entrega_a_telefono = es_primera_entrega_a_telefono(
                db, paquete.recipient_phone
            )
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
            persona_destino = (
                db.query(Persona)
                .filter(Persona.telefono == paquete.recipient_phone)
                .one_or_none()
            )
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
        return templates.TemplateResponse("search/form.html", contexto)

    return templates.TemplateResponse(
        "search/form.html", {"request": request, "q": termino, "sin_resultados": True}
    )
