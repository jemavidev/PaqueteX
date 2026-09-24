# -*- coding: utf-8 -*-
"""
Capa web — `/administracion/estadisticas-cobro` (rediseño de tablero de
tarjetas, `.scratch/estadisticas-cobro-dashboard`, ticket 01; reemplaza el
rediseño de listas de `.scratch/estadisticas-cobro-interactivas`). Solo
lectura, exclusiva de admin.

La aritmética de las tarjetas (Panorama en hora de Colombia, filtros de
"Todo el historial", casos de frontera de zona horaria) ya está probada a
fondo contra Postgres real en `tests/data_model/
test_estadisticas_tablero_service.py` (Seam A) -- acá solo se cubre que la
ruta HTTP arme los filtros correctos, que el control de acceso sea el
correcto, que el mecanismo de fetch en vivo devuelva solo el fragmento, y que
lo retirado (las 3 listas/paginación, el `<select>` de Usuario, Desde/Hasta,
el parámetro `hoy`) de verdad no esté.

Como la ruta ya no acepta fijar el reloj (`hoy` se retiró -- el servidor usa
su propio reloj real, en hora de Colombia), estos tests NO verifican límites
exactos de día/semana/mes (eso es del seam de dominio); solo que un cobro
recién creado ("ahora" real) aparece en el tablero y que los filtros se
reflejan en la salida.
"""

import re
from datetime import datetime, time, timedelta
from html.parser import HTMLParser

from app.domain.cobro import Cobro
from app.domain.cobro_service import DesgloseCobro, crear_motivo_anulacion, registrar_cobro
from app.domain.paquete import TipoPaquete
from app.domain.paquete_lifecycle import deliver as dom_deliver
from app.domain.paquete_lifecycle import receive as dom_receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.registro_sms import RegistroSms, TipoRegistroSms
from app.domain.registro_sms_service import registrar_envio
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario, Usuario
from app.domain.zona_horaria import ZONA_HORARIA_APP

_PW = "Contrasena1"


def _login_admin(client, email="admin@club.com"):
    admin = create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})
    return admin


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


