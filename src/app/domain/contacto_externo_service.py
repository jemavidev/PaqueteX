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

from sqlalchemy import func, or_, text

from .contacto_externo import (
    ContactoExterno,
    ContactoExternoTelefono,
    ContactoExternoWhatsapp,
    FuenteContactoExterno,
)
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

    # Catálogo de fuentes (issue 362, .scratch/pendientes-cliente): cada fuente
    # que usa algún contacto VÁLIDO se resuelve a su forma canónica --
    # registrándola con el siguiente número si es nueva ("whatsapp" reutiliza
    # "Whatsapp"). Se hace en el orden en que aparecen en `filas_nuevas`, para
    # que una carga con varias fuentes nuevas las numere en ese orden, y solo
    # con las de contactos válidos: un archivo cuyas filas se descartan todas
    # no consume un número.
    orden_aparicion: dict[str, int] = {}
    for fila in filas_nuevas:
        orden_aparicion.setdefault(fila.fuente, len(orden_aparicion))
    usadas = {f for c in consolidados for f in c.fuentes}
    nombre_canonico = {
        f: obtener_o_crear_fuente(session, f).nombre
        for f in sorted(usadas, key=lambda f: orden_aparicion.get(f, len(orden_aparicion)))
    }

    creados = 0
    enriquecidos = 0
    conflictos = []

    for c in consolidados:
        fuentes_canonicas = frozenset(nombre_canonico[f] for f in c.fuentes)
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
            contacto = ContactoExterno(nombre=c.nombre, fuentes=sorted(fuentes_canonicas))
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
            fuentes_nuevas = fuentes_actuales | fuentes_canonicas
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


MAX_LARGO_FUENTE = 40


def _nombre_fuente_limpio(nombre: str) -> str:
    """Recorta y colapsa espacios -- "  Mi   fuente " y "Mi fuente" son la
    misma fuente."""
    return " ".join((nombre or "").split())


def _clave_fuente(nombre: str) -> str:
    """Llave de comparación de una fuente: sin diferencias de espacios ni de
    mayúsculas ("whatsapp" = "Whatsapp")."""
    return _nombre_fuente_limpio(nombre).lower()


def listar_fuentes(session) -> list[FuenteContactoExterno]:
    """El catálogo completo de fuentes, en orden de número (la más antigua
    primero) -- puebla el `<select>` del formulario de import y la leyenda de
    `/administracion/contactos-externos` (issue 362). Reemplaza al antiguo
    `fuentes_existentes`, que salía de los propios contactos y no tenía
    orden ni número."""
    return session.query(FuenteContactoExterno).order_by(FuenteContactoExterno.numero).all()


def obtener_o_crear_fuente(session, nombre: str) -> FuenteContactoExterno:
    """La fuente `nombre` del catálogo, ignorando mayúsculas y espacios de
    más; si no existe la crea con el SIGUIENTE número (1 si el catálogo está
    vacío) -- el número de una fuente nunca cambia.

    Crear toma un bloqueo de la tabla: sin él, dos imports simultáneos con
    fuentes nuevas leerían el mismo máximo y chocarían. El bloqueo no impide
    leer, solo serializa las altas, y se libera con el commit/rollback de la
    transacción -- por eso una carga revertida no deja huecos en la
    numeración.

    Raises:
        ValueError: nombre vacío o de más de 40 caracteres (el largo de
            `contactos_externos.fuentes`).
    """
    limpio = _nombre_fuente_limpio(nombre)
    if not limpio:
        raise ValueError("La fuente no puede estar vacía.")
    if len(limpio) > MAX_LARGO_FUENTE:
        raise ValueError(f"La fuente no puede pasar de {MAX_LARGO_FUENTE} caracteres.")

    def buscar():
        return (
            session.query(FuenteContactoExterno)
            .filter(func.lower(FuenteContactoExterno.nombre) == func.lower(limpio))
            .one_or_none()
        )

    existente = buscar()
    if existente is not None:
        return existente
    session.execute(text("LOCK TABLE fuentes_contactos_externos IN EXCLUSIVE MODE"))
    existente = buscar()  # otra transacción pudo crearla mientras esperábamos el bloqueo
    if existente is not None:
        return existente
    siguiente = (session.query(func.max(FuenteContactoExterno.numero)).scalar() or 0) + 1
    fuente = FuenteContactoExterno(numero=siguiente, nombre=limpio)
    session.add(fuente)
    session.flush()
    return fuente


