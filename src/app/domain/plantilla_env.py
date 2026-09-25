# -*- coding: utf-8 -*-
"""
Plantilla del `.env` que viaja en cada respaldo (`.scratch/respaldos-y-restauracion`, ticket 03).

Al restaurar en un servidor nuevo, el `.env` lo pone Jesús a mano; esta plantilla le dice qué variables hacen falta y
con qué valores: lo que no es secreto va completo, los secretos ofuscados (`ABC****XYZ` -- 3 primeros + 3 últimos --
para reconocer cuál llave era; `****` si tienen menos de 12 caracteres, porque 3 + 3 revelaría media contraseña).
Se genera del `.env` real en cada respaldo, así que una variable nueva aparece sola (en "Otras", sin descripción).
Ante la duda, una variable se trata como secreta.
"""

import re

# (grupo, descripción, es_secreta) de cada variable conocida. El orden de los grupos es el de la plantilla.
CATALOGO: dict[str, tuple[str, str, bool]] = {
    "SECRET_KEY": ("Aplicación", "Firma las sesiones de la app. Si cambia, todos deben volver a iniciar sesión.", True),
    "PG_PASSWORD": ("Base de datos", "Contraseña del usuario `paquetex` de Postgres (servicio `db`).", True),
    "SMS_OVERRIDE_NUMBER": ("SMS", "Si se define, TODOS los SMS van a este número (pruebas). Vacío en producción.", False),
    "AWS_SNS_SMS_ENABLED": ("SMS", "Activa el envío de SMS por AWS SNS.", False),
    "AWS_ACCESS_KEY_ID": ("SMS", "Llave de AWS para publicar SMS por SNS (usuario de solo `sns:Publish`).", True),
    "AWS_SECRET_ACCESS_KEY": ("SMS", "Secreto de la llave de AWS para SNS.", True),
    "AWS_REGION": ("SMS", "Región de AWS (SNS y S3).", False),
    "LIWA_API_KEY": ("SMS", "Proveedor de SMS Liwa: llave de la API.", True),
    "LIWA_ACCOUNT": ("SMS", "Proveedor de SMS Liwa: cuenta.", False),
    "LIWA_PASSWORD": ("SMS", "Proveedor de SMS Liwa: contraseña.", True),
    "LIWA_AUTH_URL": ("SMS", "Proveedor de SMS Liwa: URL de autenticación.", False),
    "LIWA_FROM_NAME": ("SMS", "Proveedor de SMS Liwa: remitente.", False),
    "TWILIO_ACCOUNT_SID": ("SMS", "Proveedor de SMS Twilio: identificador de la cuenta.", True),
    "TWILIO_AUTH_TOKEN": ("SMS", "Proveedor de SMS Twilio: token.", True),
    "TWILIO_MESSAGING_SERVICE_SID": ("SMS", "Proveedor de SMS Twilio: servicio de mensajería.", True),
    "SMTP_HOST": ("Correo", "Servidor SMTP para enviar correos.", False),
    "SMTP_PORT": ("Correo", "Puerto del servidor SMTP.", False),
    "SMTP_USER": ("Correo", "Usuario del servidor SMTP.", False),
    "SMTP_PASSWORD": ("Correo", "Contraseña del servidor SMTP.", True),
    "SMTP_FROM_EMAIL": ("Correo", "Remitente de los correos.", False),
    "SMTP_USE_TLS": ("Correo", "Usar STARTTLS con el servidor SMTP.", False),
    "SMTP_USE_SSL": ("Correo", "Usar SSL directo con el servidor SMTP.", False),
    "EMAIL_OVERRIDE_ADDRESS": ("Correo", "Si se define, TODOS los correos van a esta dirección (pruebas).", False),
    "RESPALDO_CORREO_AVISOS": ("Respaldos", "Correos (separados por coma) que reciben los avisos de respaldos.", False),
    "RESPALDO_S3_BUCKET": ("Respaldos", "Bucket de S3 de los respaldos (una carpeta por dominio).", False),
    "RESPALDO_AWS_ACCESS_KEY_ID": ("Respaldos", "Llave de AWS de este servidor: SOLO puede subir a su carpeta.", True),
    "RESPALDO_AWS_SECRET_ACCESS_KEY": ("Respaldos", "Secreto de la llave de respaldos de este servidor.", True),
    "AWS_S3_BUCKET_NAME": ("Fotos (S3)", "Bucket de S3 donde viven las fotos de los paquetes.", False),
    "AWS_S3_PREFIX_FOTOS": ("Fotos (S3)", "Carpeta (prefijo) de las fotos dentro del bucket.", False),
    "AWS_S3_ACCESS_KEY_ID": ("Fotos (S3)", "Llave de AWS para las fotos.", True),
    "AWS_S3_SECRET_ACCESS_KEY": ("Fotos (S3)", "Secreto de la llave de AWS para las fotos.", True),
    "WHATSAPP_SOPORTE_NUMERO": ("Conjunto", "Número de WhatsApp de soporte que ven los residentes.", False),
    "DEPLOY_SSH_USER": ("Proveedores", "Usuario SSH con el que la app aplica cambios de proveedores en el host.", False),
    "V1_DATABASE_URL": ("Importador v1", "Conexión de solo lectura a la base de la v1 (incluye contraseña).", True),
    "V1_AWS_ACCESS_KEY_ID": ("Importador v1", "Llave de AWS de la v1 para copiar sus fotos.", True),
    "V1_AWS_SECRET_ACCESS_KEY": ("Importador v1", "Secreto de la llave de AWS de la v1.", True),
    "V1_AWS_S3_BUCKET": ("Importador v1", "Bucket de fotos de la v1.", False),
    "V1_AWS_REGION": ("Importador v1", "Región del bucket de la v1.", False),
    "IMPORTADOR_V1_ESPEJO_ACTIVO": ("Importador v1", "Activa el importador espejo desde la v1 (cron cada 15 min).", False),
}
_OTRAS = "Otras (sin descripción: variables que el catálogo aún no conoce)"
_PARECE_SECRETA = re.compile(r"KEY|SECRET|PASSWORD|PASSWD|PASS\b|TOKEN|_SID$|DATABASE_URL|CREDENTIAL", re.IGNORECASE)
_LARGO_MINIMO_PARA_MOSTRAR_PUNTAS = 12


