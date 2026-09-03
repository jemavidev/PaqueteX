# -*- coding: utf-8 -*-
"""
Catálogo de proveedores de notificación — única fuente de verdad de qué
proveedores existen por canal y qué variables de entorno necesita cada uno
(`.scratch/administracion-proveedores/spec.md`, issue 01).

Vive en código, no en base de datos (decisión explícita del grilling: un
proveedor nuevo SIEMPRE necesita código real para su lógica de envío, así que
mover solo la lista de campos a la BD no elimina esa necesidad — ver spec,
sección "Registro de proveedores en código"). La tabla `ProveedorConfig`
(`proveedor_config.py`) solo guarda habilitado/orden; los VALORES de estos
campos siguen viviendo en `.env` del servidor (Fase 2, issue 04).

Agregar un proveedor nuevo: escribir su `Sender` real + una entrada aquí. Ni
la tabla de BD ni la pantalla de `/administracion/proveedores` necesitan
cambiar — la pantalla se genera a partir de este catálogo.

Cuatro canales (issue 289, pedido explícito del cliente): SMS y EMAIL tienen
`Sender` real. WHATSAPP (`META`) y LLAMADA (`PXB`) NO -- están en el
catálogo solo para dejar planteados sus campos de configuración, sin que
exista código de envío detrás todavía (ver docstring de
`ProveedorInfo.disponible` para la distinción entre los dos: `META` es
editable ya; `PXB` aparece pero bloqueado -- issues 289-291, iterado varias
veces en vivo con el cliente).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CampoProveedor:
    """Un campo de configuración de un proveedor -- una variable de entorno
    en `.env` del servidor.

    `oculto=True` (issue 293, pedido explícito del cliente) marca un campo
    que sigue en el allowlist de `variables_permitidas()` (el mecanismo SSH
    lo puede aplicar) pero que la pantalla NUNCA renderiza como input
    editable -- hoy solo `AWS_SNS_SMS_ENABLED`, cuyo valor
    `admin_proveedores.py` mantiene sincronizado automáticamente con el
    toggle `habilitado` (ver `ProveedorInfo.sincroniza_habilitado_con`), en
    vez de pedírselo al admin como un segundo control redundante."""

    variable_env: str
    etiqueta: str
    secreto: bool = True
    tipo: str = "texto"  # "texto" | "booleano" -- gobierna el input que arme la Fase 2
    oculto: bool = False


@dataclass(frozen=True)
class ProveedorInfo:
    """Un proveedor disponible para un canal. `clave` es el identificador
    estable que se guarda en `ProveedorConfig.proveedor` -- nunca cambia
    aunque `etiqueta` sea puramente cosmética.

    `disponible=False` (issues 289-291, pedido explícito del cliente,
    iterado varias veces en vivo hasta esta versión final) marca un
    proveedor sin `Sender` real todavía y sin fecha de tenerlo -- el
    catálogo deja sus campos PLANTEADOS EN CÓDIGO (para no perder el diseño
    cuando llegue el momento de construirlo), y la pantalla SIGUE
    mostrando su tab/toggle/campos -- pero todo `disabled` en HTML, con un
    badge "Próximamente" (`admin_proveedores.py` + `templates/admin/
    proveedores.html`). Se probaron dos alternativas antes de converger
    acá -- ocultar solo los campos (estado vacío) y ocultar la tab entera --
    el cliente pidió volver a mostrar la forma completa, solo que bloqueada:
    "la idea es que la seccion este presente, pero desactivada". La ruta
    POST igual rechaza cualquier cambio a un proveedor no disponible aunque
    alguien arme la petición a mano -- defensa en profundidad, el `disabled`
    de HTML ya lo impide del lado navegador. Ver `PXB` (Llamadas) más abajo.
    Un proveedor `disponible=True` sin `Sender` real (`META`) es distinto:
    SÍ es editable/guardable ya -- deja el terreno listo (`.env` +
    auditoría) para cuando el módulo de WhatsApp se construya, sin
    bloquear el formulario mientras tanto.

    `sincroniza_habilitado_con` (issue 293, pedido explícito del cliente:
    "el toggle debe hacer las 2 cosas") nombra una variable de entorno
    booleana (ej. `AWS_SNS_SMS_ENABLED`) que `admin_proveedores_guardar`
    mantiene en sincronía con el toggle `habilitado` de este proveedor --
    "true"/"false" en `.env` cada vez que el toggle CAMBIA de valor (nunca
    en cada guardado, para no reiniciar el servidor sin necesidad). El
    campo correspondiente en `campos` debe llevar `oculto=True` -- si no
    está oculto, el admin vería dos controles para lo mismo."""

    clave: str
    etiqueta: str
    campos: tuple[CampoProveedor, ...]
    disponible: bool = True
    sincroniza_habilitado_con: str | None = None


# Canal -> proveedores disponibles, en el orden histórico de precedencia
# (AWS SNS -> LIWA -> Twilio para SMS, pedido explícito del cliente
# 2026-08-06 -- ver `.scratch/sms-failover-twilio-sns/spec.md`). Este orden
# es solo el default de la migración de siembra (issue 01); el orden REAL en
# tiempo de ejecución vive en `ProveedorConfig.orden` desde el issue 02.
CATALOGO: dict[str, tuple[ProveedorInfo, ...]] = {
    "SMS": (
        ProveedorInfo(
            clave="AWS_SNS",
            etiqueta="AWS SNS",
            sincroniza_habilitado_con="AWS_SNS_SMS_ENABLED",
            campos=(
                # Issue 293 (corrección en vivo, pedido explícito del
                # cliente): mostrar esto como un segundo campo editable
                # aparte del toggle `habilitado` de arriba confundía --
                # "para este caso especifico el toggle debe hacer las 2
                # cosas". `oculto=True`: sigue en el allowlist SSH (sigue
                # siendo una variable real de `.env` que `sns_habilitado()`
                # lee), pero `admin_proveedores.py` la sincroniza sola con
                # el toggle en vez de pedírsela al admin -- ver
                # `ProveedorInfo.sincroniza_habilitado_con`.
                CampoProveedor(
                    "AWS_SNS_SMS_ENABLED", "Bandera AWS_SNS_SMS_ENABLED",
                    secreto=False, tipo="booleano", oculto=True,
                ),
                # `secreto=False` (issue 291, pedido explícito del cliente:
                # "el access key id is ok") -- identificador, no un secreto
                # real (mismo criterio que ya usa la propia consola de AWS:
                # el Access Key ID se muestra completo, solo el Secret
                # Access Key se enmascara).
                CampoProveedor("AWS_ACCESS_KEY_ID", "Access Key ID", secreto=False),
                CampoProveedor("AWS_SECRET_ACCESS_KEY", "Secret Access Key"),
                CampoProveedor("AWS_REGION", "Región", secreto=False),
            ),
        ),
        ProveedorInfo(
            clave="LIWA",
            etiqueta="LIWA",
            campos=(
                CampoProveedor("LIWA_API_KEY", "API Key"),
                CampoProveedor("LIWA_ACCOUNT", "Cuenta"),
                CampoProveedor("LIWA_PASSWORD", "Contraseña"),
                CampoProveedor("LIWA_AUTH_URL", "URL de autenticación", secreto=False),
            ),
        ),
        ProveedorInfo(
            clave="TWILIO",
            etiqueta="Twilio",
            campos=(
                CampoProveedor("TWILIO_ACCOUNT_SID", "Account SID"),
                CampoProveedor("TWILIO_AUTH_TOKEN", "Auth Token"),
                CampoProveedor("TWILIO_MESSAGING_SERVICE_SID", "Messaging Service SID"),
            ),
        ),
    ),
    "EMAIL": (
        ProveedorInfo(
            clave="SMTP",
            etiqueta="SMTP",
            campos=(
                CampoProveedor("SMTP_HOST", "Host", secreto=False),
                CampoProveedor("SMTP_PORT", "Puerto", secreto=False),
                CampoProveedor("SMTP_USER", "Usuario"),
                CampoProveedor("SMTP_PASSWORD", "Contraseña"),
                CampoProveedor("SMTP_FROM_EMAIL", "Email remitente", secreto=False),
                CampoProveedor("SMTP_USE_TLS", "Usar TLS", secreto=False, tipo="booleano"),
                CampoProveedor("SMTP_USE_SSL", "Usar SSL", secreto=False, tipo="booleano"),
            ),
        ),
    ),
    # WhatsApp (issue 289): sin `Sender` real, no se va a construir por
    # ahora -- las notificaciones ya tienen plantillas para este canal
    # (`plantilla_notificacion.py`), pero nadie las envía todavía. El
    # catálogo deja el terreno de configuración listo (`.env` + auditoría)
    # para cuando el módulo se construya; mientras tanto es editable sin
    # efecto funcional. Campos genéricos de una app de WhatsApp Business
    # Cloud API de Meta.
    "WHATSAPP": (
        ProveedorInfo(
            clave="META",
            etiqueta="Meta (WhatsApp Business)",
            campos=(
                CampoProveedor("META_APP_ID", "App ID"),
                CampoProveedor("META_PHONE_NUMBER_ID", "Phone Number ID"),
                CampoProveedor("META_ACCESS_TOKEN", "Access Token"),
                CampoProveedor("META_BUSINESS_ACCOUNT_ID", "Business Account ID (WABA)"),
                CampoProveedor("META_WEBHOOK_VERIFY_TOKEN", "Webhook Verify Token"),
            ),
        ),
    ),
    # Llamadas (issues 289-291): `disponible=False` a propósito -- la
    # pantalla muestra su tab/toggle/campos igual, pero bloqueados (ver
    # docstring de `ProveedorInfo.disponible`). Campos genéricos para
    # hablarle a una PBX Issabel (Asterisk) por su interfaz de
    # administración (AMI).
    "LLAMADA": (
        ProveedorInfo(
            clave="PXB",
            etiqueta="Issabel (PBX)",
            campos=(
                CampoProveedor("PXB_HOST", "Host / IP del servidor", secreto=False),
                CampoProveedor("PXB_PUERTO", "Puerto AMI", secreto=False),
                CampoProveedor("PXB_USUARIO", "Usuario AMI"),
                CampoProveedor("PXB_SECRETO", "Secreto AMI"),
                CampoProveedor("PXB_EXTENSION_ORIGEN", "Extensión/troncal de origen", secreto=False),
            ),
            disponible=False,
        ),
    ),
}


def proveedores_de(canal: str) -> tuple[ProveedorInfo, ...]:
    """Los proveedores del catálogo para `canal` -- vacío si `canal` no
    existe en `CATALOGO`. Incluye proveedores `disponible=False` (issue
    290) tal cual -- ese filtro es responsabilidad de quien consuma este
    catálogo (ej. `admin_proveedores.py::_filas_proveedores`), no de esta
    función."""
    return CATALOGO.get(canal, ())


def variables_permitidas() -> frozenset[str]:
    """Todas las variables de entorno declaradas en el catálogo, sin
    importar canal/proveedor -- el allowlist que usará el mecanismo SSH de
    la Fase 2 (issue 04) para rechazar cualquier variable que no sea
    legítimamente de un proveedor de notificación."""
    return frozenset(
        campo.variable_env
        for proveedores in CATALOGO.values()
        for proveedor in proveedores
        for campo in proveedor.campos
    )
