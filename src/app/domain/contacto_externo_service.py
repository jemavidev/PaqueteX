# -*- coding: utf-8 -*-
"""
Servicio de dominio de `ContactoExterno` -- fusión e importación incremental
de fuentes externas (módulo "Consolidación de contactos externos",
`.scratch/contactos-externos`).

`fusionar_fuentes` es una función PURA: no toca la base de datos, agrupa
filas de cualquier fuente por teléfono compartido (componentes conexas,
unión-búsqueda) -- separada así para poder probar toda la lógica de fusión
sin sesión de BD ni HTTP de por medio.
"""

from dataclasses import dataclass

from .contacto_externo import ContactoExterno, ContactoExternoTelefono
from .telefono import normalizar_telefono

# Tags de fuente conocidos -- usados tanto por el script de importación como
# por la regla de desempate de nombre (gana Google Contacts).
FUENTE_GOOGLE_CONTACTS = "google_contacts"
FUENTE_PRODUCCION_V1 = "produccion_v1"


@dataclass(frozen=True)
class FilaFuenteContacto:
    """Una fila cruda de una fuente externa, antes de fusionar. `telefonos`
    viene TAL CUAL como lo trae la fuente (sin normalizar) -- `fusionar_fuentes`
    normaliza y descarta los que no logren normalizarse."""

    nombre: str
    telefonos: tuple[str, ...]
    fuente: str


@dataclass(frozen=True)
class ContactoExternoConsolidado:
    """Resultado de `fusionar_fuentes` -- un contacto ya fusionado, listo
    para persistirse (`importar_contactos_externos`)."""

    nombre: str
    telefonos: frozenset
    fuentes: frozenset


def fusionar_fuentes(filas: list[FilaFuenteContacto]) -> list[ContactoExternoConsolidado]:
    """Fusiona filas de cualquier fuente en contactos consolidados.

    Dos filas (de la misma fuente o de fuentes distintas) que compartan
    CUALQUIER teléfono terminan en el mismo contacto -- incluyendo el caso
    "puente" (fila A comparte un teléfono con fila B, que a su vez comparte
    otro teléfono con fila C: las tres terminan juntas). Descarta filas sin
    nombre o sin ningún teléfono que logre normalizarse; un teléfono
    individual no reconocible se ignora sin descartar el resto de la fila si
    tiene otro teléfono válido.

    Cuando el nombre difiere entre fuentes para el mismo contacto, gana el
    de `FUENTE_GOOGLE_CONTACTS`; si ninguna fila del grupo vino de ahí, gana
    la primera fila en el orden de aparición.
    """
    padre: dict[str, str] = {}

    def encontrar(x: str) -> str:
        raiz = x
        while padre[raiz] != raiz:
            raiz = padre[raiz]
        while padre[x] != raiz:
            padre[x], x = raiz, padre[x]
        return raiz

    def unir(a: str, b: str) -> None:
        ra, rb = encontrar(a), encontrar(b)
        if ra != rb:
            padre[rb] = ra

    filas_validas: list[tuple[str, list[str], str]] = []
    for fila in filas:
        nombre = (fila.nombre or "").strip()
        telefonos_norm = []
        for tel in fila.telefonos:
            try:
                telefonos_norm.append(normalizar_telefono(tel))
            except ValueError:
                continue
        if not nombre or not telefonos_norm:
            continue

        for tel in telefonos_norm:
            padre.setdefault(tel, tel)
        for tel in telefonos_norm[1:]:
            unir(telefonos_norm[0], tel)

        filas_validas.append((nombre, telefonos_norm, fila.fuente))

    grupos: dict[str, dict] = {}
    for nombre, telefonos_norm, fuente in filas_validas:
        raiz = encontrar(telefonos_norm[0])
        grupo = grupos.setdefault(
            raiz, {"filas_nombre": [], "telefonos": set(), "fuentes": set()}
        )
        grupo["filas_nombre"].append((nombre, fuente))
        grupo["telefonos"].update(telefonos_norm)
        grupo["fuentes"].add(fuente)

    resultado = []
    for grupo in grupos.values():
        nombre_google = next(
            (n for n, f in grupo["filas_nombre"] if f == FUENTE_GOOGLE_CONTACTS), None
        )
        nombre_final = nombre_google if nombre_google is not None else grupo["filas_nombre"][0][0]
        resultado.append(
            ContactoExternoConsolidado(
                nombre=nombre_final,
                telefonos=frozenset(grupo["telefonos"]),
                fuentes=frozenset(grupo["fuentes"]),
            )
        )
    return resultado