class _Metricas(HTMLParser):
    """Extrae cada elemento `data-metrica="..."` del HTML del tablero: su `class`
    y su texto visible (con todo lo que lleve dentro). Cada dato de "Periodo
    seleccionado" se declara con ese identificador (macros `metrica`/`dato` de
    `_estadisticas_cobro_resultados.html`), así una prueba apunta a UN dato sin
    depender del texto que se vea ni de dónde caiga su `</article>` -- el diseño
    agrupa varias métricas por panel. Solo stdlib: el proyecto no trae un
    parser HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.metricas = {}
        self._abiertas = []  # [slug, etiqueta HTML, profundidad de esa etiqueta]

    def handle_starttag(self, tag, attrs):
        for abierta in self._abiertas:
            if abierta[1] == tag:
                abierta[2] += 1
        atributos = dict(attrs)
        if "data-metrica" in atributos:
            slug = atributos["data-metrica"]
            self.metricas[slug] = {"clase": atributos.get("class") or "", "texto": ""}
            self._abiertas.append([slug, tag, 1])

    def handle_endtag(self, tag):
        for abierta in list(self._abiertas):
            if abierta[1] == tag:
                abierta[2] -= 1
                if abierta[2] == 0:
                    self._abiertas.remove(abierta)

    def handle_data(self, data):
        for slug, _, _ in self._abiertas:
            self.metricas[slug]["texto"] += data


def _metricas(html):
    """Todos los datos con `data-metrica` de la página: {slug: {clase, texto,
    atenuada}}. `atenuada` = lleva la clase `opacity-40` (matriz de "no aplica")."""
    parser = _Metricas()
    parser.feed(html)
    for m in parser.metricas.values():
        m["texto"] = " ".join(m["texto"].split())
        m["atenuada"] = "opacity-40" in m["clase"].split()
    return parser.metricas


def _metrica(html, slug):
    metricas = _metricas(html)
    assert slug in metricas, f"no hay data-metrica={slug!r}; hay: {sorted(metricas)}"
    return metricas[slug]


def _zona_periodo(texto):
    """El fragmento de HTML de "Todo el historial" -- es la ÚLTIMA de las
    3 zonas, así que basta con recortar desde su marca de apertura hasta el
    final. Panorama SIEMPRE muestra el total sin filtrar (issue estadisticas-
    cobro-dashboard): comparar un monto contra la página completa colisiona
    con la propia tarjeta de Panorama, que no filtra nada -- las
    aserciones de "Todo el historial" deben acotarse a este recorte."""
    inicio = texto.index('aria-label="Todo el historial')
    return texto[inicio:]


def _entregar_con_cobro(client, staff, monto_total, tel, tipo=None, motivo_anulacion=None):
    p = announce(
        client.db,
        anunciante_telefono=tel,
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    dom_receive(client.db, p, staff, package_type=tipo)
    dom_deliver(client.db, p, staff)
    registrar_cobro(
        client.db,
        p,
        DesgloseCobro(
            monto_base=monto_total, bloques_bodegaje=0, monto_bodegaje=0, monto_total=monto_total
        ),
        staff,
        motivo_anulacion=motivo_anulacion,
    )
    client.db.commit()
    return p


def test_sin_sesion_redirige_a_login(client):
    r = client.get("/administracion/estadisticas-cobro", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_operador_es_rechazado_403(client):
    _login_operador(client)
    r = client.get("/administracion/estadisticas-cobro")
    assert r.status_code == 403


def test_carga_completa_muestra_las_tres_zonas(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 4321, tel="3001111111")

    r = client.get("/administracion/estadisticas-cobro")
    assert r.status_code == 200
    assert "<h1" in r.text
    # El punto de color de cada zona -- Panorama (azul), Ahora (ámbar), Periodo
    # seleccionado (verde) -- junto a su título visible (ver la prueba de títulos).
    assert "bg-blue-600" in r.text
    assert "bg-amber-500" in r.text
    assert "bg-emerald-600" in r.text
    assert "Ingresos" in r.text
    assert "Total de ingresos" in r.text
    assert "4,321" in r.text


def test_ingresos_de_panorama_no_cambia_con_ningun_filtro(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 7777, tel="3001111111", tipo=TipoPaquete.NORMAL)

    sin_filtro = client.get("/administracion/estadisticas-cobro")
    con_tipo = client.get(
        "/administracion/estadisticas-cobro", params={"tipo": "EXTRA_DIMENSIONADO"}
    )
    con_rango_lejano = client.get("/administracion/estadisticas-cobro", params={"rango": "hoy"})

    for r in (sin_filtro, con_tipo, con_rango_lejano):
        assert r.status_code == 200
        assert "7,777" in r.text  # sigue en "Ingresos" de Panorama, sin importar el filtro


def test_total_de_ingresos_de_periodo_responde_a_los_filtros(client):
    admin = _login_admin(client)
    normal = _entregar_con_cobro(client, admin, 1500, tel="3001111111", tipo=TipoPaquete.NORMAL)
    _entregar_con_cobro(client, admin, 2500, tel="3002222222", tipo=TipoPaquete.EXTRA_DIMENSIONADO)
    assert normal.estado.value == "ENTREGADO"

    sin_filtro = _zona_periodo(client.get("/administracion/estadisticas-cobro").text)
    assert "4,000" in sin_filtro  # Total de ingresos = todos los datos
    assert "Todos los datos" in sin_filtro

    solo_normal = _zona_periodo(
        client.get("/administracion/estadisticas-cobro", params={"tipo": "NORMAL"}).text
    )
    assert "1,500" in solo_normal
    assert "4,000" not in solo_normal

    solo_extra = _zona_periodo(
        client.get(
            "/administracion/estadisticas-cobro", params={"tipo": "EXTRA_DIMENSIONADO"}
        ).text
    )
    assert "2,500" in solo_extra


def test_total_de_ingresos_filtra_por_cobrado_y_anulado(client):
    admin = _login_admin(client)
    crear_motivo_anulacion(client.db, "Reclamo")
    client.db.commit()
    _entregar_con_cobro(client, admin, 1234, tel="3001111111")
    _entregar_con_cobro(client, admin, 0, tel="3002222222", motivo_anulacion="Reclamo")

    solo_anulados = client.get(
        "/administracion/estadisticas-cobro", params={"estado_cobro": "anulado"}
    )
    assert solo_anulados.status_code == 200
    assert "1,234" not in _zona_periodo(solo_anulados.text)
    assert "Anulado" in solo_anulados.text

    solo_cobrados = client.get(
        "/administracion/estadisticas-cobro", params={"estado_cobro": "cobrado"}
    )
    assert "1,234" in _zona_periodo(solo_cobrados.text)
    assert "Cobrado" in solo_cobrados.text


def test_una_clave_de_rango_desconocida_se_ignora_sin_error(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1500, tel="3001234567")

    r = client.get("/administracion/estadisticas-cobro", params={"rango": "no-existe"})
    assert r.status_code == 200
    assert "1,500" in r.text
    assert "Todos los datos" in r.text


def test_el_parametro_hoy_ya_no_se_acepta_y_se_ignora(client):
    """Issue estadisticas-cobro-dashboard, ticket 01: el servidor calcula
    "hoy" con su propio reloj, en hora de Colombia -- ya no depende de lo que
    mande el navegador."""
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1500, tel="3001234567")

    r = client.get(
        "/administracion/estadisticas-cobro",
        params={"rango": "hoy", "hoy": "1999-01-01"},
    )
    assert r.status_code == 200
    assert "1,500" in r.text  # sigue contando "hoy" de verdad, no 1999


def test_ya_no_acepta_desde_hasta_ni_paginacion_ni_usuario(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1500, tel="3001234567")

    r = client.get(
        "/administracion/estadisticas-cobro",
        params={
            "desde": "2020-01-01",
            "hasta": "2020-12-31",
            "pagina_apartamento": 2,
            "pagina_usuario": 2,
            "pagina_diario": 2,
            "usuario_id": "no-es-un-uuid",
        },
    )
    assert r.status_code == 200
    assert "1,500" in r.text  # ninguno de esos parámetros lo excluyó


def test_las_tres_listas_y_sus_controles_ya_no_existen(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1500, tel="3001234567")

    r = client.get("/administracion/estadisticas-cobro")
    for texto in (
        "Por cliente / apartamento",
        "Por usuario",
        "Serie diaria",
        "Todos los usuarios",
        'name="desde"',
        'name="hasta"',
        'name="usuario_id"',
        "data-pag-prev",
        "data-pag-next",
    ):
        assert texto not in r.text


def test_ya_no_hay_barra_de_filtros(client):
    """Issue 399 (`.scratch/pendientes-cliente`): se quitó la barra de filtros --
    atajos de fecha, Tipo y Cobrado/Anulado -- a pedido del cliente."""
    _login_admin(client)

    r = client.get("/administracion/estadisticas-cobro")

    assert r.status_code == 200
    assert 'id="filtros-estadisticas-cobro"' not in r.text
    assert "data-atajo-fecha" not in r.text
    assert "data-tipo-icono" not in r.text
    assert "data-estadocobro-icono" not in r.text
    assert re.search(r"<h1[^>]*>\s*Estadísticas de cobro\s*</h1>", r.text)


def test_con_base_vacia_carga_sin_error(client):
    _login_admin(client)

    r = client.get("/administracion/estadisticas-cobro")
    assert r.status_code == 200
    assert "$0" in r.text


def test_peticion_en_vivo_devuelve_solo_el_fragmento(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1500, tel="3001234567")

    r = client.get(
        "/administracion/estadisticas-cobro", headers={"X-Requested-With": "fetch"}
    )
    assert r.status_code == 200
    assert "<html" not in r.text
    assert "<h1" not in r.text  # el título vive en la página, fuera del fragmento
    assert "1,500" in r.text


def test_carga_completa_incluye_el_titulo_y_el_fragmento(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 4321, tel="3001111111")

    r = client.get("/administracion/estadisticas-cobro")
    assert r.status_code == 200
    assert "<h1" in r.text
    assert "4,321" in r.text


def test_paquetes_ritmo_y_tasas_se_ven_en_periodo(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")

    r = _zona_periodo(client.get("/administracion/estadisticas-cobro").text)
    for texto in ("Total de paquetes", "Anunciados", "Recibidos", "Entregados", "Cancelados", "Tasa de entrega", "Tasa de cancelación"):
        assert texto in r
    for slug in (
        "total_paquetes", "paquetes_anunciados", "paquetes_recibidos", "paquetes_entregados",
        "paquetes_cancelados", "ritmo_anunciados", "ritmo_recibidos", "ritmo_entregados",
        "tasa_entrega", "tasa_cancelacion",
    ):
        _metrica(r, slug)


def test_tipo_atenua_total_de_paquetes_pero_no_recibidos(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1000, tel="3001111111", tipo=TipoPaquete.NORMAL)

    sin_filtro = _zona_periodo(client.get("/administracion/estadisticas-cobro").text)
    assert "no depende de Tipo" not in sin_filtro

    con_tipo = _zona_periodo(
        client.get("/administracion/estadisticas-cobro", params={"tipo": "NORMAL"}).text
    )
    assert "no depende de Tipo" in con_tipo
    # Tipo acota Recibidos (y Entregados) pero no el total de paquetes con movimiento.
    assert _metrica(con_tipo, "total_paquetes")["atenuada"]
    assert not _metrica(con_tipo, "paquetes_recibidos")["atenuada"]


def test_recaudo_completo_se_ve_en_periodo(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")

    r = _zona_periodo(client.get("/administracion/estadisticas-cobro").text)
    for texto in (
        "Total de ingresos", "Promedio por paquete", "Bodegaje", "Servicio",
        "Exonerado por anulaciones", "Exenciones por primera entrega", "Cobro más alto",
    ):
        assert texto in r
    for slug in (
        "total_ingresos", "promedio_por_paquete", "recaudado_bodegaje", "recaudado_servicio",
        "exonerado_por_anulaciones", "exenciones_primera_entrega", "cobro_mas_alto",
    ):
        _metrica(r, slug)


def test_promedio_por_paquete_se_redondea_sin_decimales_de_flotante(client):
    """Bug real encontrado en vivo: `promedio_por_paquete` es un float (una
    división) -- formatearlo como dinero sin redondear imprimía algo como
    "$2,520.6919945725917" en vez de "$2,521"."""
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")
    _entregar_con_cobro(client, admin, 1234, tel="3002222222")

    promedio = _metrica(client.get("/administracion/estadisticas-cobro").text, "promedio_por_paquete")
    assert "$1,117" in promedio["texto"]  # (1000+1234)/2 = 1117.0, exacto
    assert "." not in promedio["texto"]


def test_exenciones_primera_entrega_se_atenua_con_cobrado_anulado_pero_no_con_tipo(client):
    """Matriz de "no aplica" (issue estadisticas-cobro-dashboard, spec.md):
    esta tarjeta es la única excepción dentro de "Recaudo" -- Tipo SÍ la
    acota, Cobrado/Anulado NO."""
    admin = _login_admin(client)
    crear_motivo_anulacion(client.db, "Reclamo")
    client.db.commit()
    _entregar_con_cobro(client, admin, 1500, tel="3001111111")  # primera entrega -- exenta

    con_estado = client.get(
        "/administracion/estadisticas-cobro", params={"estado_cobro": "cobrado"}
    ).text
    exenciones = _metrica(con_estado, "exenciones_primera_entrega")
    assert exenciones["atenuada"]
    assert "no depende de Cobrado/Anulado" in exenciones["texto"]

    con_tipo = client.get("/administracion/estadisticas-cobro", params={"tipo": "NORMAL"}).text
    exenciones = _metrica(con_tipo, "exenciones_primera_entrega")
    assert not exenciones["atenuada"]
    assert "no depende de" not in exenciones["texto"]


def test_clientes_se_ven_en_periodo_con_nombre_no_telefono(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")

    r = _zona_periodo(client.get("/administracion/estadisticas-cobro").text)
    for texto in ("Clientes", "Activos", "Nuevos", "Recurrentes", "Más paquetes", "Mayor gasto"):
        assert texto in r
    for slug in (
        "clientes_activos", "clientes_nuevos", "clientes_recurrentes",
        "cliente_con_mas_paquetes", "cliente_con_mayor_gasto",
    ):
        _metrica(r, slug)
    assert "ANA" in _metrica(r, "cliente_con_mas_paquetes")["texto"]  # el nombre, no su teléfono
    assert "+573001111111" not in r


def test_cobrado_anulado_atenua_paquetes_y_ritmo_pero_no_recaudo(client):
    admin = _login_admin(client)
    crear_motivo_anulacion(client.db, "Reclamo")
    client.db.commit()
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")

    r = _zona_periodo(
        client.get("/administracion/estadisticas-cobro", params={"estado_cobro": "cobrado"}).text
    )
    assert "no depende de Cobrado/Anulado" in r
    for slug in ("total_paquetes", "paquetes_anunciados", "ritmo_anunciados", "tasa_entrega"):
        assert _metrica(r, slug)["atenuada"], slug
    # "Total de ingresos" (Recaudo) SÍ responde a Cobrado/Anulado -- nunca
    # debería llevar esa nota.
    total = _metrica(r, "total_ingresos")
    assert not total["atenuada"]
    assert "no depende de" not in total["texto"]


def test_operacion_y_calidad_se_ven_en_periodo(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")

    r = _zona_periodo(client.get("/administracion/estadisticas-cobro").text)
    for texto, slug in (
        ("Operador con más entregas", "operador_con_mas_entregas"),
        ("Día más activo", "dia_mas_activo"),
        ("Hora pico", "hora_pico"),
        ("Entregas dentro de 48 h", "entregas_dentro_de_48h"),
        ("Extra-dimensionados", "extra_dimensionados"),
        ("Recibidos en mal estado", "recibidos_en_mal_estado"),
    ):
        assert texto in _metrica(r, slug)["texto"]


def test_operacion_y_calidad_no_depende_de_cobrado_anulado(client):
    # Ninguna tarjeta de Operación/Calidad responde a Cobrado/Anulado (matriz
    # del ticket 05) -- al activar ese filtro, todas deben mostrar la nota.
    admin = _login_admin(client)
    crear_motivo_anulacion(client.db, "Reclamo")
    client.db.commit()
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")

    r = _zona_periodo(
        client.get("/administracion/estadisticas-cobro", params={"estado_cobro": "cobrado"}).text
    )

    for slug in (
        "operador_con_mas_entregas",
        "dia_mas_activo",
        "hora_pico",
        "entregas_dentro_de_48h",
        "extra_dimensionados",
        "recibidos_en_mal_estado",
    ):
        metrica = _metrica(r, slug)
        assert metrica["atenuada"], slug
        assert "no depende de Cobrado/Anulado" in metrica["texto"], slug


def test_extra_dimensionados_no_depende_de_tipo(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1000, tel="3001111111", tipo=TipoPaquete.NORMAL)

    r = _zona_periodo(
        client.get("/administracion/estadisticas-cobro", params={"tipo": "NORMAL"}).text
    )
    extra = _metrica(r, "extra_dimensionados")
    assert extra["atenuada"]
    assert "no depende de Tipo" in extra["texto"]

    operador = _metrica(r, "operador_con_mas_entregas")  # Tipo SÍ lo acota
    assert not operador["atenuada"]
    assert "no depende de" not in operador["texto"]


def test_panorama_entregados_y_cancelados_no_cambian_con_filtros(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")

    r = client.get("/administracion/estadisticas-cobro").text
    inicio = r.index('aria-label="Panorama')
    fin = r.index('aria-label="Ahora')
    panorama = r[inicio:fin]
    assert "Entregados" in panorama
    assert "Cancelados" in panorama

    con_filtros = client.get(
        "/administracion/estadisticas-cobro", params={"tipo": "NORMAL", "rango": "hoy"}
    ).text
    inicio2 = con_filtros.index('aria-label="Panorama')
    fin2 = con_filtros.index('aria-label="Ahora')
    panorama_filtrado = con_filtros[inicio2:fin2]
    assert panorama == panorama_filtrado


def test_tiempos_promedio_de_panorama_se_ven_y_no_cambian_con_filtros(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")

    r = client.get("/administracion/estadisticas-cobro").text
    inicio = r.index('aria-label="Panorama')
    fin = r.index('aria-label="Ahora')
    panorama = r[inicio:fin]
    for texto in ("Tiempos promedio", "Anuncio → recepción", "Permanencia en bodega", "Bodegaje cobrado"):
        assert texto in panorama

    con_filtros = client.get(
        "/administracion/estadisticas-cobro", params={"tipo": "NORMAL", "rango": "hoy"}
    ).text
    inicio2 = con_filtros.index('aria-label="Panorama')
    fin2 = con_filtros.index('aria-label="Ahora')
    assert panorama == con_filtros[inicio2:fin2]


def test_tendencia_de_panorama_se_ve_y_no_cambia_con_filtros(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")

    r = client.get("/administracion/estadisticas-cobro").text
    inicio = r.index('aria-label="Panorama')
    fin = r.index('aria-label="Ahora')
    panorama = r[inicio:fin]
    assert "Últimos 7 días" in panorama

    con_filtros = client.get(
        "/administracion/estadisticas-cobro", params={"tipo": "NORMAL", "rango": "hoy"}
    ).text
    inicio2 = con_filtros.index('aria-label="Panorama')
    fin2 = con_filtros.index('aria-label="Ahora')
    assert panorama == con_filtros[inicio2:fin2]


def test_tendencia_sin_tramo_anterior_no_muestra_ningun_porcentaje(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")

    r = client.get("/administracion/estadisticas-cobro").text
    inicio = r.index('aria-label="Panorama')
    fin = r.index('aria-label="Ahora')
    panorama = r[inicio:fin]
    # Sin ningún cobro/entrega/cancelación "ayer" en un ambiente de pruebas
    # recién creado -- ninguna de las tres tarjetas debería mostrar "▲"/"▼".
    assert "▲" not in panorama
    assert "▼" not in panorama


def test_ahora_se_ve_y_no_cambia_con_filtros(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")

    r = client.get("/administracion/estadisticas-cobro").text
    inicio = r.index('aria-label="Ahora')
    fin = r.index('aria-label="Todo el historial')
    ahora = r[inicio:fin]
    for texto in (
        "paquetes pendientes",
        "En bodega",
        "En gracia",
        "Con bodegaje corriendo",
        "Más de 7 días",
        "Abandonados",
        "Paquete más antiguo",
        "Anuncios que nunca llegaron",
        "Clientes registrados",
    ):
        assert texto in ahora

    con_filtros = client.get(
        "/administracion/estadisticas-cobro", params={"tipo": "NORMAL", "rango": "hoy"}
    ).text
    inicio2 = con_filtros.index('aria-label="Ahora')
    fin2 = con_filtros.index('aria-label="Todo el historial')
    assert ahora == con_filtros[inicio2:fin2]


def test_dinero_de_ahora_se_ve_y_no_cambia_con_filtros(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")

    r = client.get("/administracion/estadisticas-cobro").text
    inicio = r.index('aria-label="Ahora')
    fin = r.index('aria-label="Todo el historial')
    ahora = r[inicio:fin]
    assert "Por cobrar en bodega" in ahora
    assert "Deuda contra entrega" in ahora

    con_filtros = client.get(
        "/administracion/estadisticas-cobro", params={"tipo": "NORMAL", "rango": "hoy"}
    ).text
    inicio2 = con_filtros.index('aria-label="Ahora')
    fin2 = con_filtros.index('aria-label="Todo el historial')
    assert ahora == con_filtros[inicio2:fin2]


def test_sms_de_panorama_se_ve_y_no_cambia_con_filtros(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")
    registrar_envio(client.db, TipoRegistroSms.AVISO_PAQUETE, True, proveedor="AWS_SNS")
    client.db.commit()

    r = client.get("/administracion/estadisticas-cobro").text
    inicio = r.index('aria-label="Panorama')
    fin = r.index('aria-label="Ahora')
    panorama = r[inicio:fin]
    assert "SMS enviados por AWS" in panorama
    assert "Registro desde el" in panorama

    con_filtros = client.get(
        "/administracion/estadisticas-cobro", params={"tipo": "NORMAL", "rango": "hoy"}
    ).text
    inicio2 = con_filtros.index('aria-label="Panorama')
    fin2 = con_filtros.index('aria-label="Ahora')
    assert panorama == con_filtros[inicio2:fin2]


def test_sms_de_panorama_sin_registro_no_muestra_error(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")

    r = client.get("/administracion/estadisticas-cobro").text
    inicio = r.index('aria-label="Panorama')
    fin = r.index('aria-label="Ahora')
    panorama = r[inicio:fin]
    assert "Aún sin registros" in panorama


def _registrar_sms_aws_en(client, cuando):
    # Directo, sin pasar por `registrar_envio`: necesita fijar `created_at`, y
    # `RegistroSms.id` es un UUID -- no hay forma de "recuperar la última
    # fila insertada" ordenando por id.
    client.db.add(
        RegistroSms(tipo=TipoRegistroSms.AVISO_PAQUETE, exitoso=True, proveedor="AWS_SNS", created_at=cuando)
    )
    client.db.commit()


def _tarjeta_sms_de_panorama(client):
    r = client.get("/administracion/estadisticas-cobro").text
    inicio_panorama = r.index('aria-label="Panorama')
    inicio = r.index("SMS enviados por AWS", inicio_panorama)
    return r[inicio : r.index("</article>", inicio)]


def _ayer_a_medianoche():
    """Ayer a las 00:00 (hora de Colombia) -- SIEMPRE cae dentro de "ayer hasta
    la misma hora de ahora", sea la hora que sea al correr la prueba."""
    ayer = datetime.now(ZONA_HORARIA_APP).date() - timedelta(days=1)
    return datetime.combine(ayer, time.min, tzinfo=ZONA_HORARIA_APP)


