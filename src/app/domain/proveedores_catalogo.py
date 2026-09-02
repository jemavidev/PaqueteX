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
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CampoProveedor:
    """Un campo de configuración de un proveedor -- una variable de entorno
    en `.env` del servidor."""

    variable_env: str
    etiqueta: str
    secreto: bool = True
    tipo: str = "texto"  # "texto" | "booleano" -- gobierna el input que arme la Fase 2


@dataclass(frozen=True)
class ProveedorInfo:
    """Un proveedor disponible para un canal. `clave` es el identificador
    estable que se guarda en `ProveedorConfig.proveedor` -- nunca cambia
    aunque `etiqueta` sea puramente cosmética."""

    clave: str
    etiqueta: str
    campos: tuple[CampoProveedor, ...]


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
            campos=(
                CampoProveedor("AWS_SNS_SMS_ENABLED", "Habilitado", secreto=False, tipo="booleano"),
                CampoProveedor("AWS_ACCESS_KEY_ID", "Access Key ID"),
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
}


def proveedores_de(canal: str) -> tuple[ProveedorInfo, ...]:
    """Los proveedores del catálogo para `canal` -- vacío si el canal no
    tiene ningún proveedor real todavía (WhatsApp, Llamadas)."""
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
