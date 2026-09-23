# -*- coding: utf-8 -*-
"""
Sugerencia de nombre desde Contactos externos para `/announce`
(`.scratch/contactos-externos-en-announce`).

Cuando el Staff teclea un Teléfono que no existe como Persona, `/announce`
consulta acá si coincide con un `ContactoExterno` y, de ser así, le sugiere
ese nombre en vez de pedírselo a mano. Es una consulta de SOLO LECTURA: no
crea, modifica, marca ni enlaza nada, y devuelve únicamente el NOMBRE -- la
vista de administración de Contactos externos es exclusiva de Admin, pero
esta sugerencia la ve todo el Staff (Operador incluido), así que no expone
ningún otro dato del contacto (otros teléfonos, usuarios de WhatsApp,
fuentes, fechas). Por eso vive aparte de `contacto_externo_service` (fusión,
importación y listado de esa vista): su superficie es deliberadamente mínima.
"""

from .contacto_externo import ContactoExterno, ContactoExternoTelefono
from .telefono import normalizar_telefono
from .texto import normalizar_nombre


def sugerir_nombre_de_contacto_externo(session, tipo: str, valor: str) -> str | None:
    """El nombre del `ContactoExterno` que coincide EXACTAMENTE con `valor`,
    ya en su forma canónica (`normalizar_nombre`: el mismo que se le
    registraría a la Persona), o `None` si no hay coincidencia.

    `tipo` es `"telefono"` (`"whatsapp"` llega con el ticket 03; mientras
    tanto, y para cualquier otro valor, devuelve `None`). `valor` se
    normaliza igual que la importación de Contactos externos, así que
    cualquier formato de entrada (con o sin +57, espacios, guiones) coincide
    con lo guardado -- y coincide con CUALQUIERA de los teléfonos del
    contacto. La llave es única a nivel de tabla: hay a lo sumo un contacto
    por valor, nunca varias sugerencias entre las cuales elegir.

    Nunca lanza por un valor inválido -- mientras se teclea, un valor a medio
    terminar es un caso normal (mismo criterio que `buscar_persona_por_
    telefono`)."""
    if tipo != "telefono":
        return None
    try:
        telefono = normalizar_telefono(valor)
    except ValueError:
        return None
    nombre = (
        session.query(ContactoExterno.nombre)
        .join(
            ContactoExternoTelefono,
            ContactoExternoTelefono.contacto_externo_id == ContactoExterno.id,
        )
        .filter(ContactoExternoTelefono.telefono == telefono)
        .scalar()
    )
    if nombre is None:
        return None
    return normalizar_nombre(nombre)