@dataclass(frozen=True)
class ResumenImportacion:
    """Resultado de `importar_contactos_externos` -- para que el script (o
    quien lo invoque) sepa qué pasó, incluyendo los casos que necesitan
    revisión manual (`conflictos`)."""

    creados: int
    enriquecidos: int
    conflictos: list


def importar_contactos_externos(session, filas_nuevas: list[FilaFuenteContacto]) -> ResumenImportacion:
    """Fusiona `filas_nuevas` (`fusionar_fuentes`) y cruza cada grupo contra
    los `ContactoExternoTelefono` YA existentes en la tabla:

    - Ningún teléfono existe -> crea un `ContactoExterno` nuevo.
    - Coincide con exactamente uno existente -> lo enriquece (suma teléfonos
      y fuentes nuevas, NUNCA sobreescribe `nombre`).
    - Coincide con más de uno existente y distinto -> NO se fusionan
      automáticamente; se reporta en `conflictos` para revisión manual.

    Reimportar el mismo lote es un no-op real (no crea filas nuevas, todo ya
    coincide con teléfonos existentes).
    """
    consolidados = fusionar_fuentes(filas_nuevas)
    creados = 0
    enriquecidos = 0
    conflictos = []

    for c in consolidados:
        existentes = (
            session.query(ContactoExternoTelefono)
            .filter(ContactoExternoTelefono.telefono.in_(c.telefonos))
            .all()
        )
        ids_existentes = {row.contacto_externo_id for row in existentes}

        if not ids_existentes:
            contacto = ContactoExterno(nombre=c.nombre, fuentes=sorted(c.fuentes))
            session.add(contacto)
            session.flush()
            for tel in c.telefonos:
                session.add(
                    ContactoExternoTelefono(contacto_externo_id=contacto.id, telefono=tel)
                )
            creados += 1
        elif len(ids_existentes) == 1:
            contacto_id = next(iter(ids_existentes))
            contacto = session.get(ContactoExterno, contacto_id)
            fuentes_actuales = set(contacto.fuentes or [])
            fuentes_nuevas = fuentes_actuales | c.fuentes
            if fuentes_nuevas != fuentes_actuales:
                contacto.fuentes = sorted(fuentes_nuevas)
            telefonos_actuales = {row.telefono for row in existentes}
            for tel in c.telefonos - telefonos_actuales:
                session.add(
                    ContactoExternoTelefono(contacto_externo_id=contacto_id, telefono=tel)
                )
            enriquecidos += 1
        else:
            conflictos.append(
                f"Los teléfonos {sorted(c.telefonos)} conectan {len(ids_existentes)} "
                "contactos ya existentes y distintos -- requiere revisión manual."
            )

    session.flush()
    return ResumenImportacion(creados=creados, enriquecidos=enriquecidos, conflictos=conflictos)


_POR_PAGINA = 20


def buscar_contactos_externos(session, q: str = None, pagina: int = 1):
    """Lista paginada de `ContactoExterno`, opcionalmente filtrada por `q`
    (coincidencia parcial de nombre, o el teléfono completo en cualquier
    formato de entrada). Devuelve `(contactos, total_paginas)` -- cada
    `ContactoExterno` trae sus teléfonos precargados en `.telefonos_cargados`
    (evita N+1 al listar)."""
    query = session.query(ContactoExterno)

    termino = (q or "").strip()
    if termino:
        telefono_normalizado = None
        try:
            telefono_normalizado = normalizar_telefono(termino)
        except ValueError:
            pass

        if telefono_normalizado is not None:
            ids_por_telefono = [
                row.contacto_externo_id
                for row in session.query(ContactoExternoTelefono.contacto_externo_id)
                .filter(ContactoExternoTelefono.telefono == telefono_normalizado)
                .all()
            ]
            query = query.filter(ContactoExterno.id.in_(ids_por_telefono))
        else:
            query = query.filter(ContactoExterno.nombre.ilike(f"%{termino}%"))

    total = query.count()
    total_paginas = max(1, -(-total // _POR_PAGINA))
    contactos = (
        query.order_by(ContactoExterno.nombre.asc())
        .offset((pagina - 1) * _POR_PAGINA)
        .limit(_POR_PAGINA)
        .all()
    )

    if contactos:
        telefonos_por_contacto: dict = {c.id: [] for c in contactos}
        for row in (
            session.query(ContactoExternoTelefono)
            .filter(ContactoExternoTelefono.contacto_externo_id.in_(telefonos_por_contacto.keys()))
            .all()
        ):
            telefonos_por_contacto[row.contacto_externo_id].append(row.telefono)
        for c in contactos:
            c.telefonos_cargados = telefonos_por_contacto.get(c.id, [])

    return contactos, total_paginas
