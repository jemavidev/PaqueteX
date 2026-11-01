# -*- coding: utf-8 -*-
"""
Ruta `/dinero` — vista "Dinero" (pedido del cliente 2026-11-01): una sola lista con todo movimiento de dinero
(saldo contra entrega, cobros, entregas sin cobrar), filtros en la URL y una línea de totales. Cualquier rol de staff,
mismo criterio que `/residentes/movimientos-saldo-contra-entrega`. La lógica vive en `dinero_service`.

Búsqueda en vivo: mismo mecanismo que `/paquetes` (`components/_busqueda_filtros.html`) -- con el header
`X-Requested-With: fetch` responde solo el fragmento `dinero/_resultados.html`.
"""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.domain.dinero_service import GRUPO_SIN_COBRAR, GRUPOS, TIPOS, FiltrosDinero, listar_dinero, operadores
from app.domain.usuario import Usuario

from ..db import get_db
from ..security import current_staff
from ..templating import templates

router = APIRouter()


def _uuid_o_none(valor: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID((valor or "").strip())
    except ValueError:
        return None


def _fecha_o_none(valor: str | None) -> date | None:
    try:
        return date.fromisoformat((valor or "").strip())
    except ValueError:
        return None


def _filtros(q, grupo, tipo, operador, desde, hasta, solo_deudas, cliente) -> FiltrosDinero:
    """Lo que llega por la URL, validado: un valor inválido (grupo/tipo desconocido, fecha o id mal escritos) no
    filtra. La barra solo usa `grupo` (los 4 íconos); `tipo` (uno solo) y `solo_deudas=1` (hoy el grupo "Sin
    cobrar") se siguen aceptando para que un enlace viejo funcione."""
    if not grupo and solo_deudas:
        grupo = GRUPO_SIN_COBRAR
    return FiltrosDinero(
        q=(q or "").strip(),
        grupo=grupo if grupo in GRUPOS else None,
        tipo=tipo if tipo in TIPOS else None,
        operador_id=_uuid_o_none(operador),
        desde=_fecha_o_none(desde),
        hasta=_fecha_o_none(hasta),
        cliente_id=_uuid_o_none(cliente),
    )


def _params_url(filtros: FiltrosDinero) -> dict:
    """Los filtros vigentes como querystring (paginación, enlace del cliente); vacíos fuera."""
    params = {
        "q": filtros.q,
        "grupo": filtros.grupo or "",
        "tipo": filtros.tipo or "",
        "operador": str(filtros.operador_id) if filtros.operador_id else "",
        "desde": filtros.desde.isoformat() if filtros.desde else "",
        "hasta": filtros.hasta.isoformat() if filtros.hasta else "",
        "cliente": str(filtros.cliente_id) if filtros.cliente_id else "",
    }
    return {clave: valor for clave, valor in params.items() if valor}


@router.get("/dinero", response_class=HTMLResponse)
def dinero(
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    q: str = None,
    grupo: str = None,
    tipo: str = None,
    operador: str = None,
    desde: str = None,
    hasta: str = None,
    solo_deudas: str = None,
    cliente: str = None,
    pagina: str = "1",
):
    filtros = _filtros(q, grupo, tipo, operador, desde, hasta, solo_deudas, cliente)
    resultado = listar_dinero(db, filtros, int(pagina) if pagina.isdigit() else 1)
    params = _params_url(filtros)
    # Tocar un cliente filtra por él: se quita `q` (con lo que se lo encontró) y se conserva el resto.
    params_cliente = {clave: valor for clave, valor in params.items() if clave not in ("q", "cliente")}
    contexto = {
        "request": request,
        "staff": staff,
        "filtros": filtros,
        "resultado": resultado,
        "params": params,
        "params_cliente": params_cliente,
        "operadores": operadores(db),
    }
    if request.headers.get("X-Requested-With") == "fetch":
        return templates.TemplateResponse("dinero/_resultados.html", contexto)
    return templates.TemplateResponse("dinero/list.html", contexto)
