# -*- coding: utf-8 -*-
"""
Dependencias de autenticación de la capa web (rebuild PaqueteXv.2).

`current_staff` lee la sesión (cookie firmada), carga el `Usuario` y lo entrega:
es el **actor** de la máquina de estados y la **puerta** de las rutas con
privilegios. `require_admin` añade la exigencia de rol ADMIN. El id del usuario
sale SIEMPRE de la sesión verificada, nunca de un parámetro del cliente.
"""

import uuid
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, Signer
from sqlalchemy.orm import Session

from app.domain.configuracion_conjunto_service import SEGUNDOS_INACTIVIDAD_POR_DEFECTO, obtener_seguridad_sesion
from app.domain.operador_dispositivo_service import acepta_pin, ahora, registro_vigente, tiene_registros_vigentes
from app.domain.persona import Persona
from app.domain.usuario import RolUsuario, Usuario

from .config import secret_key
from .db import get_db

SESSION_KEY = "usuario_id"
# Clave INDEPENDIENTE de SESSION_KEY: staff y cliente son sesiones separadas que
# coexisten en el mismo navegador sin pisarse (CONTEXT.md: "Usuario = staff;
# Persona/Cliente = residente").
CUSTOMER_SESSION_KEY = "persona_id"
# Dato DERIVADO guardado en sesión al hacer login (ver DEC-09) para que el
# header pueda decidir si mostrar Administración sin una dependencia de BD
# nueva en cada ruta que renderiza una página completa. NUNCA es la fuente de
# autorización real -- `require_admin` (abajo) sigue siendo la única puerta.
ROLE_SESSION_KEY = "rol"
# Issue 383 (.scratch/pendientes-cliente): versión de sesión del Usuario con la que se abrió esta sesión (ver
# `Usuario.sesion_version`). Una cookie de antes de este cambio no la trae: cuenta como 0, la versión inicial.
SESION_VERSION_KEY = "sesion_version"
# Mismo espíritu que ROLE_SESSION_KEY: el nombre para pintar el avatar/trigger
# de cuenta del header (Grupo "header producción") sin una dependencia de BD
# nueva en base.html. Dato derivado para UI únicamente -- si el nombre real
# cambia, se refleja en el próximo login, igual que el rol. DOS claves
# separadas (no una compartida) porque cliente y staff son sesiones
# independientes que pueden coexistir -- una clave única haría que la
# segunda sesión en loguearse pisara el nombre de la primera.
NOMBRE_SESSION_KEY = "nombre"
CUSTOMER_NOMBRE_SESSION_KEY = "persona_nombre"


# Dispositivo registrado (`.scratch/pin-operador-dispositivo`): cookie PROPIA del equipo, firmada, independiente de la
# de sesión -- identifica el navegador aunque la sesión venza o se cierre, y es lo que hace que un PIN solo valga donde
# su dueño entró con contraseña. Su vida es larga a propósito: la vigencia real de cada registro la decide el servidor
# con los días configurados.
COOKIE_DISPOSITIVO = "paquetex_dispositivo"
_DURACION_COOKIE_DISPOSITIVO_SEGUNDOS = 365 * 24 * 60 * 60


def _firmador_dispositivo() -> Signer:
    return Signer(secret_key(), salt="paquetex-dispositivo")


def dispositivo_id_de(request: Request) -> uuid.UUID | None:
    """El id del equipo según su cookie firmada, o `None` si no hay cookie o la firma no cuadra."""
    crudo = request.cookies.get(COOKIE_DISPOSITIVO)
    if not crudo:
        return None
    try:
        return uuid.UUID(_firmador_dispositivo().unsign(crudo).decode("ascii"))
    except (BadSignature, ValueError, UnicodeDecodeError):
        return None


