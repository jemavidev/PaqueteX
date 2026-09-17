# -*- coding: utf-8 -*-
"""
Normalización y validación del usuario de WhatsApp.

Promovido desde `persona_service.py` (donde vivía privado, usado solo por
`Persona`) a un módulo compartido -- mismo patrón que `telefono.py` ya
establece para el teléfono (`.scratch/contactos-externos-import-export`,
que lo reutiliza para el motor de fusión de contactos externos, en vez de
inventar una segunda regla de normalización paralela).

Regla (reglas publicadas por Meta, rollout 2026, issue 67 de
`.scratch/pendientes-cliente`): el `@` inicial es puramente de presentación
y se recorta siempre; Meta identifica usuarios sin distinguir mayúsculas de
minúsculas (issue 162), así que `Jesus.Villalobos` y `jesus.villalobos`
deben resolver al MISMO valor. Formato válido: 3 a 35 caracteres, letras
latinas, números, puntos o guion bajo.

`normalizar_whatsapp_usuario` NUNCA lanza (incluso con valor vacío) --
separado de `validar_whatsapp_usuario` porque algunos llamadores necesitan
distinguir "vacío" (un no-op válido, ej. un campo que no se está tocando)
de "con forma inválida" (error real). Quien necesite la forma canónica
válida o nada llama a ambos en secuencia.
"""

import re

WHATSAPP_USUARIO_RE = re.compile(r"^[A-Za-z0-9._]{3,35}$")


def normalizar_whatsapp_usuario(raw: str) -> str:
    """Forma canónica de un usuario de WhatsApp: sin espacios, sin `@`
    inicial, todo en minúscula. No valida formato -- un valor vacío o
    inválido normaliza igual, sin lanzar (ver `validar_whatsapp_usuario`)."""
    return (raw or "").strip().lstrip("@").lower()


def validar_whatsapp_usuario(whatsapp_usuario: str) -> None:
    """Valida la forma de un usuario de WhatsApp ya normalizado (sin `@`
    inicial).

    Raises:
        ValueError: si no cumple el formato (3-35 caracteres, letras,
            números, puntos o guion bajo).
    """
    if not WHATSAPP_USUARIO_RE.match(whatsapp_usuario):
        raise ValueError(
            f"El usuario de WhatsApp {whatsapp_usuario!r} no es válido -- usa "
            "entre 3 y 35 letras, números, puntos o guion bajo (sin el @)."
        )