def es_secreta(variable: str) -> bool:
    if variable in CATALOGO:
        return CATALOGO[variable][2]
    return bool(_PARECE_SECRETA.search(variable))


def ofuscar(valor: str) -> str:
    if len(valor) < _LARGO_MINIMO_PARA_MOSTRAR_PUNTAS:
        return "****"
    return f"{valor[:3]}****{valor[-3:]}"


def _leer_env(texto: str) -> list[tuple[str, str]]:
    variables = []
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        nombre, valor = linea.split("=", 1)
        nombre = nombre.removeprefix("export ").strip()
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "'\"":
            valor = valor[1:-1]
        variables.append((nombre, valor))
    return variables


def generar_plantilla_env(texto_env: str, dominio: str, fecha: str) -> str:
    variables = _leer_env(texto_env)
    grupos: dict[str, list[str]] = {}
    orden_grupos = list(dict.fromkeys(g for g, _, _ in CATALOGO.values())) + [_OTRAS]
    for nombre, valor in variables:
        grupo, descripcion, _ = CATALOGO.get(nombre, (_OTRAS, "", False))
        mostrado = ofuscar(valor) if (valor and es_secreta(nombre)) else valor
        comentario = f"# {descripcion}{' (secreto: ofuscado)' if es_secreta(nombre) and valor else ''}" if descripcion else (
            "# (secreto: ofuscado)" if es_secreta(nombre) and valor else ""
        )
        bloque = ([comentario] if comentario else []) + [f"{nombre}={mostrado}"]
        grupos.setdefault(grupo, []).append("\n".join(bloque))
    lineas = [
        f"# Plantilla del .env de {dominio} -- generada en el respaldo del {fecha} (hora Colombia).",
        "# NO es el .env real: los secretos van ofuscados. Al restaurar, copiar como .env y reemplazar cada valor con",
        "# **** por el secreto real.",
    ]
    for grupo in orden_grupos:
        if grupo in grupos:
            lineas += ["", f"# ===== {grupo} =====", *grupos[grupo]]
    return "\n".join(lineas) + "\n"
