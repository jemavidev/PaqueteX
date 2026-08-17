# -*- coding: utf-8 -*-
"""
Clasificación de un valor de contacto tecleado en un solo campo -- Teléfono o
WhatsApp, sin que quien escribe tenga que elegir un tipo de campo a mano
(`.scratch/ocupante-principal-escenarios`, ticket 01).

Única fuente de verdad para esta regla -- antes vivía privada en
`announce_new.py` (`_clasificar`, que además reconoce Torre+Apto, un caso
propio de esa vista que NO se generaliza acá).

Teléfono delega en `telefono.normalizar_telefono` -- no reimplementa su regla
(retroalimentación en vivo, hallazgo de code-review: el campo único de
`/announce` y el contacto de "nuevo residente" de `/paquetes` solo aceptaban
el celular colombiano pelado de 10 dígitos, "300...", nunca "+57300...", así
que quien pegaba/tecleaba un número ya en formato internacional se quedaba
sin resultado o con un rechazo "contacto inválido" para un número que sí era
válido). Delegar evita mantener DOS reglas de "qué es un teléfono" que
puedan divergir, y de paso generaliza a cualquier país sin necesitar una
lista de indicativos: `normalizar_telefono` ya acepta cualquier número con
`+` de 10 a 15 dígitos (rango E.164) sin validar de qué país es -- así que
"+13002596319" (EE.UU.), "+584121234567" (Venezuela) o "+34612345678"
(España) clasifican como teléfono exactamente igual que "+573001234567"
(Colombia), sin que este módulo necesite saber que existen. Sin `+`, la
única forma reconocible sigue siendo el celular colombiano (empieza en `3`,
10 dígitos) -- fuera de Colombia no hay forma de saber el país de un número
sin indicativo, así que ahí `normalizar_telefono` sigue exigiendo el `+`.

WhatsApp acepta el usuario con o sin `@` inicial (conversación 2026-08-17,
pedido explícito -- mismo principio que el `+57` de teléfono: "con la @ y
sin la @" deben llevar al mismo resultado). `persona_service.py` ya
resolvía esto para BUSCAR/CREAR la Persona (`get_or_create_persona_por_
whatsapp`/`buscar_persona_por_whatsapp` hacen su propio `.lstrip("@")`) --
el hueco estaba acá, en la clasificación: `"@ana.whats"` no empieza con una
letra (empieza con `@`), así que nunca llegaba a esas funciones -- se
quedaba en `"ninguno"` sin que la Persona ya existiera importara.
"""

from .telefono import normalizar_telefono

_MIN_LARGO_WHATSAPP = 3


def clasificar_contacto(valor: str) -> str:
    """`"telefono"` | `"whatsapp"` | `"ninguno"`.

    - Cualquier valor que `telefono.normalizar_telefono` acepte sin lanzar
      -> `"telefono"` (celular colombiano pelado o con indicativo, o
      cualquier número internacional con `+`).
    - Empieza con una letra (con o sin `@` inicial primero), al menos 3
      caracteres de usuario -> `"whatsapp"`.
    - Cualquier otro caso (vacío, a medio teclear, formato que no calza con
      ninguno de los dos) -> `"ninguno"`.

    Exige el valor COMPLETO, no un prefijo -- un teléfono a medio teclear no
    debe clasificar como inválido mientras la persona todavía está
    escribiendo (ver `announce_new.py` para el hallazgo original de este
    comportamiento en code-review). Se cumple gratis: `normalizar_telefono`
    ya lanza `ValueError` para cualquier cantidad de dígitos que no calce
    con un teléfono completo (colombiano o E.164 con `+`)."""
    valor = (valor or "").strip()
    if not valor:
        return "ninguno"
    try:
        normalizar_telefono(valor)
        return "telefono"
    except ValueError:
        pass
    usuario = valor[1:] if valor.startswith("@") else valor
    if usuario[:1].isalpha():
        return "whatsapp" if len(usuario) >= _MIN_LARGO_WHATSAPP else "ninguno"
    return "ninguno"