def fijar_cookie_dispositivo(request: Request, response, dispositivo_id) -> None:
    """`Secure` con el mismo criterio que la cookie de sesión, decidido al crear el app (`create_app`)."""
    response.set_cookie(
        COOKIE_DISPOSITIVO,
        _firmador_dispositivo().sign(str(dispositivo_id)).decode("ascii"),
        max_age=_DURACION_COOKIE_DISPOSITIVO_SEGUNDOS,
        httponly=True,
        samesite="lax",
        secure=getattr(request.app.state, "cookies_seguras", False),
    )


# Bloqueo por inactividad (`.scratch/pin-operador-dispositivo`, ticket 04). La sesión guarda la marca de la última
# actividad del Usuario; el navegador avisa de la actividad local (toques, teclas) como máximo una vez por minuto, así
# que el servidor espera ese minuto de más antes de bloquear: el Bloqueo en pantalla lo manda el navegador, el servidor
# es la red de seguridad para cuando el navegador falla o se manipula.
ULTIMA_ACTIVIDAD_KEY = "ultima_actividad"
OPERADOR_BLOQUEADO_KEY = "operador_bloqueado"
SEGUNDOS_INACTIVIDAD_KEY = "segundos_inactividad"
MARGEN_AVISO_SEGUNDOS = 60
# Peticiones que el navegador hace solo (cola de fotos, reintentos): no cuentan como actividad.
ENCABEZADO_AUTOMATICO = "x-paquetex-automatico"


class RedireccionStaff(Exception):
    """Una puerta de staff que no niega el acceso sino que lo desvía (ej. "Crea tu PIN"). El app la convierte en un
    303 hacia `destino`."""

    def __init__(self, destino: str):
        self.destino = destino


def abrir_sesion_staff(request: Request, usuario: Usuario) -> None:
    """Deja a `usuario` como Operador activo de la sesión (al entrar con contraseña o al desbloquear con PIN)."""
    request.session[SESSION_KEY] = str(usuario.id)
    request.session[SESION_VERSION_KEY] = usuario.sesion_version or 0  # issue 383
    # Dato derivado para el menú (DEC-09) -- require_admin sigue siendo la
    # única puerta real de las rutas de administración.
    request.session[ROLE_SESSION_KEY] = usuario.rol.value
    request.session[NOMBRE_SESSION_KEY] = usuario.nombre
    request.session[ULTIMA_ACTIVIDAD_KEY] = ahora().timestamp()
    request.session.pop(OPERADOR_BLOQUEADO_KEY, None)


def _quitar_operador(request: Request) -> None:
    """Quita al Operador activo, sin olvidar quién era si hubo un Bloqueo. `pop`, nunca `clear`: la sesión de cliente
    es independiente."""
    request.session.pop(SESSION_KEY, None)
    request.session.pop(ROLE_SESSION_KEY, None)
    request.session.pop(NOMBRE_SESSION_KEY, None)
    request.session.pop(ULTIMA_ACTIVIDAD_KEY, None)


def cerrar_sesion_staff(request: Request) -> None:
    """Cierra la sesión de staff del todo (salir, no bloquear)."""
    _quitar_operador(request)
    request.session.pop(OPERADOR_BLOQUEADO_KEY, None)


def cerrar_sesion_cliente(request: Request) -> None:
    """Cierra la sesión de cliente (residente), independiente de la de staff."""
    request.session.pop(CUSTOMER_SESSION_KEY, None)
    request.session.pop(CUSTOMER_NOMBRE_SESSION_KEY, None)


def bloquear_sesion_staff(request: Request) -> None:
    """Bloqueo (por inactividad o con "Bloquear"): quita al Operador activo, pero recuerda quién era, para que al
    desbloquear se sepa si es la misma persona (sigue donde iba) u otra (vista limpia). Se quita del todo, no se marca:
    las vistas que solo miran si hay sesión de staff (búsqueda, `/entrar`) no deben ver nada con el equipo bloqueado."""
    anterior = request.session.get(SESSION_KEY)
    _quitar_operador(request)
    if anterior:
        request.session[OPERADOR_BLOQUEADO_KEY] = anterior


