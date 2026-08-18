# -*- coding: utf-8 -*-
"""
Ruta `/announce` — identificar y anunciar un paquete, rápido (staff).

Rediseño completo (`.scratch/announce-rapido`): reemplaza el formulario
viejo de 3 bloques (Apartamento/Residentes/Anunciar, desconectados entre
sí) por UN campo de texto único con detección de formato y resolución en
vivo. "Declarar apartamento sin anunciar nada" se retiró de esta ruta --
vive en `/residentes` (enlace visible desde acá).

Gated por `current_staff` (cualquier rol), igual que antes.

Detección de formato del campo único (re-aplicada SIEMPRE en el servidor --
`/announce/identificar` es la única fuente de verdad, el cliente no
clasifica nada, solo dispara la petición tras un debounce):
  - empieza en `3`, todo dígitos (10 exactos) → Teléfono.
  - empieza en `0`/`1`, todo dígitos → Torre+Apartamento (ticket 05):
    primeros 2 dígitos = Torre (01-10), el resto = Apartamento tal cual.
  - empieza con una letra (mínimo 3 caracteres) → usuario de WhatsApp.
  - cualquier otro caso → nada que resolver.

Tres caminos para identificar a quién se le anuncia (POST /announce):
  1. Teléfono/WhatsApp directo (ticket 04) -- la Persona resuelta (o
     recién creada) es Anunciante Y Destinatario (`Destinatario.yo_mismo()`).
  2. Un residente YA EXISTENTE elegido de la lista de una unidad (ticket
     05) -- `ocupante_id`, resuelto vía `Destinatario.ocupante(id)`; el
     Anunciante es la Persona propia del Ocupante o, si no tiene, la del
     Principal de su unidad (`anunciante_para_ocupante`) -- SALVO que ya se
     conozca a quien llamó (ver más abajo).
  3. Un residente NUEVO dentro de una unidad (ticket 05) -- `torre` +
     `apartamento` + `nombre` (+ `contacto` opcional): da de alta el
     Ocupante (`agregar_ocupante` tal cual, nace `pending`) y anuncia en el
     mismo paso, mismo mecanismo del camino 2.

Teléfono/WhatsApp con co-residentes (ticket 02, `.scratch/announce-
residente-correcto`): si la Persona que resuelve el camino 1 es Ocupante
activo de una unidad con MÁS de un residente activo, `GET /announce/
identificar` deja de mostrar la tarjeta directa -- en su lugar muestra la
MISMA pantalla de unidad del camino 2/3 (`_identificar_unidad.html`,
`_unidad_con_coresidentes`), con esa Persona marcada "Anunciante" en la
lista. Sin importar a qué residente se le anuncie desde ahí (existente o
nuevo), el Anunciante del Paquete es SIEMPRE quien se identificó por
Teléfono/WhatsApp -- nunca `anunciante_para_ocupante` -- gracias a que
`telefono`/`whatsapp_usuario` viajan como campos ocultos ADICIONALES junto
a `ocupante_id`/`torre`+`apartamento` (ver `_anunciar_para` más abajo). El
camino Torre+Apto directo no cambia: ahí nunca se conoce con certeza quién
llama, así que sigue cayendo en `anunciante_para_ocupante`.

Los tres caminos comparten el mismo botón doble Anunciar/Recibir (ticket 06,
`components/_persona_resuelta.html` e `_identificar_unidad.html`) -- ambos
son `type="submit"` del MISMO form, distinguidos por `accion` (`name="accion"
value="anunciar|recibir"`, ver `components/_botones.html`). `announce()` se
llama exactamente igual en los dos casos; la única diferencia es qué se
renderiza al terminar: con `accion=="recibir"` la respuesta incluye,
además del toast de siempre, el modal de recepción YA ABIERTO
(`components/_recibir_paquete.html`, el mismo componente/JS que usa
`/paquetes` -- requisito duro del ticket, no se reimplementa) scoped al
`paquete_id` recién creado. Completar ESE formulario sigue transicionando a
RECIBIDO vía la ruta `/paquetes/{id}/recibir` existente, sin cambios.
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.domain.apartamento import Apartamento
from app.domain.apartamento_service import listar_catalogo_por_torre, resolver_apartamento
from app.domain.contacto import clasificar_contacto
from app.domain.notification_sender import NotificationSender
from app.domain.notificacion_service import preparar_notificacion
from app.domain.ocupante import Ocupante
from app.domain.ocupante_service import (
    agregar_ocupante,
    anunciante_para_ocupante,
    listar_ocupantes,
    mensaje_ya_ocupante_activo,
    mover_ocupante,
    ocupante_activo_de_persona,
    ocupante_activo_por_contacto,
    residentes_por_torre_apartamento,
)
from app.domain.paquete import CondicionPaquete, EstadoPaquete, TipoPaquete
from app.domain.paquete_correccion_service import candidatos_correccion
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona import Persona
from app.domain.persona_service import buscar_persona_por_telefono, buscar_persona_por_whatsapp
from app.domain.usuario import Usuario

from ..db import get_db
from ..notifications import enviar_en_segundo_plano, get_notification_sender
from ..security import current_staff
from ..templating import templates

router = APIRouter()

_MAX_TORRE = 10


def _clasificar(valor: str) -> str:
    """'telefono' | 'whatsapp' | 'torre_apto' | 'ninguno' -- ver docstring
    del módulo. Única función que decide esto; `/announce/identificar`,
    `/announce` (POST, para el `contacto` de un residente nuevo) y esta
    misma la usan, para que nunca diverjan. Teléfono/WhatsApp delega en
    `clasificar_contacto` (dominio, `.scratch/ocupante-principal-
    escenarios` ticket 01) -- Torre+Apto es un caso propio de esta vista,
    no se generaliza.

    Exige el valor COMPLETO, no cualquier prefijo -- encontrado en
    code-review antes de desplegar: sin este mínimo, el primer dígito/letra
    tecleado ya clasificaba como candidato, disparando de inmediato la
    tarjeta "no encontramos a nadie" (con foco automático al campo Nombre)
    en CADA tecleo mientras el staff todavía estaba escribiendo -- le robaba
    el foco al campo principal a mitad de un número de teléfono real. Un
    celular colombiano son SIEMPRE 10 dígitos (`normalizar_telefono`), así
    que ese es el umbral exacto para Teléfono; WhatsApp no tiene largo fijo,
    así que se usa el mínimo de `WHATSAPP_USUARIO_RE` (3) -- reduce el
    problema bastante aunque no lo elimina del todo para nombres de usuario
    largos (por eso el Nombre del fragmento YA NO lleva `autofocus`, ver
    `_identificar.html`: ese es el fix que cierra el caso completo). Torre+
    Apto no necesita un umbral de longitud análogo: `_resolver_torre_apto`
    ya solo resuelve contra una unidad EXACTA del catálogo, y ningún código
    válido es prefijo de otro (verificado matemáticamente en el spec de
    `.scratch/announce-rapido` contra las 804 unidades reales)."""
    valor = (valor or "").strip()
    if not valor:
        return "ninguno"
    primero = valor[0]
    if primero in ("0", "1") and valor.isdigit():
        return "torre_apto"
    return clasificar_contacto(valor)


def _torre_desde_codigo(valor: str) -> str | None:
    """Primeros 2 dígitos del código Torre+Apto -> `"TORRE N"` (`01` ->
    `"TORRE 1"` .. `10` -> `"TORRE 10"`), o `None` si el código no alcanza
    para 2 dígitos todavía, o el número no es una torre real (1-10)."""
    if len(valor) < 2:
        return None
    codigo = valor[:2]
    if not codigo.isdigit():
        return None
    numero = int(codigo)
    if not (1 <= numero <= _MAX_TORRE):
        return None
    return f"TORRE {numero}"


def _torre_apto_completo(valor: str) -> bool:
    """¿`valor` ya tiene los 2 dígitos de una Torre VÁLIDA (1-10) más un
    número de apartamento con largo mínimo real (3 dígitos -- el catálogo
    sembrado en `0021_seed_catalogo_apartamentos` numera cada piso como
    `piso*100 + i`, así que ningún apartamento real tiene menos de 3
    dígitos, aunque el piso 13 de las torres grandes sube a 4)? -- umbral
    para decidir si un código Torre+Apto que no calzó ya es evaluable
    contra el catálogo (ticket 15, `.scratch/ocupante-principal-
    escenarios`) o si todavía está a medio teclear (estado normal, sin
    mensaje). Un código con torre inválida (ej. "11", torre 11 no existe)
    nunca cuenta como completo -- ningún dígito adicional lo va a arreglar,
    pero eso ya es indistinguible de "a medio teclear" con la info que hay,
    así que se trata igual (sin mensaje, mismo comportamiento de antes de
    este ticket)."""
    torre = _torre_desde_codigo(valor)
    if torre is None:
        return False
    return len(valor) - 2 >= 3


def _resolver_torre_apto(session: Session, valor: str):
    """Resuelve `valor` (código Torre+Apto tecleado, ej. `"01106"`) contra
    el catálogo cerrado -- `None` si el código todavía no calza EXACTO con
    ninguna unidad real (a medio teclear, o simplemente inválido). Nunca
    lanza -- un código incompleto es un estado normal mientras se escribe,
    no un error."""
    torre = _torre_desde_codigo(valor)
    if torre is None:
        return None
    apto_numero = valor[2:]
    if not apto_numero:
        return None
    try:
        return resolver_apartamento(session, torre, apto_numero)
    except ValueError:
        return None


def _unidad_con_coresidentes(session: Session, persona: Persona):
    """`(Apartamento, residentes)` si `persona` es Ocupante activo de una
    unidad con MÁS de un residente activo -- `None` si no es Ocupante de
    nada, o si vive sola (ahí el atajo directo de Teléfono/WhatsApp de
    siempre sigue aplicando -- ticket 02, `.scratch/announce-residente-
    correcto`)."""
    ocupante = ocupante_activo_de_persona(session, persona.id)
    if ocupante is None:
        return None
    apto = session.get(Apartamento, ocupante.apartamento_id)
    residentes = listar_ocupantes(session, apto)
    if len(residentes) <= 1:
        return None
    return apto, residentes


@router.get("/announce", response_class=HTMLResponse)
def announce_form(request: Request, staff: Usuario = Depends(current_staff)):
    return templates.TemplateResponse("announce_new/form.html", {"request": request, "staff": staff})


@router.get("/announce/identificar", response_class=HTMLResponse)
def announce_identificar(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
):
    tipo = _clasificar(q)

    if tipo in ("telefono", "whatsapp"):
        persona = (
            buscar_persona_por_telefono(db, q)
            if tipo == "telefono"
            else buscar_persona_por_whatsapp(db, q)
        )
        if persona is not None:
            unidad = _unidad_con_coresidentes(db, persona)
            if unidad is not None:
                apto, residentes = unidad
                return templates.TemplateResponse(
                    "announce_new/_identificar_unidad.html",
                    {
                        "request": request,
                        "apartamento": apto,
                        "residentes": residentes,
                        "anunciante_persona_id": persona.id,
                        "anunciante_telefono": persona.telefono if tipo == "telefono" else None,
                        "anunciante_whatsapp": persona.whatsapp_usuario if tipo == "whatsapp" else None,
                    },
                )
        return templates.TemplateResponse(
            "announce_new/_identificar.html",
            {"request": request, "tipo": tipo, "valor": q, "persona": persona},
        )

    if tipo == "torre_apto":
        apto = _resolver_torre_apto(db, q)
        if apto is None:
            if _torre_apto_completo(q):
                return templates.TemplateResponse(
                    "announce_new/_identificar_torre_apto_sin_match.html",
                    {"request": request},
                )
            return HTMLResponse("")
        residentes = listar_ocupantes(db, apto)
        return templates.TemplateResponse(
            "announce_new/_identificar_unidad.html",
            {"request": request, "apartamento": apto, "residentes": residentes},
        )

    return HTMLResponse("")  # "ninguno" -- nada que mostrar todavía.


def _resolver_ocupante(session: Session, ocupante_id: str) -> Ocupante | None:
    """`ocupante_id` (string, forma libre) -> `Ocupante`, o `None` si no es
    un UUID válido o no existe -- compartido por `GET /announce/identificar-
    ocupante` y el camino 2 de `POST /announce` (residente ya existente),
    para no repetir el parseo de UUID en los dos lugares."""
    try:
        oid = uuid.UUID(ocupante_id)
    except (ValueError, TypeError):
        return None
    return session.get(Ocupante, oid)


@router.get("/announce/identificar-ocupante", response_class=HTMLResponse)
def announce_identificar_ocupante(
    request: Request,
    ocupante_id: str = "",
    anunciante_telefono: str = None,
    anunciante_whatsapp: str = None,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
):
    """Clic/tap sobre un residente de la lista de `_identificar_unidad.html`
    (ticket 05) -- resuelve ese Ocupante puntual y devuelve la misma
    tarjeta Anunciar/Recibir que el camino de Teléfono/WhatsApp.

    `anunciante_telefono`/`anunciante_whatsapp` (ticket 02): presentes solo
    si se llegó desde el camino Teléfono/WhatsApp con co-residentes -- se
    propagan tal cual a la tarjeta para que el Anunciante del Paquete
    quede fijo en quien llamó (ver docstring del módulo)."""
    ocupante = _resolver_ocupante(db, ocupante_id)
    if ocupante is None:
        return HTMLResponse("")
    return templates.TemplateResponse(
        "announce_new/_identificar_ocupante.html",
        {
            "request": request,
            "ocupante": ocupante,
            "anunciante_telefono": anunciante_telefono,
            "anunciante_whatsapp": anunciante_whatsapp,
        },
    )


@router.post("/announce", response_class=HTMLResponse)
def announce_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    sender: NotificationSender = Depends(get_notification_sender),
    telefono: str = Form(None),
    whatsapp_usuario: str = Form(None),
    nombre: str = Form(None),
    ocupante_id: str = Form(None),
    torre: str = Form(None),
    apartamento: str = Form(None),
    contacto: str = Form(None),
    mover_de_otra_unidad: str = Form(None),
    accion: str = Form("anunciar"),
):
    def _error(mensaje: str, valor_q: str = None):
        # Repuebla el campo principal con lo ya identificado (issue
        # encontrado en code-review) -- sin esto, cualquier error de
        # validación borraba el Teléfono/WhatsApp que el staff ya había
        # resuelto, obligando a retipearlo desde cero. No repuebla el
        # fragmento de abajo (Anunciar/Recibir) -- toca al staff volver a
        # tocar el campo para que la búsqueda en vivo lo re-resuelva.
        contexto = {"request": request, "staff": staff, "error": mensaje}
        if valor_q:
            contexto["valor_q"] = valor_q
        return templates.TemplateResponse(
            "announce_new/form.html", contexto, status_code=400
        )

    def _anunciar_para(ocupante: Ocupante, llamante_telefono: str = None, llamante_whatsapp: str = None):
        """`(paquete, None)` si se pudo anunciar, o `(None, mensaje)` si
        `ocupante` no tiene ninguna identidad real (propia ni del
        Principal de su unidad) con la cual anunciar -- ver
        `anunciante_para_ocupante`. Comparte el mismo camino (ticket 05)
        tanto para un residente YA existente como para uno recién creado
        con "Nueva persona".

        `llamante_telefono`/`llamante_whatsapp` (ticket 02, `.scratch/
        announce-residente-correcto`): si vienen, YA se sabe quién anuncia
        (el camino Teléfono/WhatsApp con co-residentes) -- el Anunciante es
        esa Persona, sin pasar por `anunciante_para_ocupante` (que resuelve
        a partir del Destinatario, no de quien llamó). Ambos juntos nunca
        deberían llegar acá (el GET de `/announce/identificar` y el `elif`
        de `_identificar_unidad.html` ya son mutuamente excluyentes sobre
        cuál de los dos se propaga) -- por defensivo, si pasara, se
        prefiere Teléfono en silencio, a diferencia del camino 1 más abajo
        (que rechaza explícitamente "Identifica a la persona antes de
        anunciar." cuando el STAFF manda los dos desde el campo único).
        Son dos guards distintos a propósito: acá el valor viene de un
        hidden field que la propia app generó, no de lo que el staff
        tecleó -- no hace falta la misma UX de rechazo explícito para un
        estado que la UI nunca produce."""
        llamante_telefono = (llamante_telefono or "").strip()
        llamante_whatsapp = (llamante_whatsapp or "").strip()
        if llamante_telefono or llamante_whatsapp:
            paquete = announce(
                db,
                anunciante_telefono=llamante_telefono or None,
                anunciante_whatsapp=None if llamante_telefono else (llamante_whatsapp or None),
                destinatario=Destinatario.ocupante(ocupante.id),
                staff_actor=staff,
            )
            return paquete, None

        anunciante = anunciante_para_ocupante(db, ocupante)
        if anunciante is None:
            return None, (
                "Este residente no tiene Teléfono ni WhatsApp propio, y la "
                "unidad todavía no tiene un Principal confirmado -- no se "
                "puede anunciar todavía."
            )
        paquete = announce(
            db,
            anunciante_telefono=anunciante.telefono or None,
            anunciante_nombre=anunciante.nombre,
            destinatario=Destinatario.ocupante(ocupante.id),
            staff_actor=staff,
            anunciante_whatsapp=None if anunciante.telefono else anunciante.whatsapp_usuario,
        )
        return paquete, None

    if ocupante_id:
        # Camino 2 (ticket 05, + llamante conocido del ticket 02): residente
        # YA existente, elegido de la lista de una unidad.
        ocupante = _resolver_ocupante(db, ocupante_id)
        if ocupante is None:
            return _error("Ese residente ya no existe -- vuelve a buscar la unidad.")
        paquete, error = _anunciar_para(ocupante, telefono, whatsapp_usuario)
        if error:
            return _error(error)

    elif torre and apartamento:
        # Camino 3 (ticket 05, + llamante conocido del ticket 02): "Nueva
        # persona" dentro de una unidad -- registra el Ocupante
        # (agregar_ocupante tal cual, nace pending) Y anuncia en el mismo
        # paso.
        try:
            apto = resolver_apartamento(db, torre, apartamento)
        except ValueError as exc:
            return _error(str(exc))
        if not (nombre or "").strip():
            return _error("Escribe el nombre para registrar a este residente.")

        contacto_valor = (contacto or "").strip()
        tipo_contacto = _clasificar(contacto_valor) if contacto_valor else "ninguno"
        kwargs_contacto = {}
        if tipo_contacto == "telefono":
            kwargs_contacto["telefono"] = contacto_valor
        elif tipo_contacto == "whatsapp":
            kwargs_contacto["whatsapp_usuario"] = contacto_valor
        elif contacto_valor:
            # Se tecleó algo pero no clasifica como Teléfono (10 dígitos,
            # empieza en 3) ni WhatsApp (mínimo 3 caracteres) -- bug real
            # encontrado en code-review: antes esto se descartaba en
            # silencio y el Ocupante quedaba SIN el contacto que el staff sí
            # quiso darle, sin ningún aviso.
            return _error(
                "Ese contacto no parece un Teléfono ni un usuario de "
                "WhatsApp válido -- revísalo, o déjalo vacío."
            )

        # Mover (.scratch/ocupante-principal-escenarios, ticket 12): si el
        # contacto ya es Ocupante activo no-principal de OTRA unidad, mover
        # a esa persona (con su identidad real) en vez de crear un
        # registro nuevo -- el `nombre` recién tecleado se ignora en ese
        # caso. Nunca aplica a un principal, sin excepción.
        conflicto = ocupante_activo_por_contacto(db, **kwargs_contacto) if kwargs_contacto else None
        moviendo = conflicto is not None and conflicto.apartamento_id != apto.id
        if moviendo and (conflicto.es_principal or not mover_de_otra_unidad):
            return _error(mensaje_ya_ocupante_activo(db, conflicto))

        try:
            if moviendo:
                ocupante = mover_ocupante(db, conflicto, apto)
            else:
                ocupante = agregar_ocupante(db, apto, nombre, **kwargs_contacto)
        except ValueError as exc:
            return _error(str(exc))

        paquete, error = _anunciar_para(ocupante, telefono, whatsapp_usuario)
        if error:
            # Integridad transaccional (.scratch/ocupante-principal-
            # escenarios, ticket 09): agregar_ocupante/mover_ocupante YA
            # tuvo éxito (el Ocupante está flushed en esta sesión) -- sin
            # este rollback, `get_db` lo comitearía igual al cerrar el
            # request (commit al éxito / rollback SOLO si se lanza una
            # excepción), dejando un cambio aplicado aunque el anuncio en
            # sí haya fallado.
            db.rollback()
            return _error(error)

    else:
        # Caminos 1 (ticket 04): Teléfono/WhatsApp directo.
        tiene_telefono = bool((telefono or "").strip())
        tiene_whatsapp = bool((whatsapp_usuario or "").strip())
        valor_original = telefono if tiene_telefono else (whatsapp_usuario if tiene_whatsapp else None)

        if tiene_telefono and tiene_whatsapp:
            # No debería pasar viniendo del fragmento de /announce/identificar
            # (siempre fija exactamente uno) -- se corta acá ANTES del lookup
            # de "ya_registrada" de abajo, que necesita saber cuál de los dos
            # usar sin ambigüedad. El caso "ninguno de los dos" no necesita
            # este guard: cae directo al ValueError de `announce()` más
            # abajo, sin duplicar ese mismo chequeo acá.
            return _error("Identifica a la persona antes de anunciar.")

        if tiene_telefono or tiene_whatsapp:
            ya_registrada = (
                buscar_persona_por_telefono(db, telefono)
                if tiene_telefono
                else buscar_persona_por_whatsapp(db, whatsapp_usuario)
            )
            if ya_registrada is None and not (nombre or "").strip():
                return _error("Escribe el nombre para registrar a esta persona.", valor_original)

        try:
            paquete = announce(
                db,
                anunciante_telefono=telefono if tiene_telefono else None,
                anunciante_nombre=nombre,
                destinatario=Destinatario.yo_mismo(),
                staff_actor=staff,
                anunciante_whatsapp=whatsapp_usuario if tiene_whatsapp else None,
            )
        except ValueError as exc:
            return _error(str(exc), valor_original)

    resultado = preparar_notificacion(db, paquete, EstadoPaquete.ANUNCIADO)
    if resultado is not None:
        background_tasks.add_task(enviar_en_segundo_plano, sender, *resultado)

    contexto = {"request": request, "staff": staff, "paquete_creado": paquete}
    if accion == "recibir":
        # Ticket 06: mismo botón, distinto desenlace -- además del toast de
        # siempre, la respuesta trae el modal de recepción YA ABIERTO para
        # este Paquete (ver docstring del módulo). `tipos`/`condiciones`
        # son los mismos enums que `packages.py` ya le pasa a
        # `packages/_resultados.html` para ese mismo modal -- misma forma de
        # contexto, ahora con dos consumidores.
        contexto["mostrar_recibir"] = True
        contexto["tipos"] = list(TipoPaquete)
        contexto["condiciones"] = list(CondicionPaquete)
        # Paso nuevo de Recibir (.scratch/ocupante-principal-escenarios,
        # ticket 05) -- mismo contexto que ya le pasa packages.py a su
        # propio modal_recibir, para el mismo componente compartido.
        contexto["sin_apartamento"] = not paquete.snapshot_apartamento
        contexto["candidatos"] = candidatos_correccion(db, paquete)
        contexto["catalogo_torres"] = listar_catalogo_por_torre(db)
        # Aviso de "¿ya hay residentes acá?" al declarar unidad nueva desde
        # Recibir (issue 127, retroalimentación en vivo 2026-08-18) -- mismo
        # dato que ya usa "Asignar apartamento" en packages.py.
        contexto["residentes_por_unidad"] = residentes_por_torre_apartamento(db)

    return templates.TemplateResponse("announce_new/form.html", contexto)