def test_sms_de_panorama_tendencia_sube_se_pinta_en_gris_no_en_verde(client):
    """Ticket 16: más o menos SMS no es "bueno" ni "malo" por sí solo -- la
    flecha es neutra (gris), a diferencia de Ingresos/Entregados/Cancelados."""
    _login_admin(client)
    _registrar_sms_aws_en(client, _ayer_a_medianoche())
    _registrar_sms_aws_en(client, datetime.now(ZONA_HORARIA_APP))
    _registrar_sms_aws_en(client, datetime.now(ZONA_HORARIA_APP))

    tarjeta = _tarjeta_sms_de_panorama(client)

    assert "▲ 100%" in tarjeta  # 2 hoy vs 1 ayer
    assert "bg-green-100" not in tarjeta
    assert "bg-red-100" not in tarjeta


def test_sms_de_panorama_tendencia_baja_tampoco_es_roja(client):
    _login_admin(client)
    _registrar_sms_aws_en(client, _ayer_a_medianoche())
    _registrar_sms_aws_en(client, _ayer_a_medianoche())
    _registrar_sms_aws_en(client, datetime.now(ZONA_HORARIA_APP))

    tarjeta = _tarjeta_sms_de_panorama(client)

    assert "▼ 50%" in tarjeta  # 1 hoy vs 2 ayer
    assert "bg-green-100" not in tarjeta
    assert "bg-red-100" not in tarjeta


