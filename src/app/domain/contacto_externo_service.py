# -*- coding: utf-8 -*-
"""
Servicio de dominio de `ContactoExterno` -- fusión e importación incremental
de fuentes externas (módulo "Consolidación de contactos externos",
`.scratch/contactos-externos`, extendido en `.scratch/contactos-externos-
import-export` con WhatsApp como segunda llave de fusión).

`fusionar_fuentes` es una función PURA: no toca la base de datos, agrupa
filas de cualquier fuente por TELÉFONO O WHATSAPP compartido (componentes
conexas, unión-búsqueda sobre dos tipos de llave) -- separada así para poder
probar toda la lógica de fusión sin sesión de BD ni HTTP de por medio.
"""

from dataclasses import dataclass, field

from .contacto_externo import ContactoExterno, ContactoExternoTelefono, ContactoExternoWhatsapp
from .telefono import normalizar_telefono
from .whatsapp import normalizar_whatsapp_usuario, validar_whatsapp_usuario

# Tags de fuente conocidos -- usados tanto por el script de importación como
# por la regla de desempate de nombre (gana Google Contacts).
FUENTE_GOOGLE_CONTACTS = "google_contacts"
FUENTE_PRODUCCION_V1 = "produccion_v1"


@dataclass(frozen=True)
class FilaFuenteContacto:
    """Una fila cruda de una fuente externa, antes de fusionar. `telefonos`/
    `whatsapps` vienen TAL CUAL como los trae la fuente (sin normalizar) --
    `fusionar_fuentes` normaliza y descarta los valores individuales que no
    logren normalizarse/validarse, sin descartar la fila completa por eso."""

    nombre: str
    telefonos: tuple[str, ...]
    fuente: str
    whatsapps: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContactoExternoConsolidado:
    """Resultado de `fusionar_fuentes` -- un contacto ya fusionado, listo
    para persistirse (`importar_contactos_externos`)."""

    nombre: str
    telefonos: frozenset
    fuentes: frozenset
    whatsapps: frozenset = field(default_factory=frozenset)


def _identificadores_normalizados(fila: FilaFuenteContacto) -> tuple[list[str], list[str]]:
    """Normaliza los teléfonos y usuarios de WhatsApp de una fila, ignorando
    (sin descartar la fila) cualquier valor individual que no logre
    normalizarse/validarse -- compartida por `fusionar_fuentes` y
    `filas_descartadas` para que "qué es una fila válida" nunca diverja
    entre ambas."""
    telefonos_norm = []
    for tel in fila.telefonos:
        try:
            telefonos_norm.append(normalizar_telefono(tel))
        except ValueError:
            continue

    whatsapps_norm = []
    for wa in fila.whatsapps:
        wa_norm = normalizar_whatsapp_usuario(wa)
        try:
            validar_whatsapp_usuario(wa_norm)
        except ValueError:
            continue
        whatsapps_norm.append(wa_norm)

    return telefonos_norm, whatsapps_norm


