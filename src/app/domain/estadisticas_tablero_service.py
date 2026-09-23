# -*- coding: utf-8 -*-
"""
Servicio de dominio del tablero de tarjetas de
`/administracion/estadisticas-cobro` (`.scratch/estadisticas-cobro-dashboard`,
rediseño que reemplaza al de listas de `.scratch/estadisticas-cobro-
interactivas` -- ese servicio, `cobro_service.estadisticas_cobro`, se retiró
en el ticket 17 de esta misma feature).

Tres zonas (ver la spec del feature):
  - **Panorama**: cifras FIJAS Hoy / Esta semana / Este mes, en HORA DE
    COLOMBIA -- ignoran siempre los filtros de "Periodo seleccionado".
  - **Ahora**: foto del momento (aún sin tarjetas -- llegan en los tickets
    09-10 de esta misma feature).
  - **Periodo seleccionado**: responde a los filtros (atajo de fecha, Tipo,
    Cobrado/Anulado); sin ningún atajo activo, TODOS los datos existentes
    (issue 364, ya vigente antes de este rediseño).

`calcular_tablero` recibe el instante "ahora" como parámetro EXPLÍCITO --
nunca lee el reloj del sistema por su cuenta -- para que las pruebas puedan
fijarlo. Esto es crítico acá: "Hoy" se resuelve en hora de Colombia (UTC-5),
no en UTC -- a las 19:00 UTC el día local ya cambió, y una tarjeta fija de
"Hoy" que siguiera contando en UTC estaría mostrando el día equivocado
durante 5 horas todas las noches.
"""

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from .cobro import Cobro
from .cobro_service import _HORAS_GRACIA_BODEGAJE, calcular_cobro, obtener_tarifas_vigentes
from .paquete import CondicionPaquete, EstadoPaquete, Paquete, TipoPaquete
from .persona import Persona
from .proveedor_config_service import obtener_costo_promedio_sms
from .registro_sms import TipoRegistroSms
from .registro_sms_service import contar_envios, fecha_primer_registro
from .saldo_contra_entrega import MovimientoSaldoContraEntrega
from .tarifa_cobro import TarifaCobro
from .usuario import Usuario
from .zona_horaria import ZONA_HORARIA_APP

_PROVEEDOR_SMS_CON_COSTO = "AWS_SNS"


@dataclass(frozen=True)
class FiltrosTablero:
    """Filtros de la zona "Periodo seleccionado" -- Panorama y Ahora los
    ignoran siempre (decisiones D1/D9/D11 del grilling: son fijos / una foto
    del momento, nunca dependen de lo que el admin filtre). `rango=None`, o
    una clave que no matchea ningún atajo conocido, significa "todos los
    datos existentes" (issue 364)."""

    rango: str | None = None
    tipo: TipoPaquete | None = None
    anulado: bool | None = None


@dataclass(frozen=True)
class TrioHoySemanaMes:
    """Una métrica de Panorama: su valor Hoy, Esta semana y Este mes -- las
    tres calculadas en hora de Colombia, ninguna depende de `FiltrosTablero`."""

    hoy: int
    semana: int
    mes: int


@dataclass(frozen=True)
class TrioPromedioHoras:
    """Como `TrioHoySemanaMes`, pero para un promedio en HORAS -- cada
    campo es `None` sin ningún paquete que califique en esa ventana (evita
    un "0 h" engañoso, se pinta como "—")."""

    hoy: float | None
    semana: float | None
    mes: float | None


@dataclass(frozen=True)
class TiemposPromedio:
    """Las tres filas de la tarjeta "Tiempos promedio" de Panorama (ticket
    07), cada una con su propio trío Hoy/Semana/Mes en horas:

    - `anuncio_recepcion`: recibido − anunciado, de los RECIBIDOS en la
      ventana.
    - `permanencia_bodega`: entregado − recibido, de los ENTREGADOS en la
      ventana -- de TODOS los entregados, cobrado bodegaje o no.
    - `bodegaje_cobrado`: la misma resta que `permanencia_bodega`, pero
      solo de los entregados cuyo cobro sí llevó bloques de bodegaje
      (`Cobro.bloques_bodegaje > 0`) -- misma métrica que ya existía en la
      pantalla anterior (`cobro_service.estadisticas_cobro`, retirada en el
      ticket 17), reubicada acá."""

    anuncio_recepcion: TrioPromedioHoras
    permanencia_bodega: TrioPromedioHoras
    bodegaje_cobrado: TrioPromedioHoras


@dataclass(frozen=True)
class Tendencia:
    """Tendencia de un trío de Panorama (ticket 08): la variación
    porcentual de cada columna contra el MISMO TRAMO del periodo anterior
    -- Hoy contra ayer hasta esta misma hora, Semana contra la semana
    anterior hasta el mismo día de la semana y hora, Mes contra el mes
    anterior hasta el mismo día del mes y hora (recortado a su último día
    si es más corto) -- nunca el tramo COMPLETO anterior, para no comparar
    un día/semana/mes entero contra lo que va corrido de hoy (issue que
    motivó este ticket). `None` cuando el tramo anterior valió 0 (evita un
    porcentaje infinito o un "0%" engañoso -- no se muestra nada).
    `serie_7_dias` es la cifra diaria de los últimos 7 días (hoy incluido,
    en hora de Colombia), del más viejo al más reciente, para el
    minigráfico -- el color (bien/mal) según suba o baje es una decisión
    de presentación, no de este dataclass (depende de la métrica: en
    Ingresos/Entregados subir es bueno, en Cancelados es al revés)."""

    variacion_hoy: float | None
    variacion_semana: float | None
    variacion_mes: float | None
    serie_7_dias: tuple[int, ...]


@dataclass(frozen=True)
class TrioCosto:
    """Como `TrioHoySemanaMes`, pero para un costo ESTIMADO en COP (ticket
    15) -- siempre los tres juntos: cuando el costo promedio SÍ está
    configurado, las tres columnas son calculables a la vez (no hay una
    ventana "sin dato" independiente de las otras, a diferencia de
    `TrioPromedioHoras`)."""

    hoy: float
    semana: float
    mes: float


@dataclass(frozen=True)
class SmsPanorama:
    """Trío Hoy/Semana/Mes de SMS enviados por AWS SNS -- Panorama, ticket
    14 (cantidades) + 15 (costo estimado) + 16 (tendencia).
    `enviados` es avisos de paquete + códigos de acceso JUNTOS (`RegistroSms.
    proveedor` ya resolvió cuál proveedor entregó de verdad, sin importar
    si hubo failover -- ver `notificacion_service`/`app.web.otp`, tickets
    11-12). `costo_estimado` es `enviados` multiplicado por el costo
    promedio CONFIGURADO HOY (`proveedor_config_service.
    obtener_costo_promedio_sms`) -- nunca un precio histórico: cambiar el
    costo en Proveedores y recargar el tablero recalcula esta cifra
    también para periodos pasados. `None` (el trío COMPLETO, no por
    columna) cuando el ADMIN todavía no configuró ningún costo -- se pinta
    como "—" con un enlace a Proveedores, nunca un "$0" engañoso.

    `tendencia` usa el MISMO cálculo de variación que Ingresos/Entregados/
    Cancelados (ticket 08) -- la plantilla la pinta en un color NEUTRO
    (ni verde ni rojo: más o menos SMS no es "bueno" ni "malo" por sí
    solo), a diferencia de esas tres. Su `serie_7_dias` nunca inventa
    ceros para un día ANTERIOR a `registro_desde` -- esos días simplemente
    no entran a la serie (ver `_tendencia_sms`).

    `registro_desde` es la fecha del primer envío que exista en el
    registro -- `None` sin ninguno todavía -- para dejar claro en pantalla
    que esto NO es un histórico completo, arrancó junto con esta feature."""

    enviados: TrioHoySemanaMes
    costo_estimado: TrioCosto | None
    tendencia: Tendencia
    registro_desde: datetime | None


@dataclass(frozen=True)
class Panorama:
    """Zona fija del tablero. Se completa ticket a ticket (01: Ingresos;
    06: Entregados/Cancelados; 07: Tiempos promedio; 08: tendencia; 14:
    SMS). `entregados`/`cancelados` cuentan por la fecha de SU PROPIO
    evento (entrega/cancelación) -- tarjetas separadas, nunca sumadas en un
    solo "procesados" (spec.md, ticket 06). `tendencia_*` acompaña a
    Ingresos/Entregados/Cancelados (ticket 08) -- Tiempos promedio y SMS
    (por ahora) no llevan; el ticket 16 le agrega tendencia a SMS."""

    ingresos: TrioHoySemanaMes
    entregados: TrioHoySemanaMes
    cancelados: TrioHoySemanaMes
    tiempos: TiemposPromedio
    tendencia_ingresos: Tendencia
    tendencia_entregados: Tendencia
    tendencia_cancelados: Tendencia
    sms_aws: SmsPanorama


