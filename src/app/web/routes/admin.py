# -*- coding: utf-8 -*-
"""
Ruta `/administracion/personal` — alta + gestión de cuentas de staff.

Protegida por `require_admin`. El actor de cada acción sale SIEMPRE de la
sesión (`require_admin`), nunca de un campo del formulario. Grupo 18 (Ronda
2) agregó la gestión de cuentas existentes (editar, resetear contraseña,
activar/desactivar) sobre `staff_service`, ya probado a nivel de dominio —
esta rebanada es solo el cableado HTTP.
"""

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.domain import smtp_email_sender
from app.domain.configuracion_conjunto_service import (
    obtener_nombre_conjunto,
    renombrar_conjunto,
)
from app.domain.email_sender import EmailSender
from app.domain.notification_sender import NotificationSender
from app.domain.motivo_cancelacion_service import (
    crear_motivo,
    editar_motivo,
    eliminar_motivo,
    listar_motivos,
)
from app.domain.notificacion_service import (
    guardar_plantilla,
    mensaje_de_prueba,
    obtener_asunto_actual,
    obtener_texto_actual,
)
from app.domain.paquete import EstadoPaquete
from app.domain.plantilla_email_html import envolver_html
from app.domain.preferencia_notificacion import CanalNotificacion
from app.domain.staff_service import (
    create_staff,
    editar_staff,
    listar_staff,
    resetear_password,
    set_activo_staff,
)
from app.domain.telefono import normalizar_telefono
from app.domain.usuario import RolUsuario, Usuario

from ..config import public_base_url
from ..db import get_db
from ..notifications import get_notification_sender, sms_configurado
from ..password_reset import get_email_sender
from ..security import require_admin
from ..templating import templates

router = APIRouter()

# Los 4 eventos que notifican comparten exactamente el mismo shape de fila
# -- un solo mensaje por evento, CANCELADO incluido (pedido explícito del
# cliente en vivo, 2026-09-03, `.scratch/motivos-cancelacion-catalogo`: el
# motivo elegido al cancelar no selecciona una plantilla distinta, ya se
# resuelve dentro del texto vía `{motivo}`). El catálogo de motivos
# (`motivo_cancelacion_service`) alimenta solo el picker de `/paquetes` y
# la lista de gestión embebida en el modal de CANCELADO -- sin relación con
# cuántas filas de plantilla existen.
_EVENTOS_QUE_NOTIFICAN = (
    EstadoPaquete.ANUNCIADO,
    EstadoPaquete.RECIBIDO,
    EstadoPaquete.ENTREGADO,
    EstadoPaquete.CANCELADO,
)


def _get_usuario_o_404(db: Session, usuario_id: str) -> Usuario:
    try:
        uid = uuid.UUID(usuario_id)
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cuenta no encontrada")
    usuario = db.get(Usuario, uid)
    if usuario is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cuenta no encontrada")
    return usuario