def fusionar_fuentes(filas: list[FilaFuenteContacto]) -> list[ContactoExternoConsolidado]:
    """Fusiona filas de cualquier fuente en contactos consolidados.

    Dos filas (de la misma fuente o de fuentes distintas) que compartan
    CUALQUIER teléfono O CUALQUIER usuario de WhatsApp terminan en el mismo
    contacto -- incluyendo el caso "puente" (fila A comparte un
    identificador con fila B, que a su vez comparte otro identificador con
    fila C: las tres terminan juntas) y el caso cruzado (fila A comparte
    teléfono con fila B, que a su vez comparte WhatsApp -no teléfono- con
    fila C: las tres igual terminan juntas). Descarta filas sin nombre, o
    sin NINGÚN identificador (teléfono o WhatsApp) que logre normalizarse/
    validarse; un identificador individual inválido se ignora sin descartar
    el resto de la fila si tiene otro identificador válido (ver
    `filas_descartadas` para el detalle de qué se descartó y por qué).

    Cuando el nombre difiere entre fuentes para el mismo contacto, gana el
    de `FUENTE_GOOGLE_CONTACTS`; si ninguna fila del grupo vino de ahí, gana
    la primera fila en el orden de aparición.
    """
    # Llaves tageadas ("tel"/"wa") en vez de strings crudos -- teléfono
    # canónico siempre empieza con "+" y WhatsApp normalizado nunca lo trae,
    # así que hoy nunca colisionarían, pero tagear es explícito y no depende
    # de que esa propiedad se mantenga si alguna regla de normalización
    # cambia más adelante.
    padre: dict[tuple[str, str], tuple[str, str]] = {}

    def encontrar(x: tuple[str, str]) -> tuple[str, str]:
        raiz = x
        while padre[raiz] != raiz:
            raiz = padre[raiz]
        while padre[x] != raiz:
            padre[x], x = raiz, padre[x]
        return raiz

    def unir(a: tuple[str, str], b: tuple[str, str]) -> None:
        ra, rb = encontrar(a), encontrar(b)
        if ra != rb:
            padre[rb] = ra

    filas_validas: list[tuple[str, list[str], list[str], str]] = []
    for fila in filas:
        nombre = (fila.nombre or "").strip()
        telefonos_norm, whatsapps_norm = _identificadores_normalizados(fila)
        if not nombre or (not telefonos_norm and not whatsapps_norm):
            continue

        claves = [("tel", t) for t in telefonos_norm] + [("wa", w) for w in whatsapps_norm]
        for clave in claves:
            padre.setdefault(clave, clave)
        for clave in claves[1:]:
            unir(claves[0], clave)

        filas_validas.append((nombre, telefonos_norm, whatsapps_norm, fila.fuente))

    grupos: dict[tuple[str, str], dict] = {}
    for nombre, telefonos_norm, whatsapps_norm, fuente in filas_validas:
        clave_principal = ("tel", telefonos_norm[0]) if telefonos_norm else ("wa", whatsapps_norm[0])
        raiz = encontrar(clave_principal)
        grupo = grupos.setdefault(
            raiz, {"filas_nombre": [], "telefonos": set(), "whatsapps": set(), "fuentes": set()}
        )
        grupo["filas_nombre"].append((nombre, fuente))
        grupo["telefonos"].update(telefonos_norm)
        grupo["whatsapps"].update(whatsapps_norm)
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
                whatsapps=frozenset(grupo["whatsapps"]),
                fuentes=frozenset(grupo["fuentes"]),
            )
        )
    return resultado


def filas_descartadas(filas: list[FilaFuenteContacto]) -> list[str]:
    """Motivo de descarte de cada fila que `fusionar_fuentes` ignora (sin
    nombre, o sin ningún identificador válido) -- mismo criterio de validez
    (`_identificadores_normalizados`), para que el resumen de importación
    pueda informarle al admin qué filas no se procesaron y por qué, en vez
    de descartarlas en silencio."""
    motivos = []
    for i, fila in enumerate(filas, start=1):
        nombre = (fila.nombre or "").strip()
        telefonos_norm, whatsapps_norm = _identificadores_normalizados(fila)
        if not nombre:
            motivos.append(f"Fila {i}: sin nombre.")
        elif not telefonos_norm and not whatsapps_norm:
            motivos.append(f"Fila {i}: sin ningún teléfono o WhatsApp válido.")
    return motivos


@dataclass(frozen=True)
class ResumenImportacion:
    """Resultado de `importar_contactos_externos` -- para que el script (o
    quien lo invoque) sepa qué pasó, incluyendo los casos que necesitan
    revisión manual (`conflictos`) y las filas que se ignoraron
    (`descartados`, `.scratch/contactos-externos-import-export` -- antes se
    descartaban en silencio)."""

    creados: int
    enriquecidos: int
    conflictos: list
    descartados: list = field(default_factory=list)