def _vencio_inactividad(request: Request, segundos: int, momento: float) -> bool:
    ultima = request.session.get(ULTIMA_ACTIVIDAD_KEY) or momento
    return momento - ultima > segundos + MARGEN_AVISO_SEGUNDOS


async def bloquear_si_vencio(request: Request, call_next):
    """Middleware (dentro de la sesión): antes de cualquier ruta, si la inactividad ya venció, el equipo queda
    bloqueado. Así también lo respetan las vistas que solo miran si hay sesión de staff sin pasar por `current_staff`
    (búsqueda, `/entrar`). Sin consultar la BD: usa los segundos que `current_staff` dejó en la sesión en la última
    petición; `current_staff` vuelve a comprobar con la configuración vigente."""
    if request.session.get(SESSION_KEY):
        segundos = request.session.get(SEGUNDOS_INACTIVIDAD_KEY) or SEGUNDOS_INACTIVIDAD_POR_DEFECTO
        if _vencio_inactividad(request, segundos, ahora().timestamp()):
            bloquear_sesion_staff(request)
    return await call_next(request)


def _es_fetch(request: Request) -> bool:
    """¿La petición la hizo JavaScript (fetch) y no una navegación? El navegador lo declara en `Sec-Fetch-Mode`."""
    modo = request.headers.get("sec-fetch-mode")
    return bool(modo) and modo != "navigate"


def _sin_operador(request: Request, db: Session, detalle: str):
    """Sin Operador activo válido. Si alguien puede desbloquear este equipo con su PIN: a un `fetch`, 423 con
    `X-PaqueteX-Bloqueo` (el cliente muestra la capa de bloqueo); a una navegación, la pantalla de bloqueo (volviendo
    después a la vista pedida, si era un GET). Si nadie puede, 401 → `/ingresar`."""
    _quitar_operador(request)
    if acepta_pin(db, dispositivo_id_de(request)):
        if _es_fetch(request):
            raise HTTPException(423, detail="Equipo bloqueado", headers={"X-PaqueteX-Bloqueo": "1"})
        destino = "/bloqueo"
        if request.method == "GET":
            pedido = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            destino += "?siguiente=" + quote(pedido, safe="/")
        raise RedireccionStaff(destino)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detalle)


def staff_sin_pin(request: Request, db: Session = Depends(get_db)) -> Usuario:
    """El `Usuario` de la sesión actual, sin exigir que ya tenga PIN -- solo para la pantalla que lo crea.
    Sin sesión válida → pantalla de bloqueo si el equipo tiene registros vigentes, si no 401.

    Un 401 lo convierte el app en un redirect a `/ingresar` (ver app factory).
    """
    raw = request.session.get(SESSION_KEY)
    if not raw:
        _sin_operador(request, db, "No autenticado")
    try:
        usuario_id = uuid.UUID(str(raw))
    except (ValueError, TypeError):
        _sin_operador(request, db, "Sesión inválida")

    usuario = db.get(Usuario, usuario_id)
    if usuario is not None and request.session.get(SESION_VERSION_KEY, 0) != (usuario.sesion_version or 0):
        # Issue 383: la contraseña cambió después de abrir esta sesión -- otro equipo, o un restablecimiento.
        usuario = None
    if usuario is not None and not registro_vigente(db, dispositivo_id_de(request), usuario):
        # PIN de operador: la sesión solo vale en un equipo donde este Usuario tiene un registro vigente (vence por
        # días configurados, o por "Cerrar en todos los dispositivos").
        usuario = None
    if usuario is None or not usuario.activo:
        # `activo` se relee de la BD en CADA request (sin caché, mismo
        # criterio que ya aplicaba el rol -- ver ROLE_SESSION_KEY arriba):
        # un ADMIN que desactiva a alguien con sesión YA abierta corta su
        # acceso en el siguiente request, no recién en su próximo login
        # (hueco real encontrado en auditoría, .scratch/pendientes-cliente
        # -- antes `activo` solo se chequeaba en `staff_service.autenticar`).
        _sin_operador(request, db, "Sesión inválida")

    segundos = obtener_seguridad_sesion(db).segundos_inactividad
    momento = ahora().timestamp()
    if _vencio_inactividad(request, segundos, momento):
        bloquear_sesion_staff(request)
        _sin_operador(request, db, "Equipo bloqueado")
    if not request.headers.get(ENCABEZADO_AUTOMATICO):
        request.session[ULTIMA_ACTIVIDAD_KEY] = momento
    # Para el contador del navegador (`base.html`): los segundos vigentes, sin otra consulta al pintar la página.
    request.session[SEGUNDOS_INACTIVIDAD_KEY] = segundos
    return usuario