@dataclass(frozen=True)
class Paquetes:
    """Categoría "Paquetes" de Periodo seleccionado -- cada cifra por la
    fecha de SU PROPIO evento dentro del periodo (`total` es la unión: con
    cualquier movimiento -- anuncio, recepción, entrega o cancelación --, no
    solo anunciados). `recibidos`/`entregados` ya vienen filtrados por Tipo
    si `FiltrosTablero.tipo` viene seteado (matriz de "no aplica": Tipo SÍ
    acota estas dos, NO acota total/anunciados/cancelados)."""

    total: int
    anunciados: int
    recibidos: int
    entregados: int
    cancelados: int


@dataclass(frozen=True)
class RitmoMetrica:
    """Promedio de una cifra por día, por semana y por mes dentro del
    periodo -- `total del periodo / días del periodo * (1, 7, 30)`."""

    por_dia: float
    por_semana: float
    por_mes: float


@dataclass(frozen=True)
class RitmoYTasas:
    """`ritmo_recibidos`/`ritmo_entregados` respetan el filtro de Tipo (igual
    que `Paquetes.recibidos`/`.entregados`); `ritmo_anunciados` y las 2 tasas
    NO (matriz de "no aplica") -- por eso las tasas SIEMPRE se calculan sobre
    entregados/cancelados sin filtrar por Tipo, aunque `Paquetes.entregados`
    de al lado sí esté filtrado. `tasa_entrega`/`tasa_cancelacion` ya vienen
    en escala de PORCENTAJE (0-100, no 0-1); `None` sin ningún paquete
    cerrado en el periodo (evita un "0%" engañoso)."""

    anunciados: RitmoMetrica
    recibidos: RitmoMetrica
    entregados: RitmoMetrica
    tasa_entrega: float | None
    tasa_cancelacion: float | None


@dataclass(frozen=True)
class Recaudo:
    """Categoría "Recaudo" de Periodo seleccionado. `promedio_por_paquete`,
    los 2 porcentajes, `tasa_anulacion`, `cobro_mas_alto` y
    `dias_bodega_del_mas_alto` son `None` sin ningún cobro en el periodo --
    evita una división por cero o un "$0" engañoso, se pintan como "—".

    `exonerado_anulaciones` y `dejado_de_cobrar_primera_entrega` son
    ESTIMACIONES con las tarifas de SERVICIO vigentes HOY según el Tipo de
    cada paquete anulado/exento -- el `Cobro` solo guarda el resultado
    (servicio en 0), nunca lo que se hubiera cobrado, así que no hay forma
    de saber el monto exacto que tenía cada cobro pasado antes de anularse."""

    total_ingresos: int
    promedio_por_paquete: float | None
    recaudado_bodegaje: int
    porcentaje_bodegaje: float | None
    recaudado_servicio: int
    porcentaje_servicio: float | None
    exonerado_anulaciones: int
    cantidad_anulaciones: int
    tasa_anulacion: float | None
    exenciones_primera_entrega: int
    dejado_de_cobrar_primera_entrega: int
    cobro_mas_alto: int | None
    dias_bodega_del_mas_alto: int | None


@dataclass(frozen=True)
class ClienteDestacado:
    """Un cliente puntual (por su Teléfono de destinatario) con su NOMBRE --
    el de su Persona si existe, o si no el congelado en el snapshot de su
    paquete más reciente del periodo (nunca el teléfono, spec.md: "mostrando
    siempre el NOMBRE del cliente") -- su Apartamento del snapshot MÁS
    RECIENTE (ADR-0001: nunca la unidad actual) y la cifra que lo hizo
    destacar (cantidad de paquetes, o monto gastado, según la tarjeta)."""

    nombre: str
    apartamento: str | None
    valor: int


@dataclass(frozen=True)
class Clientes:
    """Categoría "Clientes" de Periodo seleccionado -- "cliente" = una
    Persona identificada por el Teléfono del destinatario (`recipient_
    phone`); los paquetes de "nombre sin teléfono" (sin Persona detrás)
    nunca cuentan acá."""

    activos: int
    nuevos: int
    recurrentes: int
    con_mas_paquetes: ClienteDestacado | None
    con_mayor_gasto: ClienteDestacado | None


@dataclass(frozen=True)
class OperacionYCalidad:
    """Categoría "Operación y calidad" de Periodo seleccionado. Todos los
    porcentajes son `None` sin ningún paquete que califique (evita un "0%"
    engañoso). `operador_top`/`dia_mas_activo` son `None` sin ninguna
    entrega en el periodo. Empates: operador y día, por nombre/orden
    lunes→domingo respectivamente -- deterministas entre cargas."""

    operador_top_nombre: str | None
    operador_top_cantidad: int
    dia_mas_activo: str | None
    dia_mas_activo_porcentaje: float | None
    hora_pico: str | None
    porcentaje_dentro_de_48h: float | None
    porcentaje_extra_dimensionados: float | None
    porcentaje_mal_estado: float | None


@dataclass(frozen=True)
class SmsPeriodo:
    """Categoría "SMS del periodo" -- ticket 14 (cantidades) + 15 (costo).
    `enviados_aws` es avisos + códigos de acceso JUNTOS (`avisos_aws`/
    `codigos_aws` alimentan el desglose "X avisos · Y códigos de acceso");
    un SMS entregado tras fallar antes por otro proveedor cuenta para el
    que SÍ lo entregó, nunca para el primero de la cadena que se intentó
    (`RegistroSms.proveedor`, ya resuelto en los tickets 11-12). `fallidos`
    son los que NINGÚN proveedor entregó -- de cualquier tipo, no solo AWS.

    `costo_estimado` = `enviados_aws` × el costo promedio CONFIGURADO HOY
    -- `None` sin costo configurado. `costo_por_paquete` = `costo_estimado`
    ÷ el MISMO `Paquetes.total` del ticket 02 (paquetes con movimiento en
    el periodo) -- `None` sin costo configurado O sin ningún paquete en el
    periodo (evita una división por cero).

    Matriz de "no aplica": NI Tipo NI Cobrado/Anulado acotan ninguna
    tarjeta de esta categoría -- un envío SMS no tiene Tipo de paquete ni
    estado de cobro. `registro_desde` es la misma fecha que `Panorama.
    SmsPanorama.registro_desde` (fuente única: `registro_sms_service.
    fecha_primer_registro`)."""

    enviados_aws: int
    avisos_aws: int
    codigos_aws: int
    fallidos: int
    costo_estimado: float | None
    costo_por_paquete: float | None
    registro_desde: datetime | None


@dataclass(frozen=True)
class PeriodoSeleccionado:
    """Zona que responde a `FiltrosTablero`. `rango_activo` es la clave del
    atajo tal como quedó resuelta (`None` si no venía ninguno, o si el que
    vino no matchea ningún atajo conocido) -- lista para pintar las píldoras
    y el chip de filtros activos sin que la plantilla tenga que repetir la
    lógica de qué claves son válidas."""

    rango_activo: str | None
    recaudo: Recaudo
    paquetes: Paquetes
    ritmo: RitmoYTasas
    clientes: Clientes
    operacion: OperacionYCalidad
    sms: SmsPeriodo


@dataclass(frozen=True)
class PaqueteMasAntiguo:
    """El Recibido con la recepción más antigua, para la tarjeta "Paquete
    más antiguo" de Ahora (ticket 09) -- `apartamento` es del SNAPSHOT
    (ADR-0001), `access_code` identifica al paquete sin exponer datos del
    destinatario."""

    dias_en_bodega: int
    apartamento: str | None
    access_code: str