def importar_contactos_externos(session, filas_nuevas: list[FilaFuenteContacto]) -> ResumenImportacion:
    """Fusiona `filas_nuevas` (`fusionar_fuentes`) y cruza cada grupo contra
    los `ContactoExternoTelefono`/`ContactoExternoWhatsapp` YA existentes en
    la tabla (las dos llaves de fusión):

    - Ningún identificador existe -> crea un `ContactoExterno` nuevo.
    - Coincide con exactamente uno existente -> lo enriquece: el NOMBRE se
      actualiza al de la fila más reciente (único campo de un solo valor
      que se sobreescribe); teléfonos y WhatsApp se ACUMULAN (suma los que
      falten, nunca elimina los que ya tenía).
    - Coincide con más de uno existente y distinto (por cualquier
      combinación de las dos llaves, incluyendo el caso cruzado: teléfono
      conecta con X, WhatsApp conecta con Y≠X) -> NO se fusionan
      automáticamente; se reporta en `conflictos` para revisión manual.

    Reimportar el mismo lote es un no-op real (no crea filas nuevas, todo ya
    coincide con identificadores existentes).
    """
    consolidados = fusionar_fuentes(filas_nuevas)
    creados = 0
    enriquecidos = 0
    conflictos = []

    for c in consolidados:
        existentes_tel = (
            session.query(ContactoExternoTelefono)
            .filter(ContactoExternoTelefono.telefono.in_(c.telefonos))
            .all()
        )
        existentes_wa = (
            session.query(ContactoExternoWhatsapp)
            .filter(ContactoExternoWhatsapp.whatsapp_usuario.in_(c.whatsapps))
            .all()
        )
        ids_existentes = {row.contacto_externo_id for row in existentes_tel} | {
            row.contacto_externo_id for row in existentes_wa
        }

        if not ids_existentes:
            contacto = ContactoExterno(nombre=c.nombre, fuentes=sorted(c.fuentes))
            session.add(contacto)
            session.flush()
            for tel in c.telefonos:
                session.add(
                    ContactoExternoTelefono(contacto_externo_id=contacto.id, telefono=tel)
                )
            for wa in c.whatsapps:
                session.add(
                    ContactoExternoWhatsapp(contacto_externo_id=contacto.id, whatsapp_usuario=wa)
                )
            creados += 1
        elif len(ids_existentes) == 1:
            contacto_id = next(iter(ids_existentes))
            contacto = session.get(ContactoExterno, contacto_id)
            if contacto.nombre != c.nombre:
                contacto.nombre = c.nombre
            fuentes_actuales = set(contacto.fuentes or [])
            fuentes_nuevas = fuentes_actuales | c.fuentes
            if fuentes_nuevas != fuentes_actuales:
                contacto.fuentes = sorted(fuentes_nuevas)
            telefonos_actuales = {row.telefono for row in existentes_tel}
            for tel in c.telefonos - telefonos_actuales:
                session.add(
                    ContactoExternoTelefono(contacto_externo_id=contacto_id, telefono=tel)
                )
            whatsapps_actuales = {row.whatsapp_usuario for row in existentes_wa}
            for wa in c.whatsapps - whatsapps_actuales:
                session.add(
                    ContactoExternoWhatsapp(contacto_externo_id=contacto_id, whatsapp_usuario=wa)
                )
            enriquecidos += 1
        else:
            partes = []
            if c.telefonos:
                partes.append(f"teléfono(s) {sorted(c.telefonos)}")
            if c.whatsapps:
                partes.append(f"WhatsApp {sorted(c.whatsapps)}")
            conflictos.append(
                f"Los {' y '.join(partes)} conectan {len(ids_existentes)} "
                "contactos ya existentes y distintos -- requiere revisión manual."
            )

    session.flush()
    return ResumenImportacion(
        creados=creados,
        enriquecidos=enriquecidos,
        conflictos=conflictos,
        descartados=filas_descartadas(filas_nuevas),
    )


_POR_PAGINA = 20


def _precargar_telefonos_y_whatsapps(session, contactos: list[ContactoExterno]) -> None:
    """Precarga `.telefonos_cargados`/`.whatsapps_cargados` sobre `contactos`
    ya traídos -- evita N+1, compartida por `buscar_contactos_externos` y
    `listar_todos_los_contactos_externos`."""
    if not contactos:
        return
    ids = [c.id for c in contactos]
    telefonos_por_contacto: dict = {cid: [] for cid in ids}
    for row in (
        session.query(ContactoExternoTelefono)
        .filter(ContactoExternoTelefono.contacto_externo_id.in_(ids))
        .all()
    ):
        telefonos_por_contacto[row.contacto_externo_id].append(row.telefono)
    whatsapps_por_contacto: dict = {cid: [] for cid in ids}
    for row in (
        session.query(ContactoExternoWhatsapp)
        .filter(ContactoExternoWhatsapp.contacto_externo_id.in_(ids))
        .all()
    ):
        whatsapps_por_contacto[row.contacto_externo_id].append(row.whatsapp_usuario)
    for c in contactos:
        c.telefonos_cargados = telefonos_por_contacto.get(c.id, [])
        c.whatsapps_cargados = whatsapps_por_contacto.get(c.id, [])