def _uuid_motivo_o_404(motivo_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(motivo_id)
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Motivo no encontrado")


@router.get("/administracion/personal", response_class=HTMLResponse)
def admin_staff_form(
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    creado: str = None,
):
    """`creado` (Post/Redirect/Get -- mismo patrón que `/anunciar`/`/announce`,
    aplicado acá por consistencia aunque el email único de por sí ya evita
    un duplicado silencioso en un reload): el id del Usuario recién dado de
    alta, para el toast de éxito -- `admin_staff_submit` ahora redirige acá
    en vez de renderizar directo."""
    contexto = {
        "request": request,
        "admin": admin,
        "roles": list(RolUsuario),
        "staff_list": listar_staff(db),
    }
    if creado:
        contexto["creado"] = db.get(Usuario, creado)
    return templates.TemplateResponse("admin/staff.html", contexto)


@router.post("/administracion/personal", response_class=HTMLResponse)
def admin_staff_submit(
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    email: str = Form(None),
    nombre: str = Form(None),
    password: str = Form(None),
    rol: str = Form(None),
):
    def _error(mensaje: str, campos: list[str] = None):
        return templates.TemplateResponse(
            "admin/staff.html",
            {
                "request": request,
                "admin": admin,
                "roles": list(RolUsuario),
                "staff_list": listar_staff(db),
                "error": mensaje,
                "email": email or "",
                "nombre": nombre or "",
                "error_email": mensaje if "email" in (campos or []) else None,
                "error_nombre": mensaje if "nombre" in (campos or []) else None,
                "error_password": mensaje if "password" in (campos or []) else None,
            },
            status_code=400,
        )

    if not (email or "").strip() or not (nombre or "").strip() or not (password or ""):
        campos_vacios = [
            c for c, v in [("email", email), ("nombre", nombre), ("password", password)]
            if not (v or "").strip()
        ]
        return _error("Email, nombre y contraseña son obligatorios.", campos=campos_vacios)

    try:
        rol_enum = RolUsuario(rol)
    except ValueError:
        # Sin campo que marcar: `rol` es un grupo de chips (radio), no un
        # `input_texto` -- ese macro no tiene estado de error propio, y
        # agregarlo solo para este caso (prácticamente inalcanzable sin
        # manipular el HTML a mano) no vale la pena. Se queda en el toast.
        return _error("Selecciona un rol válido.")

    try:
        creado = create_staff(db, admin, email, nombre, password, rol_enum)
    except (PermissionError, ValueError) as exc:
        mensaje = str(exc)
        # Clasificación por prefijo del mensaje (mismo criterio que
        # password_reset.py): create_staff/staff_service solo produce estos
        # 3 prefijos posibles.
        if mensaje.startswith("El email") or mensaje.startswith("Ya existe un usuario"):
            campo = "email"
        elif mensaje.startswith("La contraseña"):
            campo = "password"
        elif mensaje.startswith("El nombre"):
            campo = "nombre"
        else:
            campo = None
        return _error(mensaje, campos=[campo] if campo else [])

    # Post/Redirect/Get: antes esta respuesta renderizaba `admin/staff.html`
    # directo -- un reload reenviaba el POST (aunque el email único ya lo
    # bloqueaba con un error confuso "ya existe" en vez de dar de alta un
    # duplicado real). Redirige a `GET /administracion/personal` (arriba),
    # que reconstruye el mismo toast a partir del id.
    return RedirectResponse(f"/administracion/personal?creado={creado.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/administracion/personal/{usuario_id}/editar", response_class=HTMLResponse)
def admin_staff_editar(
    usuario_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    nombre: str = Form(None),
    rol: str = Form(None),
):
    usuario = _get_usuario_o_404(db, usuario_id)

    def _error(mensaje: str):
        return templates.TemplateResponse(
            "admin/staff.html",
            {
                "request": request,
                "admin": admin,
                "roles": list(RolUsuario),
                "staff_list": listar_staff(db),
                "error": mensaje,
            },
            status_code=400,
        )

    try:
        rol_enum = RolUsuario(rol)
    except ValueError:
        return _error("Selecciona un rol válido.")

    try:
        editar_staff(db, admin, usuario, nombre=nombre, rol=rol_enum)
    except (PermissionError, ValueError) as exc:
        return _error(str(exc))

    return RedirectResponse("/administracion/personal", status_code=status.HTTP_303_SEE_OTHER)


@router.post(
    "/administracion/personal/{usuario_id}/resetear-password", response_class=HTMLResponse
)
def admin_staff_resetear_password(
    usuario_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    password: str = Form(None),
):
    usuario = _get_usuario_o_404(db, usuario_id)
    try:
        resetear_password(db, admin, usuario, password)
    except (PermissionError, ValueError) as exc:
        return templates.TemplateResponse(
            "admin/staff.html",
            {
                "request": request,
                "admin": admin,
                "roles": list(RolUsuario),
                "staff_list": listar_staff(db),
                "error": str(exc),
            },
            status_code=400,
        )
    return RedirectResponse("/administracion/personal", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/administracion/personal/{usuario_id}/activar", response_class=HTMLResponse)
def admin_staff_activar(
    usuario_id: str,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
):
    usuario = _get_usuario_o_404(db, usuario_id)
    set_activo_staff(db, admin, usuario, True)
    return RedirectResponse("/administracion/personal", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/administracion/personal/{usuario_id}/desactivar", response_class=HTMLResponse)
def admin_staff_desactivar(
    usuario_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
):
    usuario = _get_usuario_o_404(db, usuario_id)
    try:
        set_activo_staff(db, admin, usuario, False)
    except ValueError as exc:
        return templates.TemplateResponse(
            "admin/staff.html",
            {
                "request": request,
                "admin": admin,
                "roles": list(RolUsuario),
                "staff_list": listar_staff(db),
                "error": str(exc),
            },
            status_code=400,
        )
    return RedirectResponse("/administracion/personal", status_code=status.HTTP_303_SEE_OTHER)


_CANALES_PLANTILLA = (CanalNotificacion.SMS, CanalNotificacion.EMAIL, CanalNotificacion.WHATSAPP)


def _canal_configurado(canal: CanalNotificacion) -> bool:
    """¿Tiene `canal` al menos un proveedor de envío REAL configurado en el
    sistema? (.scratch/notificaciones-enviar-prueba, ticket 02) -- gobierna
    si el botón "Enviar prueba" de esa pestaña aparece habilitado o
    deshabilitado-con-nota. SMS/Email reusan EXACTAMENTE el mismo booleano
    que ya decide el sender real (`sms_configurado`, fuente única
    compartida con `web/notifications.py::_sender_base`; Email vía
    `smtp_email_sender.configurado()`, igual que `web/password_reset.py::
    _sender_base`) -- un proveedor a medias no debe contar como
    "configurado" acá tampoco. WhatsApp siempre `False` hoy: no existe
    ningún proveedor de envío para ese canal todavía (ticket 03 agrega la
    pestaña deshabilitada correspondiente)."""
    if canal is CanalNotificacion.SMS:
        return sms_configurado()
    if canal is CanalNotificacion.EMAIL:
        return smtp_email_sender.configurado()
    return False


def _canales_de(db: Session, evento: EstadoPaquete, motivo: str):
    """Los 3 canales de `(evento, motivo)`, cada uno con su texto vigente
    (personalizado o default), solo Email su asunto vigente
    (`.scratch/plantillas-notificacion-multicanal`, ticket 02), y si tiene
    un proveedor de envío real configurado (`_canal_configurado`, ticket 02
    de `.scratch/notificaciones-enviar-prueba`)."""
    canales = []
    for canal in _CANALES_PLANTILLA:
        es_email = canal is CanalNotificacion.EMAIL
        texto = obtener_texto_actual(db, evento, motivo, canal)
        asunto = obtener_asunto_actual(db, evento, motivo) if es_email else None
        canales.append(
            {
                "canal": canal,
                "texto": texto,
                "asunto": asunto,
                "configurado": _canal_configurado(canal),
            }
        )
    return canales


def _filas_plantillas(db: Session):
    """Una fila por cada evento que notifica (ANUNCIADO/RECIBIDO/ENTREGADO/
    CANCELADO -- ANUNCIADO dejó de distinguir Cliente/Staff en issue 202,
    `.scratch/pendientes-cliente`), cada una con sus 3 canales
    (`_canales_de`). Un solo mensaje por evento -- CANCELADO ya no se
    desglosa por motivo (`.scratch/motivos-cancelacion-catalogo`, pedido
    explícito del cliente en vivo 2026-09-03)."""
    return [
        {
            "evento": e,
            "motivo": None,
            "canales": _canales_de(db, e, None),
        }
        for e in _EVENTOS_QUE_NOTIFICAN
    ]


@router.get("/administracion/notificaciones", response_class=HTMLResponse)
def admin_notificaciones_form(
    request: Request, db: Session = Depends(get_db), admin: Usuario = Depends(require_admin)
):
    return templates.TemplateResponse(
        "admin/notificaciones.html",
        {
            "request": request,
            "admin": admin,
            "filas": _filas_plantillas(db),
            "motivos": listar_motivos(db),
        },
    )


@router.post("/administracion/notificaciones", response_class=HTMLResponse)
def admin_notificaciones_guardar(
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    evento: str = Form(None),
    motivo: str = Form(None),
    canal: str = Form(CanalNotificacion.SMS.value),
    texto: str = Form(None),
    asunto: str = Form(None),
):
    def _error(mensaje: str, marcar_fila: bool = False):
        return templates.TemplateResponse(
            "admin/notificaciones.html",
            {
                "request": request,
                "admin": admin,
                "filas": _filas_plantillas(db),
                "motivos": listar_motivos(db),
                "error": mensaje,
                # Identifica CUÁL de las N filas × 3 canales (cada uno su
                # propio <form>) falló, para marcar solo esa pestaña/textarea
                # -- retroalimentación en vivo 2026-08-02, extendida a canal
                # en el ticket 02 de plantillas-notificacion-multicanal.
                "error_evento": evento if marcar_fila else None,
                "error_motivo": (motivo or None) if marcar_fila else None,
                "error_canal": canal if marcar_fila else None,
            },
            status_code=400,
        )

    try:
        evento_enum = EstadoPaquete(evento)
    except ValueError:
        # Sin fila que marcar: `evento` viene de un input hidden -- si esto
        # falla es manipulación directa del HTML, no un error de usuario
        # real: el toast alcanza.
        return _error("Evento inválido.")

    try:
        canal_enum = CanalNotificacion(canal)
    except ValueError:
        # Mismo criterio que `evento` -- input hidden, manipulación directa.
        return _error("Canal inválido.")

    if not (texto or "").strip():
        return _error("El texto no puede quedar vacío.", marcar_fila=True)

    if canal_enum is CanalNotificacion.EMAIL and not (asunto or "").strip():
        # Mismo criterio que `texto`: un asunto en blanco borraría en
        # silencio uno ya personalizado (`guardar_plantilla` sobreescribe
        # sin preguntar) -- se rechaza en vez de guardar `NULL` sin avisar.
        return _error("El asunto no puede quedar vacío.", marcar_fila=True)

    guardar_plantilla(
        db,
        evento_enum,
        motivo or None,
        texto,
        canal=canal_enum,
        # El asunto solo tiene sentido para Email -- ignorar lo que venga en
        # el form para otro canal en vez de confiar en que el cliente HTTP
        # no lo mande (mismo criterio que el resto de la validación de esta
        # ruta: el servidor no confía en la forma del POST).
        asunto=(asunto or None) if canal_enum is CanalNotificacion.EMAIL else None,
        # El actor SIEMPRE sale de la sesión (`require_admin`), nunca de un
        # campo del formulario -- mismo principio que el resto de esta ruta
        # (ver docstring del módulo).
        usuario_id=admin.id,
    )

    return templates.TemplateResponse(
        "admin/notificaciones.html",
        {
            "request": request,
            "admin": admin,
            "filas": _filas_plantillas(db),
            "motivos": listar_motivos(db),
            "guardado": True,
            "guardado_evento": evento,
            "guardado_motivo": motivo or None,
            "guardado_canal": canal,
        },
    )


@router.post("/administracion/notificaciones/probar", response_class=HTMLResponse)
def admin_notificaciones_probar(
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    notification_sender: NotificationSender = Depends(get_notification_sender),
    email_sender: EmailSender = Depends(get_email_sender),
    evento: str = Form(None),
    motivo: str = Form(None),
    canal: str = Form(None),
    destino: str = Form(None),
):
    """Envío de prueba REAL (.scratch/notificaciones-enviar-prueba, ticket
    02) — endpoint SEPARADO de `admin_notificaciones_guardar`: los dos
    validan campos requeridos distintos (`texto`/`asunto` vs. `destino`) y
    mezclarlos en un solo handler con un `accion` de por medio complicaría
    ambas validaciones sin necesidad.

    A propósito SÍNCRONO y sin `try/except Exception: pass` alrededor del
    envío (a diferencia de `notificar_evento`, best-effort porque la
    transición del Paquete ya se completó): acá el ÚNICO propósito de la
    ruta es que el ADMIN sepa si el mensaje salió o no, así que una falla
    real del proveedor se propaga a un toast de error en vez de tragarse en
    silencio."""

    def _error(mensaje: str, marcar_fila: bool = False):
        return templates.TemplateResponse(
            "admin/notificaciones.html",
            {
                "request": request,
                "admin": admin,
                "filas": _filas_plantillas(db),
                "motivos": listar_motivos(db),
                "error": mensaje,
                "prueba_error_evento": evento if marcar_fila else None,
                "prueba_error_motivo": (motivo or None) if marcar_fila else None,
                "prueba_error_canal": canal if marcar_fila else None,
                "prueba_error_destino": destino if marcar_fila else None,
            },
            status_code=400,
        )

    try:
        evento_enum = EstadoPaquete(evento)
    except ValueError:
        # Sin fila que marcar: `evento` viene de un input hidden -- si esto
        # falla es manipulación directa del HTML, no un error de usuario real.
        return _error("Evento inválido.")

    try:
        canal_enum = CanalNotificacion(canal)
    except ValueError:
        return _error("Canal inválido.")

    if not (destino or "").strip():
        return _error("El destino no puede quedar vacío.", marcar_fila=True)

    if not _canal_configurado(canal_enum):
        # Cubre WhatsApp hoy (siempre `False`, ticket 03 agrega su propio
        # botón deshabilitado) Y, en general, cualquier canal manipulado a
        # mano en un entorno sin proveedor -- el servidor no confía en que
        # el botón esté deshabilitado en el HTML.
        return _error(f"{canal_enum.value} no está configurado todavía.", marcar_fila=True)

    # `motivo` ya no selecciona una plantilla (CANCELADO es un solo mensaje,
    # `.scratch/motivos-cancelacion-catalogo`) -- solo aporta el valor de
    # ejemplo que reemplaza `{motivo}` en la vista previa. Se usa una
    # etiqueta real del catálogo (la primera, orden de creación) en vez de
    # inventar un texto, para que la prueba se vea como una notificación
    # real se vería.
    motivo_ejemplo = None
    if evento_enum is EstadoPaquete.CANCELADO:
        motivos_catalogo = listar_motivos(db)
        motivo_ejemplo = motivos_catalogo[0].etiqueta if motivos_catalogo else None
    texto, asunto = mensaje_de_prueba(db, evento_enum, motivo_ejemplo, canal_enum, public_base_url())
    destino_limpio = destino.strip()

    try:
        if canal_enum is CanalNotificacion.EMAIL:
            cuerpo_html = envolver_html(asunto, texto, public_base_url())
            email_sender.enviar(destino_limpio, asunto, texto, cuerpo_html)
        else:
            # SMS/WhatsApp: AWS SNS acepta un `PhoneNumber` sin el prefijo de
            # país (p.ej. "3002596319") y devuelve 200 + MessageId igual --
            # el mensaje se pierde en la nada, sin ninguna excepción que
            # `_error()` pueda mostrar. `normalizar_telefono()` (la MISMA
            # normalización que ya usa el flujo de OTP en `customer_auth.py`)
            # lo deja en E.164 antes de llegar a cualquier proveedor
            # (diagnóstico en vivo 2026-09-01: "Enviar prueba" mostraba éxito
            # sin que el SMS llegara nunca).
            try:
                destino_limpio = normalizar_telefono(destino_limpio)
            except ValueError:
                return _error("Teléfono inválido.", marcar_fila=True)
            notification_sender.enviar(destino_limpio, texto)
    except Exception as exc:
        return _error(f"No se pudo enviar la prueba: {exc}", marcar_fila=True)

    return templates.TemplateResponse(
        "admin/notificaciones.html",
        {
            "request": request,
            "admin": admin,
            "filas": _filas_plantillas(db),
            "motivos": listar_motivos(db),
            "prueba_ok": True,
            "prueba_destino": destino_limpio,
            "prueba_ok_evento": evento,
            "prueba_ok_motivo": motivo or None,
            "prueba_ok_canal": canal,
        },
    )


@router.post("/administracion/notificaciones/motivos", response_class=HTMLResponse)
def admin_motivos_crear(
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    etiqueta: str = Form(None),
):
    try:
        motivo = crear_motivo(db, etiqueta)
    except ValueError as exc:
        return templates.TemplateResponse(
            "admin/notificaciones.html",
            {
                "request": request,
                "admin": admin,
                "filas": _filas_plantillas(db),
                "motivos": listar_motivos(db),
                "error": str(exc),
                # Reabre el modal "Agregar motivo" con lo ya tecleado --
                # mismo criterio que `email`/`nombre` en `admin_staff_submit`.
                "motivo_crear_error": True,
                "motivo_crear_valor": etiqueta or "",
                # Y también el modal de CANCELADO (contiene la lista de
                # motivos + este disparador) -- ver `motivo_accion_cancelado`
                # en la plantilla.
                "motivo_accion_cancelado": True,
            },
            status_code=400,
        )

    return templates.TemplateResponse(
        "admin/notificaciones.html",
        {
            "request": request,
            "admin": admin,
            "filas": _filas_plantillas(db),
            "motivos": listar_motivos(db),
            "motivo_creado": motivo.etiqueta,
            "motivo_accion_cancelado": True,
        },
    )


@router.post(
    "/administracion/notificaciones/motivos/{motivo_id}/editar", response_class=HTMLResponse
)
def admin_motivos_editar(
    motivo_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    etiqueta: str = Form(None),
):
    mid = _uuid_motivo_o_404(motivo_id)
    try:
        motivo = editar_motivo(db, mid, etiqueta)
    except ValueError as exc:
        return templates.TemplateResponse(
            "admin/notificaciones.html",
            {
                "request": request,
                "admin": admin,
                "filas": _filas_plantillas(db),
                "motivos": listar_motivos(db),
                "error": str(exc),
                # Identifica CUÁL motivo falló, para reabrir su propio modal
                # "Editar motivo" (uno por fila del catálogo, ver plantilla).
                "motivo_editar_error_id": motivo_id,
                "motivo_accion_cancelado": True,
            },
            status_code=400,
        )

    return templates.TemplateResponse(
        "admin/notificaciones.html",
        {
            "request": request,
            "admin": admin,
            "filas": _filas_plantillas(db),
            "motivos": listar_motivos(db),
            "motivo_editado": motivo.etiqueta,
            "motivo_accion_cancelado": True,
        },
    )


@router.post(
    "/administracion/notificaciones/motivos/{motivo_id}/eliminar", response_class=HTMLResponse
)
def admin_motivos_eliminar(
    motivo_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
):
    mid = _uuid_motivo_o_404(motivo_id)
    try:
        eliminar_motivo(db, mid)
    except ValueError as exc:
        # El botón "Borrar este motivo" vive dentro de la lista de motivos
        # embebida en el modal de CANCELADO -- reabrirlo deja al admin
        # exactamente donde estaba, con el error visible arriba. El
        # sub-modal de confirmación en sí (`modal_confirmacion`, a
        # diferencia de `modal`) no soporta `abierto` y no se reabre solo --
        # mismo límite ya aceptado en el resto de la app (ej. "Cancelar
        # paquete" en `packages.py`); el modal de CANCELADO + el toast de
        # arriba alcanzan para explicar qué pasó.
        return templates.TemplateResponse(
            "admin/notificaciones.html",
            {
                "request": request,
                "admin": admin,
                "filas": _filas_plantillas(db),
                "motivos": listar_motivos(db),
                "error": str(exc),
                "motivo_accion_cancelado": True,
            },
            status_code=400,
        )

    return templates.TemplateResponse(
        "admin/notificaciones.html",
        {
            "request": request,
            "admin": admin,
            "filas": _filas_plantillas(db),
            "motivos": listar_motivos(db),
            "motivo_eliminado": True,
            "motivo_accion_cancelado": True,
        },
    )


@router.get("/administracion/conjunto", response_class=HTMLResponse)
def admin_conjunto_form(
    request: Request, db: Session = Depends(get_db), admin: Usuario = Depends(require_admin)
):
    return templates.TemplateResponse(
        "admin/conjunto.html",
        {"request": request, "admin": admin, "nombre": obtener_nombre_conjunto(db)},
    )


@router.post("/administracion/conjunto", response_class=HTMLResponse)
def admin_conjunto_guardar(
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    nombre: str = Form(""),
):
    try:
        nombre_guardado = renombrar_conjunto(db, nombre, admin)
    except ValueError as exc:
        return templates.TemplateResponse(
            "admin/conjunto.html",
            {
                "request": request,
                "admin": admin,
                "nombre": obtener_nombre_conjunto(db),
                "error": str(exc),
            },
            status_code=400,
        )

    return templates.TemplateResponse(
        "admin/conjunto.html",
        {
            "request": request,
            "admin": admin,
            "nombre": nombre_guardado,
            "guardado": True,
        },
    )
