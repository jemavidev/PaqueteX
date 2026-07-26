# -*- coding: utf-8 -*-
"""
Ruta `/announce-new` — declarar una unidad en lote (staff).

Gated por `current_staff` (CUALQUIER rol — tarea operativa rutinaria, no
administrativa; a diferencia de `/admin/staff`). Reutiliza
`get_or_create_apartamento` + `declare_unit` SIN cambios de dominio: los
teléfonos+nombres declarados aquí se unen TODOS al Apartamento a la vez (la
herencia real, §6.4). Solo residentes CON teléfono pueden ser miembros
(ADR-0003 — un nombre sin teléfono no tiene existencia propia fuera del
snapshot de un Paquete puntual, y no puede unirse a esta unidad).

`POST` es `async` (a diferencia del resto de rutas, síncronas) para leer las
filas nombre/teléfono repetidas del formulario vía `request.form()`
(`Form(...)` no soporta listas de pares por posición).
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.domain.apartamento_service import declare_unit, get_or_create_apartamento
from app.domain.usuario import Usuario

from ..db import get_db
from ..security import current_staff
from ..templating import templates

router = APIRouter()


def _blank_to_none(valor):
    valor = (valor or "").strip()
    return valor or None


@router.get("/announce", response_class=HTMLResponse)
def announce_new_form(request: Request, staff: Usuario = Depends(current_staff)):
    return templates.TemplateResponse(
        "announce_new/form.html", {"request": request, "staff": staff}
    )


@router.post("/announce", response_class=HTMLResponse)
async def announce_new_submit(
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
):
    form = await request.form()
    conjunto = _blank_to_none(form.get("conjunto"))
    torre = _blank_to_none(form.get("torre"))
    apartamento_v = _blank_to_none(form.get("apartamento"))
    nombres = [str(n).strip() for n in form.getlist("nombre")]
    telefonos = [str(t).strip() for t in form.getlist("telefono")]

    def _error(mensaje: str):
        return templates.TemplateResponse(
            "announce_new/form.html",
            {
                "request": request,
                "staff": staff,
                "error": mensaje,
                "conjunto": conjunto or "",
                "torre": torre or "",
                "apartamento": apartamento_v or "",
            },
            status_code=400,
        )

    if not (conjunto and torre and apartamento_v):
        return _error("Completa Conjunto, Torre y Apartamento.")

    miembros = []
    for nombre, telefono in zip(nombres, telefonos):
        if not nombre and not telefono:
            continue  # fila vacía: se ignora
        if not nombre or not telefono:
            return _error("Cada residente necesita nombre Y teléfono.")
        miembros.append((telefono, nombre))

    if not miembros:
        return _error("Agrega al menos un residente (nombre + teléfono).")

    apto = get_or_create_apartamento(db, conjunto, torre, apartamento_v)
    personas = declare_unit(db, apto, miembros)

    return templates.TemplateResponse(
        "announce_new/form.html",
        {
            "request": request,
            "staff": staff,
            "creado": personas,
            "apartamento_creado": apto,
        },
    )