def fuentes_existentes(session) -> list[str]:
    """Valores de `fuentes` ya usados por algún `ContactoExterno`, sin
    duplicados y ordenados -- puebla el `<select>` del formulario de import
    (`.scratch/contactos-externos-import-export`): elegir entre fuentes ya
    usadas evita tags casi-duplicados por tipeo entre un import y el
    siguiente (ej. "WhatsApp Business" vs "whatsapp business")."""
    todas = session.query(ContactoExterno.fuentes).all()
    return sorted({f for (lista,) in todas for f in (lista or [])})


def listar_todos_los_contactos_externos(session) -> list[ContactoExterno]:
    """Listado COMPLETO de `ContactoExterno`, sin paginar y sin respetar
    ningún término de búsqueda -- usado únicamente por el export
    (`.scratch/contactos-externos-import-export`, decisión explícita del
    cliente: exportar siempre trae todo, para no exportar por error un
    subconjunto filtrado sin darse cuenta). Cada contacto trae sus teléfonos
    y usuarios de WhatsApp precargados, igual que `buscar_contactos_externos`."""
    contactos = session.query(ContactoExterno).order_by(ContactoExterno.nombre.asc()).all()
    _precargar_telefonos_y_whatsapps(session, contactos)
    return contactos


def buscar_contactos_externos(session, q: str = None, pagina: int = 1):
    """Lista paginada de `ContactoExterno`, opcionalmente filtrada por `q`
    (coincidencia parcial de nombre, o el teléfono completo en cualquier
    formato de entrada -- sin cambios, el buscador no se extendió a
    WhatsApp). Devuelve `(contactos, total_paginas, total)` -- `total` es la
    cantidad real de contactos que matchean `q` (.scratch/pendientes-
    cliente, issue 338: la paginación de la vista necesitaba mostrarlo, y
    ya se calculaba acá para derivar `total_paginas`, solo que se
    descartaba). Cada `ContactoExterno` trae sus teléfonos y usuarios de
    WhatsApp precargados en `.telefonos_cargados`/`.whatsapps_cargados`
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

    _precargar_telefonos_y_whatsapps(session, contactos)
    return contactos, total_paginas, total


COLUMNAS_PLANTILLA_CONTACTOS_EXTERNOS = ("Nombre", "Teléfonos", "WhatsApp")


def contactos_externos_a_filas_plantilla(contactos: list[ContactoExterno]) -> list[dict]:
    """Convierte contactos ya cargados (con `.telefonos_cargados`/
    `.whatsapps_cargados`, ver `buscar_contactos_externos`) a filas con las
    columnas EXACTAS de la plantilla de import (`COLUMNAS_PLANTILLA_
    CONTACTOS_EXTERNOS`) -- para que el CSV exportado se pueda volver a
    subir como import tal cual, sin editarlo (`.scratch/contactos-externos-
    import-export`)."""
    return [
        {
            "Nombre": c.nombre,
            "Teléfonos": ";".join(c.telefonos_cargados),
            "WhatsApp": ";".join(c.whatsapps_cargados),
        }
        for c in contactos
    ]


def fila_plantilla_a_fila_fuente(row: dict, fuente: str) -> FilaFuenteContacto:
    """Convierte una fila de la plantilla de import (columnas `Nombre`/
    `Teléfonos`/`WhatsApp`, esta última y `Teléfonos` separadas por `;`) en
    `FilaFuenteContacto` -- la normalización real ocurre después, dentro de
    `fusionar_fuentes`."""
    nombre = (row.get("Nombre") or "").strip()
    telefonos = tuple(t.strip() for t in (row.get("Teléfonos") or "").split(";") if t.strip())
    whatsapps = tuple(w.strip() for w in (row.get("WhatsApp") or "").split(";") if w.strip())
    return FilaFuenteContacto(nombre=nombre, telefonos=telefonos, whatsapps=whatsapps, fuente=fuente)