@dataclass(frozen=True)
class Ahora:
    """Zona "Ahora": la FOTO DEL MOMENTO -- ignora siempre `FiltrosTablero`,
    igual que Panorama, pero a diferencia de esa zona no es una serie
    (Hoy/Semana/Mes): es un instante.

    `en_gracia`/`con_bodegaje_corriendo` particionan los MISMOS `en_bodega`
    sin solaparse (el corte es el mismo de `cobro_service._HORAS_GRACIA_
    BODEGAJE`); `mas_de_7_dias`/`abandonados` son umbrales ACUMULATIVOS
    (un paquete de 40 días cuenta en ambos), no una partición.

    `por_cobrar_en_bodega` (ticket 10) es una ESTIMACIÓN: la suma de lo que
    se cobraría SI cada Recibido actual se entregara justo ahora --
    `cobro_service.calcular_cobro`, la misma aritmética (y la misma
    exención de primera entrega) que ya usa el modal Entregar de
    `/paquetes`, nunca reimplementada aparte; cambia solo con el paso del
    tiempo (el bodegaje sigue corriendo), nunca con un cobro real hasta que
    alguien de verdad entrega. `deuda_contra_entrega`/`personas_con_deuda`
    son la suma de los saldos NEGATIVOS de `MovimientoSaldoContraEntrega`
    agrupados por Persona -- un saldo A FAVOR no compensa la deuda de otra
    Persona, así que nunca se netean entre sí."""

    pendientes: int
    pendientes_anunciados: int
    pendientes_recibidos: int
    en_bodega: int
    en_gracia: int
    con_bodegaje_corriendo: int
    mas_de_7_dias: int
    abandonados: int
    paquete_mas_antiguo: PaqueteMasAntiguo | None
    anuncios_sin_llegar: int
    clientes_registrados: int
    por_cobrar_en_bodega: int
    deuda_contra_entrega: int
    personas_con_deuda: int


@dataclass(frozen=True)
class TableroEstadisticasCobro:
    panorama: Panorama
    ahora: Ahora
    periodo: PeriodoSeleccionado


# --- Atajos de fecha (issue 364; relocados acá desde `web/routes/admin.py`
# en el ticket 01: "hoy" pasa de ser "lo que manda el navegador" a ser "lo
# que dice el reloj del servidor, en hora de Colombia" -- lógica de dominio,
# no de la capa web). ---------------------------------------------------- #


def _restar_meses(d: date, meses: int) -> date:
    """`d` menos `meses` meses calendario; si ese mes no tiene el mismo día
    (ej. 31-may menos 3 meses -> febrero) cae en el último día de ese mes."""
    anio, mes = divmod(d.year * 12 + (d.month - 1) - meses, 12)
    mes += 1
    return date(anio, mes, min(d.day, calendar.monthrange(anio, mes)[1]))


# Meses hacia atrás de las ventanas móviles: "3 últimos meses", "Semestre"
# (= últimos 6 meses), "Último año" (= últimos 12 meses).
_MESES_RANGO_PERIODO = {"tres_meses": 3, "semestre": 6, "anio": 12}


def _rango_por_atajo(clave: str | None, hoy: date) -> tuple[date, date] | None:
    """Los días (desde, hasta), ambos inclusive y LOCALES (hora de
    Colombia), de un atajo de fecha, o `None` si `clave` no es un atajo
    conocido -- sin rango, todos los datos.

    "Esta semana" empieza el lunes; "Este mes", el día 1. Las ventanas de
    varios meses son móviles y terminan hoy: empiezan el día SIGUIENTE a la
    misma fecha N meses atrás (hoy 20-sep, 3 meses -> desde 21-jun), así
    abarcan exactamente N meses."""
    if clave == "hoy":
        return hoy, hoy
    if clave == "ayer":
        ayer = hoy - timedelta(days=1)
        return ayer, ayer
    if clave == "semana":
        return hoy - timedelta(days=hoy.weekday()), hoy
    if clave == "mes":
        return hoy.replace(day=1), hoy
    meses = _MESES_RANGO_PERIODO.get(clave)
    if meses is not None:
        return _restar_meses(hoy, meses) + timedelta(days=1), hoy
    return None


def _limites_utc_de_dias_locales(desde: date, hasta: date) -> tuple[datetime, datetime]:
    """Un rango de días LOCALES (hora de Colombia), ambos inclusive, a
    límites UTC listos para filtrar una columna `DateTime(timezone=True)`
    como `Cobro.cobrado_en`."""
    inicio = datetime.combine(desde, time.min, tzinfo=ZONA_HORARIA_APP)
    fin = datetime.combine(hasta, time.max, tzinfo=ZONA_HORARIA_APP)
    return inicio.astimezone(timezone.utc), fin.astimezone(timezone.utc)


# --- Panorama -------------------------------------------------------------- #


def _suma_ingresos_entre(session: Session, desde_utc: datetime, hasta_utc: datetime) -> int:
    total = (
        session.query(func.coalesce(func.sum(Cobro.monto_total), 0))
        .filter(Cobro.cobrado_en >= desde_utc, Cobro.cobrado_en <= hasta_utc)
        .scalar()
    )
    return int(total)


def _contar_paquetes_entre(session: Session, columna, desde_utc: datetime, hasta_utc: datetime) -> int:
    return int(
        session.query(func.count(Paquete.id))
        .filter(columna.isnot(None), columna >= desde_utc, columna <= hasta_utc)
        .scalar()
    )


def _promedio_horas_entre(
    session: Session,
    columna_ventana,
    columna_inicio,
    columna_fin,
    desde_utc: datetime,
    hasta_utc: datetime,
    solo_con_bodegaje_cobrado: bool = False,
) -> float | None:
    """Promedio en HORAS de `columna_fin - columna_inicio`, de los
    `Paquete` cuya `columna_ventana` cae en `[desde_utc, hasta_utc]` --
    `None` sin ninguno que califique. `solo_con_bodegaje_cobrado` une con
    `Cobro` y exige `bloques_bodegaje > 0` (fila "Bodegaje cobrado")."""
    query = session.query(
        func.avg(func.extract("epoch", columna_fin - columna_inicio) / 3600.0)
    ).filter(
        columna_ventana.isnot(None),
        columna_ventana >= desde_utc,
        columna_ventana <= hasta_utc,
        columna_inicio.isnot(None),
        columna_fin.isnot(None),
    )
    if solo_con_bodegaje_cobrado:
        query = query.join(Cobro, Cobro.paquete_id == Paquete.id).filter(Cobro.bloques_bodegaje > 0)
    valor = query.scalar()
    return float(valor) if valor is not None else None


def _calcular_tiempos_promedio(
    session: Session,
    desde_hoy: datetime, hasta_hoy: datetime,
    desde_semana: datetime, hasta_semana: datetime,
    desde_mes: datetime, hasta_mes: datetime,
) -> TiemposPromedio:
    def _trio_horas(columna_ventana, columna_inicio, columna_fin, solo_con_bodegaje_cobrado=False) -> TrioPromedioHoras:
        return TrioPromedioHoras(
            hoy=_promedio_horas_entre(
                session, columna_ventana, columna_inicio, columna_fin, desde_hoy, hasta_hoy, solo_con_bodegaje_cobrado
            ),
            semana=_promedio_horas_entre(
                session, columna_ventana, columna_inicio, columna_fin, desde_semana, hasta_semana, solo_con_bodegaje_cobrado
            ),
            mes=_promedio_horas_entre(
                session, columna_ventana, columna_inicio, columna_fin, desde_mes, hasta_mes, solo_con_bodegaje_cobrado
            ),
        )

    return TiemposPromedio(
        anuncio_recepcion=_trio_horas(Paquete.received_at, Paquete.announced_at, Paquete.received_at),
        permanencia_bodega=_trio_horas(Paquete.delivered_at, Paquete.received_at, Paquete.delivered_at),
        bodegaje_cobrado=_trio_horas(
            Paquete.delivered_at, Paquete.received_at, Paquete.delivered_at, solo_con_bodegaje_cobrado=True
        ),
    )


def _tramo_anterior_utc(hoy_local: date, ahora_local: datetime, ventana: str) -> tuple[datetime, datetime]:
    """Límites UTC del "mismo tramo del periodo anterior" (ticket 08) para
    `ventana` ("hoy"/"semana"/"mes") -- el mismo punto de corte de HORA que
    `ahora_local`, nunca el tramo completo anterior. `mes` reusa
    `_restar_meses` a propósito: ya resuelve "recortado a su último día"."""
    if ventana == "hoy":
        fecha_cutoff = hoy_local - timedelta(days=1)
        fecha_inicio = fecha_cutoff
    elif ventana == "semana":
        inicio_actual = hoy_local - timedelta(days=hoy_local.weekday())
        fecha_cutoff = hoy_local - timedelta(days=7)
        fecha_inicio = inicio_actual - timedelta(days=7)
    else:
        fecha_cutoff = _restar_meses(hoy_local, 1)
        fecha_inicio = _restar_meses(hoy_local.replace(day=1), 1)
    cutoff_local = datetime.combine(fecha_cutoff, ahora_local.time(), tzinfo=ZONA_HORARIA_APP)
    inicio_local = datetime.combine(fecha_inicio, time.min, tzinfo=ZONA_HORARIA_APP)
    return inicio_local.astimezone(timezone.utc), cutoff_local.astimezone(timezone.utc)


