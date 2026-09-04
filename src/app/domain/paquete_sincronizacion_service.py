# -*- coding: utf-8 -*-
"""
Sincronización de snapshot entre paquetes "hermanos" del mismo destinatario
(.scratch/paquetes-residentes-conexion) -- TERCERA excepción, acotada y
auditada, a la inmutabilidad del snapshot de `Paquete` (ADR-0001), hermana de
`corregir_apartamento`/`corregir_destinatario` (`paquete_lifecycle.py`).

Caso real reportado en vivo: "TOMAS LIBANO" tenía 2 paquetes ANUNCIADO/
RECIBIDO que debían compartir apartamento -- uno se anunció sin unidad
resuelta, al otro se le asignó Torre 2 · 302 más tarde, y esa asignación
nunca se enteró de que existía un hermano con el mismo destinatario
(`corregir_apartamento`/`corregir_destinatario` mutan UN solo `Paquete` a la
vez, por diseño). Este módulo es la costura donde esa propagación ocurre,
invocada EXPLÍCITAMENTE desde las rutas -- nunca desde dentro de `ocupante_
service`/`persona_service` mismos, para que el dominio de residentes siga
sin saber que `Paquete` existe (esa dirección de dependencia, hoy en un solo
sentido, es deliberada).

DOS FUNCIONES, NO UNA -- por qué el orden importa:

`paquetes_hermanos_confirmados` DEBE llamarse ANTES de tocar cualquier dato
de la Persona o de su padrón de Ocupante (nombre, teléfono, apartamento) --
la confirmación compara el snapshot YA CONGELADO de cada Paquete contra el
estado ACTUAL de esa Persona/sus Ocupantes (`candidatos_correccion`,
`destinatario_coincide_con_candidato_real` -- MISMO criterio que ya usa
`/paquetes` para su ícono de advertencia, nunca dos que puedan divergir).
Bug real encontrado construyendo esto (no solo teórico): `persona_service.
update_datos_personales` YA propaga un renombre a `Ocupante.nombre` de forma
síncrona (issue 189) -- si se resuelve la confirmación DESPUÉS de ese
cambio, el candidato ya tiene el nombre NUEVO, comparado contra el
`recipient_name` VIEJO del Paquete -- exactamente los paquetes que
necesitaban actualizarse dejan de "coincidir", auto-invalidando su propia
propagación. Lo mismo aplica a un cambio de unidad (`mover_ocupante`): una
vez que la Persona se mudó, ya no aparece en el padrón de su unidad VIEJA,
así que un Paquete con esa unidad vieja en su snapshot deja de poder
confirmarse contra ella. Resolver ANTES evita los dos casos por igual, sin
necesitar un criterio de confirmación distinto para cada campo.

`aplicar_snapshot_de_persona` se llama DESPUÉS, leyendo el estado YA
actualizado de la Persona -- copia `recipient_name`/`recipient_phone`/la
terna de apartamento a los hermanos ya resueltos.

`sincronizar_snapshot_a_hermanos` encadena las dos para los 3 triggers de
`/paquetes` que corrigen el PAQUETE (nunca a la Persona) ANTES de resolver
identidad -- ahí no existe la ventana de invalidación (la Persona todavía no
cambió cuando se resuelve). Los triggers de `/residentes`, que sí mutan a la
Persona directamente, deben usar las 2 funciones por separado, en ese orden.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .apartamento import Apartamento
from .paquete import Paquete
from .paquete_correccion_service import candidatos_correccion, persona_confirmada_del_destinatario
from .paquete_service import paquetes_abiertos_de_persona
from .persona import Persona
from .usuario import Usuario


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def paquetes_hermanos_confirmados(session: Session, persona: Persona) -> list[Paquete]:
    """Paquetes hermanos de `persona` (`ESTADOS_CORREGIBLES`, vía `paquetes_
    abiertos_de_persona`) cuyo destinatario ya está CONFIRMADO a ELLA
    ESPECÍFICAMENTE -- LLAMAR ANTES de mutar `persona` o su padrón de
    Ocupante (ver el docstring del módulo para el porqué).

    Bug real encontrado construyendo esto (no solo teórico): NO alcanza con
    "¿el destinatario está confirmado a ALGUIEN real?" (eso es lo que
    responde `destinatario_coincide_con_candidato_real`, el criterio del
    ícono de advertencia de `/paquetes` -- sirve ahí porque solo importa
    "confirmado sí/no"). Acá hace falta "¿confirmado a ESTA Persona,
    puntual?" -- sin esa distinción, un Anunciante (ej. un portero) que
    anuncia paquetes para varios residentes reales de la misma unidad
    (ej. "LAIS HERNANDEZ" y "RAFAEL TORRES") terminaba "sincronizando" el
    paquete de Rafael con el nombre/teléfono del portero, porque ese
    paquete SÍ estaba confirmado (a Rafael) pero `paquetes_abiertos_de_
    persona(portero)` lo trae igual (por `announced_by_persona_id`).
    `persona_confirmada_del_destinatario` resuelve la identidad REAL
    (`Persona.id`) del candidato confirmado -- acá se exige que sea
    exactamente `persona.id`, no cualquier candidato real.

    Deliberadamente NO se apoya en el match heurístico crudo de teléfono/
    nombre que usa la capa de lectura de `/paquetes` para sugerencias de UI
    (`_personas_por_telefono`/`_personas_por_nombre`) -- ese match acepta
    falsos positivos como riesgo conocido (dos Personas con el mismo nombre,
    o un teléfono "prestado" del Principal de una unidad, issue 163) porque
    solo afecta a un link decorativo. Escribir datos reales de un paquete
    ajeno sobre esa misma heurística mezclaría los datos de dos personas
    reales distintas -- de ahí el guard de identidad real acá, sobre el
    padrón de Ocupantes REAL de la unidad de cada paquete, no un texto
    suelto."""
    resultado = []
    for paquete in paquetes_abiertos_de_persona(session, persona):
        candidatos = candidatos_correccion(session, paquete)
        confirmado_a = persona_confirmada_del_destinatario(paquete, candidatos)
        if confirmado_a is not None and str(confirmado_a) == str(persona.id):
            resultado.append(paquete)
    return resultado


def aplicar_snapshot_de_persona(
    session: Session, hermanos: list[Paquete], persona: Persona, actor: Usuario
) -> list[Paquete]:
    """Copia `recipient_name`/`recipient_phone`/la terna de apartamento
    ACTUALES de `persona` a cada paquete de `hermanos` -- LLAMAR DESPUÉS de
    que `persona`/su padrón ya reflejen el dato nuevo, con `hermanos` ya
    resuelto por `paquetes_hermanos_confirmados` ANTES de ese cambio (ver
    docstring del módulo).

    Devuelve los paquetes efectivamente modificados (los que ya tenían el
    dato al día se quedan afuera, sin tocar `corrected_at`) -- mismas
    columnas de auditoría que `corregir_apartamento`/`corregir_destinatario`
    (`corrected_at`/`corrected_by_usuario_id`), el esquema ya no distingue
    cuál de las 3 correcciones ocurrió."""
    apartamento_actual = (
        session.get(Apartamento, persona.apartamento_actual_id)
        if persona.apartamento_actual_id
        else None
    )

    propagados = []
    for paquete in hermanos:
        cambiado = False
        if persona.nombre and paquete.recipient_name != persona.nombre:
            paquete.recipient_name = persona.nombre
            cambiado = True
        if persona.telefono and paquete.recipient_phone != persona.telefono:
            paquete.recipient_phone = persona.telefono
            cambiado = True
        if apartamento_actual is not None and (
            paquete.snapshot_conjunto != apartamento_actual.conjunto
            or paquete.snapshot_torre != apartamento_actual.torre
            or paquete.snapshot_apartamento != apartamento_actual.apartamento
        ):
            paquete.snapshot_conjunto = apartamento_actual.conjunto
            paquete.snapshot_torre = apartamento_actual.torre
            paquete.snapshot_apartamento = apartamento_actual.apartamento
            cambiado = True

        if cambiado:
            paquete.corrected_at = _utcnow()
            paquete.corrected_by_usuario_id = actor.id
            propagados.append(paquete)

    if propagados:
        session.flush()
    return propagados


def sincronizar_snapshot_a_hermanos(
    session: Session, persona_id, actor: Usuario
) -> list[Paquete]:
    """Conveniencia que encadena `paquetes_hermanos_confirmados` +
    `aplicar_snapshot_de_persona` en un solo paso -- SOLO segura para
    callers donde ni `persona.nombre`/`persona.telefono` ni su padrón de
    Ocupante cambiaron todavía en este mismo request al momento de
    llamarla. Los 3 triggers de `/paquetes` cumplen esto: corrigen el
    PAQUETE que se está editando (su propio snapshot), nunca `nombre`/
    `telefono` de ninguna Persona -- lo único que sí puede cambiar de la
    Persona ahí es `apartamento_actual_id` (crear/mover un Ocupante), que no
    participa de la confirmación por nombre ni del `recipient_phone ==
    persona.telefono` que usa `paquetes_abiertos_de_persona` para encontrar
    hermanos, así que no hay ventana de invalidación.

    Los triggers de `/residentes` SÍ mutan `nombre`/`telefono` de la Persona
    directamente -- ahí este atajo puede fallar en silencio de 2 formas: (1)
    el candidato ya tiene el nombre/teléfono NUEVO al resolver, invalidando
    la confirmación de paquetes que lo tenían viejo (ver docstring del
    módulo); (2) si la Persona no es la Anunciante de un paquete (ej. un
    residente distinto de quien anunció), `paquetes_abiertos_de_persona` la
    encuentra SOLO por `recipient_phone == persona.telefono` -- si ese
    teléfono ya cambió, deja de encontrar ese paquete. Por eso `/residentes`
    llama las 2 funciones por separado, resolviendo ANTES de su propio
    cambio y aplicando DESPUÉS (ver docstring del módulo)."""
    persona = session.get(Persona, persona_id)
    if persona is None:
        return []
    hermanos = paquetes_hermanos_confirmados(session, persona)
    return aplicar_snapshot_de_persona(session, hermanos, persona, actor)