def test_sms_de_panorama_minigrafico_con_menos_de_7_dias_de_registro_lo_dice(client):
    _login_admin(client)
    _registrar_sms_aws_en(client, _ayer_a_medianoche())
    _registrar_sms_aws_en(client, datetime.now(ZONA_HORARIA_APP))

    tarjeta = _tarjeta_sms_de_panorama(client)

    assert "Últimos 2 días (los que lleva el registro)" in tarjeta
    # Solo ayer y hoy, sin ceros inventados: la línea del gráfico tiene 2 puntos.
    puntos = re.search(r'<polyline points="([^"]+)"', tarjeta).group(1).split()
    assert len(puntos) == 2


def test_sms_de_panorama_sin_registro_no_muestra_minigrafico_ni_flechas(client):
    _login_admin(client)

    tarjeta = _tarjeta_sms_de_panorama(client)

    assert "Últimos" not in tarjeta
    assert "<polyline" not in tarjeta
    assert "▲" not in tarjeta
    assert "▼" not in tarjeta


def test_sms_de_periodo_desglosa_avisos_y_codigos(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")
    registrar_envio(client.db, TipoRegistroSms.AVISO_PAQUETE, True, proveedor="AWS_SNS")
    registrar_envio(client.db, TipoRegistroSms.OTP, True, proveedor="AWS_SNS")
    registrar_envio(client.db, TipoRegistroSms.AVISO_PAQUETE, False)
    client.db.commit()

    r = _zona_periodo(client.get("/administracion/estadisticas-cobro").text)
    enviados = _metrica(r, "sms_enviados_aws")["texto"]
    assert "Enviados por AWS" in enviados
    assert "1 avisos" in enviados
    assert "1 códigos de acceso" in enviados
    assert "Fallidos" in _metrica(r, "sms_fallidos")["texto"]


def test_sms_de_periodo_no_depende_de_tipo_ni_cobrado_anulado(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")
    registrar_envio(client.db, TipoRegistroSms.AVISO_PAQUETE, True, proveedor="AWS_SNS")
    client.db.commit()

    r = _zona_periodo(
        client.get(
            "/administracion/estadisticas-cobro", params={"tipo": "NORMAL", "estado_cobro": "cobrado"}
        ).text
    )

    for slug in ("sms_enviados_aws", "sms_fallidos"):
        assert "no depende de Tipo ni de Cobrado/Anulado" in _metrica(r, slug)["texto"], slug


def test_sms_costo_de_panorama_sin_configurar_ofrece_link_a_proveedores(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")
    registrar_envio(client.db, TipoRegistroSms.AVISO_PAQUETE, True, proveedor="AWS_SNS")
    client.db.commit()

    r = client.get("/administracion/estadisticas-cobro").text
    inicio = r.index('aria-label="Panorama')
    fin = r.index('aria-label="Ahora')
    panorama = r[inicio:fin]
    assert 'href="/administracion/proveedores?tab=SMS"' in panorama
    assert "Configura el costo" in panorama


def test_sms_costo_de_panorama_configurado_muestra_el_monto(client):
    from decimal import Decimal

    from app.domain.proveedor_config_service import guardar_costo_promedio_sms

    admin = _login_admin(client)
    guardar_costo_promedio_sms(client.db, "AWS_SNS", Decimal("50"))
    client.db.commit()
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")
    registrar_envio(client.db, TipoRegistroSms.AVISO_PAQUETE, True, proveedor="AWS_SNS")
    client.db.commit()

    r = client.get("/administracion/estadisticas-cobro").text
    inicio = r.index('aria-label="Panorama')
    fin = r.index('aria-label="Ahora')
    panorama = r[inicio:fin]
    assert "$50" in panorama
    assert "Configura el costo" not in panorama


def test_sms_costo_de_periodo_sin_configurar_ofrece_link_a_proveedores(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")
    registrar_envio(client.db, TipoRegistroSms.AVISO_PAQUETE, True, proveedor="AWS_SNS")
    client.db.commit()

    r = _zona_periodo(client.get("/administracion/estadisticas-cobro").text)
    assert "Costo estimado" in _metrica(r, "sms_costo_estimado")["texto"]
    assert "Costo por paquete" in _metrica(r, "sms_costo_por_paquete")["texto"]
    assert r.count('href="/administracion/proveedores?tab=SMS"') == 2


def test_sms_costo_de_periodo_configurado_calcula_estimado_y_por_paquete(client):
    from decimal import Decimal

    from app.domain.proveedor_config_service import guardar_costo_promedio_sms

    admin = _login_admin(client)
    guardar_costo_promedio_sms(client.db, "AWS_SNS", Decimal("50"))
    client.db.commit()
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")
    registrar_envio(client.db, TipoRegistroSms.AVISO_PAQUETE, True, proveedor="AWS_SNS")
    client.db.commit()

    r = _zona_periodo(client.get("/administracion/estadisticas-cobro").text)

    assert "$50" in _metrica(r, "sms_costo_estimado")["texto"]
    assert "$50" in _metrica(r, "sms_costo_por_paquete")["texto"]  # 1 SMS x $50 / 1 paquete


# --- Rediseño visual (issue 369 de .scratch/pendientes-cliente) ----------------- #


def _seccion(html, desde, hasta=None):
    inicio = html.index(f'aria-label="{desde}')
    return html[inicio : html.index(f'aria-label="{hasta}', inicio)] if hasta else html[inicio:]


def test_cada_zona_tiene_su_titulo_visible_y_ya_no_habla_de_filtros(client):
    """Cada zona tiene título y subtítulo visibles. Sin barra de filtros (issue 399),
    ninguna zona habla de filtros, y "Todo el historial" pasa a "Todo el historial"."""
    _login_admin(client)

    r = client.get("/administracion/estadisticas-cobro").text

    for titulo, aclaracion in (
        ("Panorama", "Hoy, esta semana y este mes"),
        ("Ahora", "Foto del momento"),
        ("Todo el historial", "Todos los datos registrados"),
    ):
        assert re.search(rf"<h2[^>]*>.*?{titulo}.*?</h2>", r, re.S), titulo
        assert aclaracion in r, aclaracion
    assert "filtros" not in r.lower()
    assert "Periodo seleccionado" not in r


def test_ninguna_cifra_del_panorama_se_corta_con_puntos_suspensivos(client):
    """Queja original: `$110,…` en Ingresos y `Anuncio…` en Tiempos promedio -- una cifra
    cortada es un dato perdido. Ningún elemento del Panorama debe truncar su texto."""
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1234567, tel="3001111111")

    r = client.get("/administracion/estadisticas-cobro").text

    assert "truncate" not in _seccion(r, "Panorama", "Ahora")
    assert "1,234,567" in _seccion(r, "Panorama", "Ahora")


def test_todo_dato_atenuado_dice_por_que_y_solo_esos(client):
    """Regla de la spec (matriz de "no aplica"): un dato que el filtro activo no acota se
    atenúa Y lo explica. Un dato atenuado sin explicación parece roto; una explicación
    sin atenuar, contradictoria. Se verifica para TODOS los datos y todas las
    combinaciones de filtros, no dato por dato."""
    admin = _login_admin(client)
    crear_motivo_anulacion(client.db, "Reclamo")
    client.db.commit()
    _entregar_con_cobro(client, admin, 1000, tel="3001111111")

    combinaciones = [
        {},
        {"tipo": "NORMAL"},
        {"estado_cobro": "cobrado"},
        {"tipo": "NORMAL", "estado_cobro": "anulado"},
    ]
    for params in combinaciones:
        metricas = _metricas(client.get("/administracion/estadisticas-cobro", params=params).text)
        assert len(metricas) >= 30, params  # que el extractor no se haya quedado sin nada
        for slug, m in metricas.items():
            assert m["atenuada"] == ("no depende de" in m["texto"]), (params, slug)
        if params:
            assert any(m["atenuada"] for m in metricas.values()), params
        else:
            assert not any(m["atenuada"] for m in metricas.values())


def test_deuda_contra_entrega_lleva_el_signo_antes_del_peso(client):
    """`$-5,000` (el signo pegado a la cifra) leía como un error de formato; es
    `-$5,000`. La deuda contra entrega es negativa por definición."""
    from app.domain.persona import Persona
    from app.domain.saldo_contra_entrega_service import registrar_movimiento_saldo

    admin = _login_admin(client)
    persona = Persona(nombre="Con deuda", telefono="3002222222")
    client.db.add(persona)
    client.db.commit()
    registrar_movimiento_saldo(client.db, persona.id, -5000, admin)
    client.db.commit()

    ahora = _seccion(client.get("/administracion/estadisticas-cobro").text, "Ahora", "Todo el historial")

    assert "-$5,000" in ahora
    assert "$-5,000" not in ahora