def _variacion_pct(actual: int, anterior: int) -> float | None:
    """`None` cuando el tramo anterior valió 0 -- ni infinito ni un "0%"
    engañoso, no se muestra ningún porcentaje (spec.md, ticket 08)."""
    if anterior == 0:
        return None
    return (actual - anterior) / anterior * 100


def _ultimos_7_dias_locales(hoy_local: date) -> list[date]:
    return [hoy_local - timedelta(days=i) for i in range(6, -1, -1)]


def _tendencia_ingresos(session: Session, hoy_local: date, ahora_local: datetime, trio: TrioHoySemanaMes) -> Tendencia:
    variacion_hoy = _variacion_pct(
        trio.hoy, _suma_ingresos_entre(session, *_tramo_anterior_utc(hoy_local, ahora_local, "hoy"))
    )
    variacion_semana = _variacion_pct(
        trio.semana, _suma_ingresos_entre(session, *_tramo_anterior_utc(hoy_local, ahora_local, "semana"))
    )
    variacion_mes = _variacion_pct(
        trio.mes, _suma_ingresos_entre(session, *_tramo_anterior_utc(hoy_local, ahora_local, "mes"))
    )
    serie = tuple(
        _suma_ingresos_entre(session, *_limites_utc_de_dias_locales(dia, dia))
        for dia in _ultimos_7_dias_locales(hoy_local)
    )
    return Tendencia(
        variacion_hoy=variacion_hoy, variacion_semana=variacion_semana, variacion_mes=variacion_mes,
        serie_7_dias=serie,
    )


def _tendencia_paquetes(
    session: Session, columna, hoy_local: date, ahora_local: datetime, trio: TrioHoySemanaMes
) -> Tendencia:
    variacion_hoy = _variacion_pct(
        trio.hoy, _contar_paquetes_entre(session, columna, *_tramo_anterior_utc(hoy_local, ahora_local, "hoy"))
    )
    variacion_semana = _variacion_pct(
        trio.semana, _contar_paquetes_entre(session, columna, *_tramo_anterior_utc(hoy_local, ahora_local, "semana"))
    )
    variacion_mes = _variacion_pct(
        trio.mes, _contar_paquetes_entre(session, columna, *_tramo_anterior_utc(hoy_local, ahora_local, "mes"))
    )
    serie = tuple(
        _contar_paquetes_entre(session, columna, *_limites_utc_de_dias_locales(dia, dia))
        for dia in _ultimos_7_dias_locales(hoy_local)
    )
    return Tendencia(
        variacion_hoy=variacion_hoy, variacion_semana=variacion_semana, variacion_mes=variacion_mes,
        serie_7_dias=serie,
    )


def _calcular_panorama(session: Session, hoy_local: date, ahora_local: datetime) -> Panorama:
    desde_hoy, hasta_hoy = _limites_utc_de_dias_locales(hoy_local, hoy_local)
    desde_semana, hasta_semana = _limites_utc_de_dias_locales(
        hoy_local - timedelta(days=hoy_local.weekday()), hoy_local
    )
    desde_mes, hasta_mes = _limites_utc_de_dias_locales(hoy_local.replace(day=1), hoy_local)

    def _trio_paquetes(columna) -> TrioHoySemanaMes:
        return TrioHoySemanaMes(
            hoy=_contar_paquetes_entre(session, columna, desde_hoy, hasta_hoy),
            semana=_contar_paquetes_entre(session, columna, desde_semana, hasta_semana),
            mes=_contar_paquetes_entre(session, columna, desde_mes, hasta_mes),
        )

    ingresos = TrioHoySemanaMes(
        hoy=_suma_ingresos_entre(session, desde_hoy, hasta_hoy),
        semana=_suma_ingresos_entre(session, desde_semana, hasta_semana),
        mes=_suma_ingresos_entre(session, desde_mes, hasta_mes),
    )
    entregados = _trio_paquetes(Paquete.delivered_at)
    cancelados = _trio_paquetes(Paquete.cancelled_at)
    tiempos = _calcular_tiempos_promedio(
        session, desde_hoy, hasta_hoy, desde_semana, hasta_semana, desde_mes, hasta_mes
    )
    return Panorama(
        ingresos=ingresos,
        entregados=entregados,
        cancelados=cancelados,
        tiempos=tiempos,
        tendencia_ingresos=_tendencia_ingresos(session, hoy_local, ahora_local, ingresos),
        tendencia_entregados=_tendencia_paquetes(session, Paquete.delivered_at, hoy_local, ahora_local, entregados),
        tendencia_cancelados=_tendencia_paquetes(session, Paquete.cancelled_at, hoy_local, ahora_local, cancelados),
        sms_aws=_calcular_sms_panorama(
            session, hoy_local, ahora_local, desde_hoy, hasta_hoy, desde_semana, hasta_semana, desde_mes, hasta_mes
        ),
    )


def _tendencia_sms(
    session: Session,
    hoy_local: date,
    ahora_local: datetime,
    trio: TrioHoySemanaMes,
    registro_desde: datetime | None,
) -> Tendencia:
    """Como `_tendencia_paquetes`, pero para SMS -- MISMO cálculo exacto de
    variación (ticket 16: "el mismo cálculo exacto que ya usan Ingresos/
    Entregados/Cancelados"). `serie_7_dias` es la única diferencia: nunca
    inventa ceros para un día ANTERIOR a que el registro de SMS existiera
    (`registro_desde`) -- esos días simplemente no entran a la serie, en
    vez de aparecer como "0 enviados" (que sería un dato falso: no es que
    ese día no se enviara nada, es que ese día no se registraba todavía).
    Sin ningún registro aún, la serie queda vacía."""

    def _contar(desde: datetime, hasta: datetime) -> int:
        return contar_envios(session, proveedor=_PROVEEDOR_SMS_CON_COSTO, desde=desde, hasta=hasta)

    variacion_hoy = _variacion_pct(trio.hoy, _contar(*_tramo_anterior_utc(hoy_local, ahora_local, "hoy")))
    variacion_semana = _variacion_pct(
        trio.semana, _contar(*_tramo_anterior_utc(hoy_local, ahora_local, "semana"))
    )
    variacion_mes = _variacion_pct(trio.mes, _contar(*_tramo_anterior_utc(hoy_local, ahora_local, "mes")))

    dias = _ultimos_7_dias_locales(hoy_local)
    if registro_desde is not None:
        primero_local = registro_desde.astimezone(ZONA_HORARIA_APP).date()
        dias = [dia for dia in dias if dia >= primero_local]
    else:
        dias = []
    serie = tuple(_contar(*_limites_utc_de_dias_locales(dia, dia)) for dia in dias)

    return Tendencia(
        variacion_hoy=variacion_hoy, variacion_semana=variacion_semana, variacion_mes=variacion_mes,
        serie_7_dias=serie,
    )


def _calcular_sms_panorama(
    session: Session,
    hoy_local: date,
    ahora_local: datetime,
    desde_hoy: datetime, hasta_hoy: datetime,
    desde_semana: datetime, hasta_semana: datetime,
    desde_mes: datetime, hasta_mes: datetime,
) -> SmsPanorama:
    def _contar(desde: datetime, hasta: datetime) -> int:
        return contar_envios(session, proveedor=_PROVEEDOR_SMS_CON_COSTO, desde=desde, hasta=hasta)

    enviados = TrioHoySemanaMes(
        hoy=_contar(desde_hoy, hasta_hoy),
        semana=_contar(desde_semana, hasta_semana),
        mes=_contar(desde_mes, hasta_mes),
    )
    costo_unitario = obtener_costo_promedio_sms(session, _PROVEEDOR_SMS_CON_COSTO)
    costo_estimado = None
    if costo_unitario is not None:
        costo_unitario_f = float(costo_unitario)
        costo_estimado = TrioCosto(
            hoy=enviados.hoy * costo_unitario_f,
            semana=enviados.semana * costo_unitario_f,
            mes=enviados.mes * costo_unitario_f,
        )
    registro_desde = fecha_primer_registro(session)
    return SmsPanorama(
        enviados=enviados,
        costo_estimado=costo_estimado,
        tendencia=_tendencia_sms(session, hoy_local, ahora_local, enviados, registro_desde),
        registro_desde=registro_desde,
    )


# --- Ahora: foto del momento (ticket 09; el 10 le agrega dinero) ----------- #