def current_staff(request: Request, usuario: Usuario = Depends(staff_sin_pin)) -> Usuario:
    """El `Usuario` de la sesión actual: el actor de las acciones y la puerta de las rutas con privilegios.
    Sin sesión válida → 401; sin PIN todavía, o con un cambio de PIN obligatorio pendiente (ticket 07) → a `/mi-pin`
    (nadie opera sin identidad rápida)."""
    if not usuario.pin_huella or usuario.debe_cambiar_pin:
        raise RedireccionStaff("/mi-pin")
    return usuario


def dispositivo_registrado(request: Request, db: Session = Depends(get_db)) -> None:
    """Puerta de la cola de fotos en segundo plano (`.scratch/pin-operador-dispositivo`, ticket 05): basta con que el
    equipo tenga un registro vigente de algún Usuario activo -- no mira el Bloqueo ni la inactividad, y no toca la marca
    de actividad. Es seguro porque una foto en cola solo se asocia a un Paquete ya recibido y no se atribuye a nadie.
    Sin registro → 401."""
    if not tiene_registros_vigentes(db, dispositivo_id_de(request)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Equipo sin registro vigente")


def require_admin(usuario: Usuario = Depends(current_staff)) -> Usuario:
    """Como `current_staff`, pero exige rol ADMIN. Si no → 403."""
    if usuario.rol != RolUsuario.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Requiere rol ADMIN")
    return usuario


def current_customer(request: Request, db: Session = Depends(get_db)) -> Persona:
    """La `Persona` de la sesión de CLIENTE actual (independiente de `current_staff`).

    Sin sesión válida → 401. El id sale SIEMPRE de la sesión verificada, nunca de
    un parámetro del cliente.
    """
    raw = request.session.get(CUSTOMER_SESSION_KEY)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="No autenticado")
    try:
        persona_id = uuid.UUID(str(raw))
    except (ValueError, TypeError):
        request.session.pop(CUSTOMER_SESSION_KEY, None)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida")

    persona = db.get(Persona, persona_id)
    if persona is None:
        request.session.pop(CUSTOMER_SESSION_KEY, None)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida")
    return persona


def gate_bloqueado(persona: Persona) -> RedirectResponse | None:
    """`None` si `persona` puede usar el portal del cliente con normalidad;
    si sigue bloqueada (`bloqueado_en` seteado -- incluso con desbloqueo
    autorizado, todavía no aceptó términos), un redirect a la pantalla de
    aceptar términos en vez de lo que sea que la ruta iba a hacer
    (.scratch/bloquear-clientes, ticket 04). Mismo patrón que
    `customer_verify._gate_no_verificado`: la ruta llama esto primero y
    retorna temprano si no es `None`.

    NUNCA se llama desde la propia pantalla de aceptar términos -- esa
    sigue accesible con `bloqueado_en` seteado, es la única forma de salir
    de ese estado."""
    if persona.bloqueado_en is not None:
        return RedirectResponse("/mis-datos/aceptar-terminos", status_code=status.HTTP_303_SEE_OTHER)
    return None