def _numerar_fuentes(session, contactos: list[ContactoExterno]) -> None:
    """Deja en cada contacto `.fuentes_numeradas`: la lista de pares
    `(numero, nombre)` de sus fuentes, de menor a mayor número y sin repetir
    (dos grafías de la misma fuente cuentan una vez). El nombre es el del
    catálogo. Una fuente que no esté en el catálogo (solo posible si se cargó
    saltándose `importar_contactos_externos`) queda al final como
    `(None, nombre)`, para que la vista la muestre tal cual en vez de
    esconderla."""
    if not contactos:
        return
    catalogo = listar_fuentes(session)
    numero_por_clave = {_clave_fuente(f.nombre): f.numero for f in catalogo}
    nombre_por_numero = {f.numero: f.nombre for f in catalogo}
    for c in contactos:
        numeros = set()
        sin_numero = []
        for nombre in c.fuentes or []:
            numero = numero_por_clave.get(_clave_fuente(nombre))
            if numero is not None:
                numeros.add(numero)
            elif nombre not in sin_numero:
                sin_numero.append(nombre)
        c.fuentes_numeradas = [(n, nombre_por_numero[n]) for n in sorted(numeros)] + [
            (None, nombre) for nombre in sin_numero
        ]


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
    """Lista paginada de `ContactoExterno`, opcionalmente filtrada por `q`:
    el término matchea si coincide con CUALQUIERA de (issue 356,
    .scratch/pendientes-cliente) el nombre (parcial), el teléfono completo
    en cualquier formato de entrada, o el usuario de WhatsApp (parcial, sobre
    su forma canónica -- `@Ana` y `ana` buscan igual). Antes era excluyente:
    un término que normalizaba como teléfono buscaba SOLO por teléfono, y
    cualquier otro SOLO por nombre. Devuelve `(contactos, total_paginas,
    total)` -- `total` es la cantidad real de contactos que matchean `q`
    (.scratch/pendientes-cliente, issue 338: la paginación de la vista
    necesitaba mostrarlo, y ya se calculaba acá para derivar
    `total_paginas`, solo que se descartaba). Cada `ContactoExterno` trae
    sus teléfonos y usuarios de WhatsApp precargados en
    `.telefonos_cargados`/`.whatsapps_cargados` (evita N+1 al listar), y sus
    fuentes numeradas en `.fuentes_numeradas` (ver `_numerar_fuentes`)."""
    query = session.query(ContactoExterno)

    termino = (q or "").strip()
    if termino:
        condiciones = [ContactoExterno.nombre.ilike(f"%{termino}%")]

        try:
            telefono_normalizado = normalizar_telefono(termino)
        except ValueError:
            pass
        else:
            condiciones.append(
                ContactoExterno.id.in_(
                    session.query(ContactoExternoTelefono.contacto_externo_id).filter(
                        ContactoExternoTelefono.telefono == telefono_normalizado
                    )
                )
            )

        # `autoescape`: `_` es válido en un usuario de WhatsApp y en LIKE
        # sería un comodín de un carácter. La forma canónica ya viene en
        # minúscula (`normalizar_whatsapp_usuario`), igual que lo guardado.
        whatsapp_normalizado = normalizar_whatsapp_usuario(termino)
        if whatsapp_normalizado:
            condiciones.append(
                ContactoExterno.id.in_(
                    session.query(ContactoExternoWhatsapp.contacto_externo_id).filter(
                        ContactoExternoWhatsapp.whatsapp_usuario.contains(
                            whatsapp_normalizado, autoescape=True
                        )
                    )
                )
            )

        query = query.filter(or_(*condiciones))

    total = query.count()
    total_paginas = max(1, -(-total // _POR_PAGINA))
    contactos = (
        query.order_by(ContactoExterno.nombre.asc())
        .offset((pagina - 1) * _POR_PAGINA)
        .limit(_POR_PAGINA)
        .all()
    )

    _precargar_telefonos_y_whatsapps(session, contactos)
    _numerar_fuentes(session, contactos)
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
    # Issue 381: la exportación antepone un apóstrofo a un nombre que Excel leería como fórmula (`=`, `+`, `-`, `@`);
    # al volver a importar ese mismo archivo, el apóstrofo no es parte del nombre.
    if nombre.startswith("'") and nombre[1:2] in ("=", "+", "-", "@"):
        nombre = nombre[1:]
    telefonos = tuple(t.strip() for t in (row.get("Teléfonos") or "").split(";") if t.strip())
    whatsapps = tuple(w.strip() for w in (row.get("WhatsApp") or "").split(";") if w.strip())
    return FilaFuenteContacto(nombre=nombre, telefonos=telefonos, whatsapps=whatsapps, fuente=fuente)