# Umbrales de antigüedad en bodega (D9 del grilling, spec.md): "más de 7
# días" y "abandonados" (más de 30) son alertas ACUMULATIVAS, no una
# partición -- un paquete de 40 días cuenta en ambas. "Anuncios que nunca
# llegaron" reusa el mismo umbral de 7 días, pero sobre `announced_at`.
_DIAS_MAS_DE_7_EN_BODEGA = 7
_DIAS_ABANDONADO_EN_BODEGA = 30
_DIAS_ANUNCIO_SIN_LLEGAR = 7


def _contar_estado(session: Session, estado: EstadoPaquete, *condiciones) -> int:
    return int(
        session.query(func.count(Paquete.id)).filter(Paquete.estado == estado, *condiciones).scalar()
    )


def _paquete_mas_antiguo_en_bodega(session: Session, ahora: datetime) -> PaqueteMasAntiguo | None:
    fila = (
        session.query(Paquete.received_at, Paquete.snapshot_torre, Paquete.snapshot_apartamento, Paquete.access_code)
        .filter(Paquete.estado == EstadoPaquete.RECIBIDO)
        .order_by(Paquete.received_at.asc())
        .first()
    )
    if fila is None:
        return None
    recibido_en, torre, apto, codigo = fila
    dias = int((ahora - recibido_en).total_seconds() // 86400)
    partes = [p for p in (torre, apto) if p]
    return PaqueteMasAntiguo(
        dias_en_bodega=dias, apartamento=" ".join(partes) if partes else None, access_code=codigo
    )


def _por_cobrar_en_bodega(session: Session, ahora: datetime) -> int:
    """La suma de lo que se cobraría SI cada Recibido actual se entregara
    justo ahora -- reusa `calcular_cobro` (misma exención de primera
    entrega por teléfono, mismo criterio batch "un puñado fijo de
    consultas" que ya usa `packages.py::_listar` para el modal Entregar,
    en vez de una consulta de "primera entrega" por paquete)."""
    recibidos = session.query(Paquete).filter(Paquete.estado == EstadoPaquete.RECIBIDO).all()
    if not recibidos:
        return 0
    telefonos_recibido = {p.recipient_phone for p in recibidos if p.recipient_phone}
    telefonos_con_entrega_previa = set()
    if telefonos_recibido:
        telefonos_con_entrega_previa = {
            fila[0]
            for fila in session.query(Paquete.recipient_phone)
            .filter(
                Paquete.recipient_phone.in_(telefonos_recibido), Paquete.estado == EstadoPaquete.ENTREGADO
            )
            .distinct()
            .all()
        }
    tarifas = obtener_tarifas_vigentes(session)
    return sum(
        calcular_cobro(
            p, tarifas, ahora, bool(p.recipient_phone and p.recipient_phone not in telefonos_con_entrega_previa)
        ).monto_total
        for p in recibidos
    )


def _deuda_contra_entrega(session: Session) -> tuple[int, int]:
    """(monto adeudado total, cantidad de Personas con saldo negativo) --
    una sola consulta agregada por Persona (`HAVING SUM(...) < 0`); un
    saldo A FAVOR nunca compensa la deuda de otra Persona, así que no se
    netean entre sí (spec.md, ticket 10)."""
    saldos_negativos = [
        int(saldo)
        for (saldo,) in session.query(func.sum(MovimientoSaldoContraEntrega.monto))
        .group_by(MovimientoSaldoContraEntrega.persona_id)
        .having(func.sum(MovimientoSaldoContraEntrega.monto) < 0)
        .all()
    ]
    return sum(saldos_negativos), len(saldos_negativos)


def _calcular_ahora(session: Session, ahora: datetime) -> Ahora:
    anunciados = _contar_estado(session, EstadoPaquete.ANUNCIADO)
    recibidos = _contar_estado(session, EstadoPaquete.RECIBIDO)

    limite_gracia = ahora - timedelta(hours=_HORAS_GRACIA_BODEGAJE)
    limite_7_dias = ahora - timedelta(days=_DIAS_MAS_DE_7_EN_BODEGA)
    limite_30_dias = ahora - timedelta(days=_DIAS_ABANDONADO_EN_BODEGA)
    limite_anuncio_sin_llegar = ahora - timedelta(days=_DIAS_ANUNCIO_SIN_LLEGAR)
    deuda_total, personas_con_deuda = _deuda_contra_entrega(session)

    return Ahora(
        pendientes=anunciados + recibidos,
        pendientes_anunciados=anunciados,
        pendientes_recibidos=recibidos,
        en_bodega=recibidos,
        en_gracia=_contar_estado(session, EstadoPaquete.RECIBIDO, Paquete.received_at >= limite_gracia),
        con_bodegaje_corriendo=_contar_estado(
            session, EstadoPaquete.RECIBIDO, Paquete.received_at < limite_gracia
        ),
        mas_de_7_dias=_contar_estado(session, EstadoPaquete.RECIBIDO, Paquete.received_at < limite_7_dias),
        abandonados=_contar_estado(session, EstadoPaquete.RECIBIDO, Paquete.received_at < limite_30_dias),
        paquete_mas_antiguo=_paquete_mas_antiguo_en_bodega(session, ahora),
        anuncios_sin_llegar=_contar_estado(
            session, EstadoPaquete.ANUNCIADO, Paquete.announced_at < limite_anuncio_sin_llegar
        ),
        clientes_registrados=int(
            session.query(func.count(Persona.id))
            .filter(Persona.eliminado_en.is_(None), Persona.baja_administrativa_en.is_(None))
            .scalar()
        ),
        por_cobrar_en_bodega=_por_cobrar_en_bodega(session, ahora),
        deuda_contra_entrega=deuda_total,
        personas_con_deuda=personas_con_deuda,
    )


# --- Periodo seleccionado --------------------------------------------------- #


def _query_cobros_periodo(session: Session, hoy_local: date, filtros: FiltrosTablero):
    """La consulta base de "Periodo seleccionado": `Cobro` unido a `Paquete`
    (para poder filtrar por Tipo), acotada al rango del atajo activo (si lo
    hay) y a Tipo/Cobrado-Anulado (si vienen seteados). Sin atajo activo,
    TODOS los cobros existentes (issue 364)."""
    query = session.query(Cobro).join(Paquete, Cobro.paquete_id == Paquete.id)
    rango_dias = _rango_por_atajo(filtros.rango, hoy_local)
    if rango_dias is not None:
        desde_utc, hasta_utc = _limites_utc_de_dias_locales(*rango_dias)
        query = query.filter(Cobro.cobrado_en >= desde_utc, Cobro.cobrado_en <= hasta_utc)
    if filtros.tipo is not None:
        query = query.filter(Paquete.package_type == filtros.tipo)
    if filtros.anulado is not None:
        condicion = (
            Cobro.motivo_anulacion.isnot(None) if filtros.anulado else Cobro.motivo_anulacion.is_(None)
        )
        query = query.filter(condicion)
    return query


def _contar_paquetes(
    session: Session,
    columna,
    rango_dias: tuple[date, date] | None,
    tipo: TipoPaquete | None = None,
) -> int:
    """Cuántos `Paquete` tienen `columna` (uno de sus 4 timestamps de
    transición) no-nula y, si hay rango, dentro de él. `tipo`, si viene, se
    suma como condición AND -- lo pasan solo los callers a los que el filtro
    de Tipo SÍ les aplica (ver la matriz de "no aplica" en `Paquetes`)."""
    if rango_dias is None:
        condicion = columna.isnot(None)
    else:
        desde_utc, hasta_utc = _limites_utc_de_dias_locales(*rango_dias)
        condicion = and_(columna.isnot(None), columna >= desde_utc, columna <= hasta_utc)
    query = session.query(func.count(Paquete.id)).filter(condicion)
    if tipo is not None:
        query = query.filter(Paquete.package_type == tipo)
    return int(query.scalar())


def _contar_total_paquetes(session: Session, rango_dias: tuple[date, date] | None) -> int:
    """"Total de paquetes" = con CUALQUIER movimiento en el periodo -- unión
    de los 4 timestamps de transición, no solo `announced_at`. Sin rango,
    simplemente todos los paquetes que existen."""
    if rango_dias is None:
        return int(session.query(func.count(Paquete.id)).scalar())
    desde_utc, hasta_utc = _limites_utc_de_dias_locales(*rango_dias)

    def _en_rango(columna):
        return and_(columna.isnot(None), columna >= desde_utc, columna <= hasta_utc)

    return int(
        session.query(func.count(Paquete.id))
        .filter(
            or_(
                _en_rango(Paquete.announced_at),
                _en_rango(Paquete.received_at),
                _en_rango(Paquete.delivered_at),
                _en_rango(Paquete.cancelled_at),
            )
        )
        .scalar()
    )


def _dias_del_periodo(session: Session, hoy_local: date, rango_dias: tuple[date, date] | None) -> int:
    """Cuántos días abarca el periodo, para el divisor del ritmo. Con un
    atajo activo, los días de su rango (ambos inclusive). Sin ninguno ("todos
    los datos"), desde el primer `announced_at` que exista hasta hoy -- sin
    ningún paquete todavía, 1 (evita dividir por cero; el ritmo da 0 igual,
    porque el numerador también es 0)."""
    if rango_dias is not None:
        return (rango_dias[1] - rango_dias[0]).days + 1
    primero = session.query(func.min(Paquete.announced_at)).scalar()
    if primero is None:
        return 1
    primero_local = primero.astimezone(ZONA_HORARIA_APP).date()
    return max(1, (hoy_local - primero_local).days + 1)


def _calcular_paquetes_y_ritmo(
    session: Session, hoy_local: date, filtros: FiltrosTablero
) -> tuple[Paquetes, RitmoYTasas]:
    rango_dias = _rango_por_atajo(filtros.rango, hoy_local)

    total = _contar_total_paquetes(session, rango_dias)
    anunciados = _contar_paquetes(session, Paquete.announced_at, rango_dias)
    cancelados = _contar_paquetes(session, Paquete.cancelled_at, rango_dias)
    # Sin filtrar por Tipo -- SIEMPRE, incluso si `filtros.tipo` viene
    # seteado: son la base de las tasas (matriz de "no aplica": Tipo NO
    # acota Tasa de entrega/cancelación).
    entregados_todos = _contar_paquetes(session, Paquete.delivered_at, rango_dias)
    # Con el filtro de Tipo aplicado (si viene) -- lo que se muestra en la
    # tarjeta "Recibidos"/"Entregados" y en su ritmo.
    recibidos = _contar_paquetes(session, Paquete.received_at, rango_dias, tipo=filtros.tipo)
    entregados = _contar_paquetes(session, Paquete.delivered_at, rango_dias, tipo=filtros.tipo)

    paquetes = Paquetes(
        total=total, anunciados=anunciados, recibidos=recibidos, entregados=entregados, cancelados=cancelados
    )

    dias = _dias_del_periodo(session, hoy_local, rango_dias)

    def _ritmo(cantidad: int) -> RitmoMetrica:
        return RitmoMetrica(por_dia=cantidad / dias, por_semana=cantidad / dias * 7, por_mes=cantidad / dias * 30)

    cerrados = entregados_todos + cancelados
    tasa_entrega = (entregados_todos / cerrados * 100) if cerrados else None
    tasa_cancelacion = (cancelados / cerrados * 100) if cerrados else None

    ritmo = RitmoYTasas(
        anunciados=_ritmo(anunciados),
        recibidos=_ritmo(recibidos),
        entregados=_ritmo(entregados),
        tasa_entrega=tasa_entrega,
        tasa_cancelacion=tasa_cancelacion,
    )
    return paquetes, ritmo


def _tarifa_servicio(tarifas: TarifaCobro, tipo: TipoPaquete | None) -> int:
    """La tarifa de SERVICIO vigente que le tocaría a un paquete de `tipo`
    (el bodegaje no entra acá -- solo se estima lo exonerado/exento del
    cargo base, nunca del bodegaje, que nunca se exime -- ver
    `cobro_service.calcular_cobro`)."""
    return tarifas.base_extra_dimensionado if tipo == TipoPaquete.EXTRA_DIMENSIONADO else tarifas.base_normal


def _monto_estimado_por_tipo(filas_tipo_y_cantidad, tarifas: TarifaCobro) -> tuple[int, int]:
    """`filas_tipo_y_cantidad` = pares (Tipo, cantidad) ya agrupados.
    Retorna (cantidad total, monto total) usando la tarifa de servicio
    vigente de cada Tipo -- el monto es siempre una ESTIMACIÓN (ver
    `Recaudo`)."""
    cantidad_total = 0
    monto_total = 0
    for tipo, cantidad in filas_tipo_y_cantidad:
        cantidad = int(cantidad)
        cantidad_total += cantidad
        monto_total += cantidad * _tarifa_servicio(tarifas, tipo)
    return cantidad_total, monto_total


def _calcular_recaudo(session: Session, hoy_local: date, filtros: FiltrosTablero) -> Recaudo:
    tarifas = obtener_tarifas_vigentes(session)
    base = _query_cobros_periodo(session, hoy_local, filtros)

    cantidad, total_ingresos, bodegaje, servicio = base.with_entities(
        func.count(Cobro.id),
        func.coalesce(func.sum(Cobro.monto_total), 0),
        func.coalesce(func.sum(Cobro.monto_bodegaje), 0),
        func.coalesce(func.sum(Cobro.monto_base), 0),
    ).one()
    cantidad = int(cantidad)
    total_ingresos = int(total_ingresos)
    bodegaje = int(bodegaje)
    servicio = int(servicio)

    promedio_por_paquete = (total_ingresos / cantidad) if cantidad else None
    porcentaje_bodegaje = (bodegaje / total_ingresos * 100) if total_ingresos else None
    porcentaje_servicio = (servicio / total_ingresos * 100) if total_ingresos else None

    # "Exonerado por anulaciones" -- Tipo Y Cobrado/Anulado SÍ acotan esta
    # tarjeta (matriz de "no aplica"), así que se calcula sobre el MISMO
    # `base` ya filtrado por ambos.
    anulados_por_tipo = (
        base.filter(Cobro.motivo_anulacion.isnot(None))
        .with_entities(Paquete.package_type, func.count(Cobro.id))
        .group_by(Paquete.package_type)
        .all()
    )
    cantidad_anulaciones, exonerado_anulaciones = _monto_estimado_por_tipo(anulados_por_tipo, tarifas)
    tasa_anulacion = (cantidad_anulaciones / cantidad * 100) if cantidad else None

    # "Exenciones por primera entrega" -- Tipo SÍ acota, Cobrado/Anulado NO
    # (matriz): se recalcula sobre una variante de `base` con el mismo rango
    # y Tipo pero IGNORANDO el filtro de Cobrado/Anulado.
    filtros_sin_anulado = FiltrosTablero(rango=filtros.rango, tipo=filtros.tipo, anulado=None)
    base_exenciones = _query_cobros_periodo(session, hoy_local, filtros_sin_anulado)
    exentos_por_tipo = (
        base_exenciones.filter(Cobro.monto_base == 0, Cobro.motivo_anulacion.is_(None))
        .with_entities(Paquete.package_type, func.count(Cobro.id))
        .group_by(Paquete.package_type)
        .all()
    )
    exenciones_primera_entrega, dejado_de_cobrar_primera_entrega = _monto_estimado_por_tipo(
        exentos_por_tipo, tarifas
    )

    # "Cobro más alto" -- `bloques_bodegaje` es la MISMA cifra que el resto
    # de la app ya le muestra al staff como "N días" (ver `/paquetes`).
    fila_max = (
        base.order_by(Cobro.monto_total.desc())
        .with_entities(Cobro.monto_total, Cobro.bloques_bodegaje)
        .first()
    )
    cobro_mas_alto = int(fila_max[0]) if fila_max is not None else None
    dias_bodega_del_mas_alto = int(fila_max[1]) if fila_max is not None else None

    return Recaudo(
        total_ingresos=total_ingresos,
        promedio_por_paquete=promedio_por_paquete,
        recaudado_bodegaje=bodegaje,
        porcentaje_bodegaje=porcentaje_bodegaje,
        recaudado_servicio=servicio,
        porcentaje_servicio=porcentaje_servicio,
        exonerado_anulaciones=exonerado_anulaciones,
        cantidad_anulaciones=cantidad_anulaciones,
        tasa_anulacion=tasa_anulacion,
        exenciones_primera_entrega=exenciones_primera_entrega,
        dejado_de_cobrar_primera_entrega=dejado_de_cobrar_primera_entrega,
        cobro_mas_alto=cobro_mas_alto,
        dias_bodega_del_mas_alto=dias_bodega_del_mas_alto,
    )


def _cliente_no_eliminado_ni_de_baja():
    """Condición para descartar de "activos"/"nuevos"/"recurrentes" a
    Personas dadas de baja administrativa (`dar_de_baja_administrativa`
    nunca toca el Teléfono, así que el JOIN por teléfono la sigue
    encontrando) -- spec.md: no cuentan como clientes aunque tengan
    paquetes históricos.

    Límite conocido y aceptado: `anonimizar_persona` (ADR-0005, derecho al
    olvido) SÍ reemplaza el Teléfono por uno sintético -- el
    `recipient_phone` congelado en un paquete viejo queda huérfano, sin
    ninguna Persona VIVA que lo matchee, así que esta condición no puede
    excluir retroactivamente a alguien ya anonimizado (`Persona.id` da
    `NULL` en el JOIN, tratado como "no excluir" -- mismo criterio
    defensivo de siempre: sin Persona que matchee, no se excluye). Esto no
    empeora nada ya existente: cualquier snapshot de Paquete ya conserva el
    nombre histórico tal cual estaba ANTES de anonimizar, por diseño
    (ADR-0001, "los datos permanecen de principio a fin en cada paquete")."""
    return or_(
        Persona.id.is_(None),
        and_(Persona.eliminado_en.is_(None), Persona.baja_administrativa_en.is_(None)),
    )


def _paquetes_por_cliente_con_movimiento(
    session: Session, rango_dias: tuple[date, date] | None, tipo: TipoPaquete | None
) -> list[tuple[str, int]]:
    """`[(recipient_phone, cantidad)]` de paquetes CON destinatario propio
    (excluye "nombre sin teléfono") que tuvieron cualquier movimiento en el
    rango -- mismo criterio que `_contar_total_paquetes`, agrupado por
    cliente. `tipo`, si viene, filtra (matriz: Tipo SÍ acota Clientes)."""
    query = (
        session.query(Paquete.recipient_phone, func.count(Paquete.id))
        .outerjoin(Persona, Persona.telefono == Paquete.recipient_phone)
        .filter(Paquete.recipient_phone.isnot(None), _cliente_no_eliminado_ni_de_baja())
    )
    if tipo is not None:
        query = query.filter(Paquete.package_type == tipo)
    if rango_dias is not None:
        desde_utc, hasta_utc = _limites_utc_de_dias_locales(*rango_dias)

        def _en_rango(columna):
            return and_(columna.isnot(None), columna >= desde_utc, columna <= hasta_utc)

        query = query.filter(
            or_(
                _en_rango(Paquete.announced_at),
                _en_rango(Paquete.received_at),
                _en_rango(Paquete.delivered_at),
                _en_rango(Paquete.cancelled_at),
            )
        )
    return query.group_by(Paquete.recipient_phone).all()


def _contar_clientes_nuevos(
    session: Session, rango_dias: tuple[date, date] | None, tipo: TipoPaquete | None
) -> int:
    """Clientes cuya PRIMERA entrega HISTÓRICA (toda la vida, no solo el
    periodo) cae dentro del periodo -- por eso arranca de un `MIN(delivered_
    at)` sin acotar por fecha, y solo DESPUÉS se filtra ese mínimo contra el
    rango."""
    base = (
        session.query(Paquete.recipient_phone.label("tel"), func.min(Paquete.delivered_at).label("primera"))
        .outerjoin(Persona, Persona.telefono == Paquete.recipient_phone)
        .filter(
            Paquete.recipient_phone.isnot(None),
            Paquete.delivered_at.isnot(None),
            _cliente_no_eliminado_ni_de_baja(),
        )
    )
    if tipo is not None:
        base = base.filter(Paquete.package_type == tipo)
    subq = base.group_by(Paquete.recipient_phone).subquery()

    query = session.query(func.count()).select_from(subq)
    if rango_dias is not None:
        desde_utc, hasta_utc = _limites_utc_de_dias_locales(*rango_dias)
        query = query.filter(subq.c.primera >= desde_utc, subq.c.primera <= hasta_utc)
    return int(query.scalar())


def _nombre_y_apartamento_de_cliente(session: Session, telefono: str) -> tuple[str, str | None]:
    """El NOMBRE de la Persona de este teléfono (o, si no existe Persona, el
    nombre congelado en su paquete más reciente) y su Apartamento del
    snapshot MÁS RECIENTE -- ADR-0001, nunca la unidad actual."""
    persona = session.query(Persona).filter(Persona.telefono == telefono).one_or_none()
    fila = (
        session.query(Paquete.recipient_name, Paquete.snapshot_torre, Paquete.snapshot_apartamento)
        .filter(Paquete.recipient_phone == telefono)
        .order_by(Paquete.announced_at.desc())
        .first()
    )
    torre = apto = None
    nombre_snapshot = telefono
    if fila is not None:
        nombre_snapshot, torre, apto = fila
    nombre = persona.nombre if persona is not None else nombre_snapshot
    partes = [p for p in (torre, apto) if p]
    return nombre, " ".join(partes) if partes else None


def _cliente_destacado(session: Session, filas_telefono_valor) -> "ClienteDestacado | None":
    """El cliente con el mayor `valor` de `[(telefono, valor)]` -- empate
    resuelto por NOMBRE (spec.md: "empates por monto y luego por nombre"),
    determinista entre cargas."""
    candidatos = []
    for telefono, valor in filas_telefono_valor:
        nombre, apartamento = _nombre_y_apartamento_de_cliente(session, telefono)
        candidatos.append((int(valor), nombre, apartamento))
    if not candidatos:
        return None
    candidatos.sort(key=lambda c: (-c[0], c[1]))
    valor, nombre, apartamento = candidatos[0]
    return ClienteDestacado(nombre=nombre, apartamento=apartamento, valor=valor)


def _calcular_clientes(session: Session, hoy_local: date, filtros: FiltrosTablero) -> Clientes:
    rango_dias = _rango_por_atajo(filtros.rango, hoy_local)

    filas_movimiento = _paquetes_por_cliente_con_movimiento(session, rango_dias, filtros.tipo)
    activos = len(filas_movimiento)
    recurrentes = sum(1 for _, cantidad in filas_movimiento if cantidad >= 2)
    nuevos = _contar_clientes_nuevos(session, rango_dias, filtros.tipo)
    con_mas_paquetes = _cliente_destacado(session, filas_movimiento)

    # "Cliente con mayor gasto" -- a diferencia del resto de esta categoría,
    # SÍ respeta Cobrado/Anulado (matriz de "no aplica"): reusa la MISMA
    # consulta de Cobros de "Recaudo", agrupada por destinatario.
    filas_gasto = (
        _query_cobros_periodo(session, hoy_local, filtros)
        .filter(Paquete.recipient_phone.isnot(None))
        .with_entities(Paquete.recipient_phone, func.coalesce(func.sum(Cobro.monto_total), 0))
        .group_by(Paquete.recipient_phone)
        .all()
    )
    con_mayor_gasto = _cliente_destacado(session, filas_gasto)

    return Clientes(
        activos=activos,
        nuevos=nuevos,
        recurrentes=recurrentes,
        con_mas_paquetes=con_mas_paquetes,
        con_mayor_gasto=con_mayor_gasto,
    )


_DIAS_SEMANA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def _formato_hora_pico(hora: int) -> str:
    """`hora` (0-23, local) a un rango de una hora legible, ej. 18 ->
    "6 – 7 p. m." -- usa el sufijo AM/PM de la hora de INICIO para ambos
    extremos (simplificación aceptada: el único cruce real, 23 -> "11 – 12
    p. m.", es un caso raro y de lectura igualmente clara)."""

    def _doce_horas(h: int) -> int:
        h12 = h % 12
        return 12 if h12 == 0 else h12

    sufijo = "a. m." if hora < 12 else "p. m."
    return f"{_doce_horas(hora)} – {_doce_horas((hora + 1) % 24)} {sufijo}"


def _operador_top(
    session: Session, rango_dias: tuple[date, date] | None, tipo: TipoPaquete | None
) -> tuple[str | None, int]:
    query = session.query(Paquete.delivered_by_usuario_id, func.count(Paquete.id)).filter(
        Paquete.delivered_at.isnot(None)
    )
    if rango_dias is not None:
        desde_utc, hasta_utc = _limites_utc_de_dias_locales(*rango_dias)
        query = query.filter(Paquete.delivered_at >= desde_utc, Paquete.delivered_at <= hasta_utc)
    if tipo is not None:
        query = query.filter(Paquete.package_type == tipo)

    candidatos = []
    for usuario_id, cantidad in query.group_by(Paquete.delivered_by_usuario_id).all():
        if usuario_id is None:
            continue
        usuario = session.get(Usuario, usuario_id)
        if usuario is None:
            continue
        candidatos.append((int(cantidad), usuario.nombre))
    if not candidatos:
        return None, 0
    candidatos.sort(key=lambda c: (-c[0], c[1]))  # empate: por nombre
    cantidad, nombre = candidatos[0]
    return nombre, cantidad


def _dia_y_hora_pico(
    session: Session, rango_dias: tuple[date, date] | None, tipo: TipoPaquete | None
) -> tuple[str | None, float | None, str | None]:
    """Día de la semana y franja horaria (ambos en HORA DE COLOMBIA) con más
    entregas del periodo -- se resuelve en Python sobre los timestamps ya
    traídos (no en SQL), mismo criterio que el resto del servicio: nunca se
    le pide a la base de datos que conozca zonas horarias con nombre."""
    query = session.query(Paquete.delivered_at).filter(Paquete.delivered_at.isnot(None))
    if rango_dias is not None:
        desde_utc, hasta_utc = _limites_utc_de_dias_locales(*rango_dias)
        query = query.filter(Paquete.delivered_at >= desde_utc, Paquete.delivered_at <= hasta_utc)
    if tipo is not None:
        query = query.filter(Paquete.package_type == tipo)
    instantes = [fila[0].astimezone(ZONA_HORARIA_APP) for fila in query.all()]
    if not instantes:
        return None, None, None

    conteo_dia = [0] * 7
    conteo_hora = [0] * 24
    for local in instantes:
        conteo_dia[local.weekday()] += 1
        conteo_hora[local.hour] += 1

    mejor_dia = 0
    for i in range(1, 7):
        if conteo_dia[i] > conteo_dia[mejor_dia]:
            mejor_dia = i
    mejor_hora = 0
    for i in range(1, 24):
        if conteo_hora[i] > conteo_hora[mejor_hora]:
            mejor_hora = i

    total = len(instantes)
    return (
        _DIAS_SEMANA[mejor_dia],
        conteo_dia[mejor_dia] / total * 100,
        _formato_hora_pico(mejor_hora),
    )


def _porcentaje_dentro_de_48h(
    session: Session, rango_dias: tuple[date, date] | None, tipo: TipoPaquete | None
) -> float | None:
    query = session.query(Paquete.received_at, Paquete.delivered_at).filter(Paquete.delivered_at.isnot(None))
    if rango_dias is not None:
        desde_utc, hasta_utc = _limites_utc_de_dias_locales(*rango_dias)
        query = query.filter(Paquete.delivered_at >= desde_utc, Paquete.delivered_at <= hasta_utc)
    if tipo is not None:
        query = query.filter(Paquete.package_type == tipo)
    filas = query.all()
    if not filas:
        return None
    dentro = sum(
        1 for recibido, entregado in filas if recibido is not None and (entregado - recibido) <= timedelta(hours=48)
    )
    return dentro / len(filas) * 100


def _porcentaje_extra_dimensionados(session: Session, rango_dias: tuple[date, date] | None) -> float | None:
    """Sobre los RECIBIDOS del periodo -- Tipo NUNCA la acota (matriz de "no
    aplica": filtrar por Tipo la volvería trivial, 0% o 100%)."""
    query = session.query(Paquete.package_type).filter(Paquete.received_at.isnot(None))
    if rango_dias is not None:
        desde_utc, hasta_utc = _limites_utc_de_dias_locales(*rango_dias)
        query = query.filter(Paquete.received_at >= desde_utc, Paquete.received_at <= hasta_utc)
    tipos = [fila[0] for fila in query.all()]
    if not tipos:
        return None
    return sum(1 for t in tipos if t == TipoPaquete.EXTRA_DIMENSIONADO) / len(tipos) * 100


def _porcentaje_mal_estado(
    session: Session, rango_dias: tuple[date, date] | None, tipo: TipoPaquete | None
) -> float | None:
    query = session.query(Paquete.package_condition).filter(Paquete.received_at.isnot(None))
    if rango_dias is not None:
        desde_utc, hasta_utc = _limites_utc_de_dias_locales(*rango_dias)
        query = query.filter(Paquete.received_at >= desde_utc, Paquete.received_at <= hasta_utc)
    if tipo is not None:
        query = query.filter(Paquete.package_type == tipo)
    condiciones = [fila[0] for fila in query.all()]
    if not condiciones:
        return None
    malos = sum(1 for c in condiciones if c in (CondicionPaquete.ABIERTO, CondicionPaquete.REGULAR))
    return malos / len(condiciones) * 100


def _calcular_operacion(session: Session, hoy_local: date, filtros: FiltrosTablero) -> OperacionYCalidad:
    rango_dias = _rango_por_atajo(filtros.rango, hoy_local)
    operador_nombre, operador_cantidad = _operador_top(session, rango_dias, filtros.tipo)
    dia, dia_pct, hora = _dia_y_hora_pico(session, rango_dias, filtros.tipo)
    return OperacionYCalidad(
        operador_top_nombre=operador_nombre,
        operador_top_cantidad=operador_cantidad,
        dia_mas_activo=dia,
        dia_mas_activo_porcentaje=dia_pct,
        hora_pico=hora,
        porcentaje_dentro_de_48h=_porcentaje_dentro_de_48h(session, rango_dias, filtros.tipo),
        porcentaje_extra_dimensionados=_porcentaje_extra_dimensionados(session, rango_dias),
        porcentaje_mal_estado=_porcentaje_mal_estado(session, rango_dias, filtros.tipo),
    )


def _calcular_sms_periodo(
    session: Session, hoy_local: date, filtros: FiltrosTablero, total_paquetes: int
) -> SmsPeriodo:
    rango_dias = _rango_por_atajo(filtros.rango, hoy_local)
    desde_utc = hasta_utc = None
    if rango_dias is not None:
        desde_utc, hasta_utc = _limites_utc_de_dias_locales(*rango_dias)

    avisos = contar_envios(
        session, tipo=TipoRegistroSms.AVISO_PAQUETE, proveedor=_PROVEEDOR_SMS_CON_COSTO,
        desde=desde_utc, hasta=hasta_utc,
    )
    codigos = contar_envios(
        session, tipo=TipoRegistroSms.OTP, proveedor=_PROVEEDOR_SMS_CON_COSTO,
        desde=desde_utc, hasta=hasta_utc,
    )
    fallidos = contar_envios(session, exitoso=False, desde=desde_utc, hasta=hasta_utc)
    enviados_aws = avisos + codigos

    costo_unitario = obtener_costo_promedio_sms(session, _PROVEEDOR_SMS_CON_COSTO)
    costo_estimado = float(costo_unitario) * enviados_aws if costo_unitario is not None else None
    costo_por_paquete = (
        costo_estimado / total_paquetes if (costo_estimado is not None and total_paquetes) else None
    )

    return SmsPeriodo(
        enviados_aws=enviados_aws,
        avisos_aws=avisos,
        codigos_aws=codigos,
        fallidos=fallidos,
        costo_estimado=costo_estimado,
        costo_por_paquete=costo_por_paquete,
        registro_desde=fecha_primer_registro(session),
    )


def _calcular_periodo(session: Session, hoy_local: date, filtros: FiltrosTablero) -> PeriodoSeleccionado:
    rango_activo = filtros.rango if _rango_por_atajo(filtros.rango, hoy_local) is not None else None
    recaudo = _calcular_recaudo(session, hoy_local, filtros)
    paquetes, ritmo = _calcular_paquetes_y_ritmo(session, hoy_local, filtros)
    clientes = _calcular_clientes(session, hoy_local, filtros)
    operacion = _calcular_operacion(session, hoy_local, filtros)
    sms = _calcular_sms_periodo(session, hoy_local, filtros, paquetes.total)
    return PeriodoSeleccionado(
        rango_activo=rango_activo,
        recaudo=recaudo,
        paquetes=paquetes,
        ritmo=ritmo,
        clientes=clientes,
        operacion=operacion,
        sms=sms,
    )


def calcular_tablero(
    session: Session, ahora: datetime, filtros: FiltrosTablero = FiltrosTablero()
) -> TableroEstadisticasCobro:
    """El tablero completo para el instante `ahora` (UTC-aware, inyectado --
    nunca `datetime.now()` acá dentro, para que las pruebas puedan fijar el
    reloj) y los `filtros` de "Periodo seleccionado". Panorama y Periodo
    resuelven "hoy" con la MISMA hora de Colombia derivada de `ahora`."""
    ahora_local = ahora.astimezone(ZONA_HORARIA_APP)
    hoy_local = ahora_local.date()
    return TableroEstadisticasCobro(
        panorama=_calcular_panorama(session, hoy_local, ahora_local),
        ahora=_calcular_ahora(session, ahora),
        periodo=_calcular_periodo(session, hoy_local, filtros),
    )
