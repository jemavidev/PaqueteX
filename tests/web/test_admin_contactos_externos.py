# -*- coding: utf-8 -*-
"""
Capa web — `/administracion/contactos-externos` (`.scratch/contactos-externos`
+ `.scratch/contactos-externos-import-export`). Buscador + paginación,
import/export CSV, exclusiva de admin.
"""

import re

from app.domain.contacto_externo import ContactoExterno, FuenteContactoExterno
from app.domain.contacto_externo_service import (
    FUENTE_GOOGLE_CONTACTS,
    FilaFuenteContacto,
    importar_contactos_externos,
)
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

_PW = "Contrasena1"


def _login_admin(client, email="admin@club.com"):
    create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _sembrar(client, filas):
    importar_contactos_externos(client.db, filas)
    client.db.commit()


def test_sin_sesion_redirige_a_login(client):
    r = client.get("/administracion/contactos-externos", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_operador_es_rechazado_403(client):
    _login_operador(client)
    r = client.get("/administracion/contactos-externos")
    assert r.status_code == 403


def test_admin_lista_los_contactos(client):
    _login_admin(client)
    _sembrar(
        client,
        [FilaFuenteContacto(nombre="Juan Perez", telefonos=("3001234567",), fuente=FUENTE_GOOGLE_CONTACTS)],
    )

    r = client.get("/administracion/contactos-externos")
    assert r.status_code == 200
    assert "Juan Perez" in r.text


def test_buscar_por_nombre_encuentra_el_contacto(client):
    _login_admin(client)
    _sembrar(
        client,
        [
            FilaFuenteContacto(nombre="Juan Perez", telefonos=("3001111111",), fuente=FUENTE_GOOGLE_CONTACTS),
            FilaFuenteContacto(nombre="Ana Gomez", telefonos=("3002222222",), fuente=FUENTE_GOOGLE_CONTACTS),
        ],
    )

    r = client.get("/administracion/contactos-externos", params={"q": "Juan"})
    assert "Juan Perez" in r.text
    assert "Ana Gomez" not in r.text


def test_buscar_por_telefono_en_cualquier_formato_encuentra_el_contacto(client):
    _login_admin(client)
    _sembrar(
        client,
        [FilaFuenteContacto(nombre="Juan Perez", telefonos=("3001234567",), fuente=FUENTE_GOOGLE_CONTACTS)],
    )

    r = client.get("/administracion/contactos-externos", params={"q": "+57 300 123 4567"})
    assert "Juan Perez" in r.text


def test_buscar_por_usuario_de_whatsapp_encuentra_el_contacto(client):
    # Issue 356 (.scratch/pendientes-cliente): el buscador cubre Nombre,
    # Teléfono Y usuario de WhatsApp -- este contacto no se llama como su
    # usuario ni tiene teléfono en la búsqueda, solo el WhatsApp lo une.
    _login_admin(client)
    _sembrar(
        client,
        [
            FilaFuenteContacto(
                nombre="Ana Gomez", telefonos=("3002222222",), whatsapps=("agomez.wa",), fuente=FUENTE_GOOGLE_CONTACTS
            ),
            FilaFuenteContacto(nombre="Juan Perez", telefonos=("3001111111",), fuente=FUENTE_GOOGLE_CONTACTS),
        ],
    )

    r = client.get("/administracion/contactos-externos", params={"q": "agomez.wa"})
    assert "Ana Gomez" in r.text
    assert "Juan Perez" not in r.text


def test_buscar_por_whatsapp_es_parcial_y_tolera_arroba_y_mayusculas(client):
    _login_admin(client)
    _sembrar(
        client,
        [
            FilaFuenteContacto(
                nombre="Ana Gomez", telefonos=(), whatsapps=("agomez.wa",), fuente=FUENTE_GOOGLE_CONTACTS
            ),
        ],
    )

    for termino in ("gomez.w", "@AGomez", "  agomez.wa  "):
        r = client.get("/administracion/contactos-externos", params={"q": termino})
        assert "Ana Gomez" in r.text, termino


def test_buscar_por_whatsapp_no_trata_el_guion_bajo_como_comodin(client):
    # `_` es válido en un usuario de WhatsApp pero en LIKE es un comodín de
    # un carácter -- buscar "ana_x" no debe traer también a "anazx".
    _login_admin(client)
    _sembrar(
        client,
        [
            FilaFuenteContacto(nombre="Contacto Uno", telefonos=(), whatsapps=("ana_x",), fuente=FUENTE_GOOGLE_CONTACTS),
            FilaFuenteContacto(nombre="Contacto Dos", telefonos=(), whatsapps=("anazx",), fuente=FUENTE_GOOGLE_CONTACTS),
        ],
    )

    r = client.get("/administracion/contactos-externos", params={"q": "ana_x"})
    assert "Contacto Uno" in r.text
    assert "Contacto Dos" not in r.text


def test_buscar_por_nombre_sigue_funcionando_con_whatsapp_en_juego(client):
    # El OR con WhatsApp no le quita nada a la búsqueda por nombre.
    _login_admin(client)
    _sembrar(
        client,
        [
            FilaFuenteContacto(
                nombre="Juan Perez", telefonos=("3001111111",), whatsapps=("jp.wa",), fuente=FUENTE_GOOGLE_CONTACTS
            ),
            FilaFuenteContacto(nombre="Ana Gomez", telefonos=("3002222222",), fuente=FUENTE_GOOGLE_CONTACTS),
        ],
    )

    r = client.get("/administracion/contactos-externos", params={"q": "Perez"})
    assert "Juan Perez" in r.text
    assert "Ana Gomez" not in r.text


def test_sin_whatsapp_no_muestra_columna_forzada(client):
    _login_admin(client)
    _sembrar(
        client,
        [FilaFuenteContacto(nombre="Juan Perez", telefonos=("3001234567",), fuente=FUENTE_GOOGLE_CONTACTS)],
    )

    r = client.get("/administracion/contactos-externos")
    assert "None" not in r.text


def test_con_whatsapp_muestra_el_usuario(client):
    _login_admin(client)
    _sembrar(
        client,
        [
            FilaFuenteContacto(
                nombre="Ana Gomez", telefonos=(), whatsapps=("ana.whatsapp",), fuente=FUENTE_GOOGLE_CONTACTS
            )
        ],
    )

    r = client.get("/administracion/contactos-externos")
    assert "ana.whatsapp" in r.text


def test_muestra_el_total_de_contactos(client):
    _login_admin(client)
    _sembrar(
        client,
        [
            FilaFuenteContacto(nombre="Juan Perez", telefonos=("3001111111",), fuente=FUENTE_GOOGLE_CONTACTS),
            FilaFuenteContacto(nombre="Ana Gomez", telefonos=("3002222222",), fuente=FUENTE_GOOGLE_CONTACTS),
        ],
    )
    r = client.get("/administracion/contactos-externos")
    assert "2 contactos" in r.text


def test_muestra_el_total_aunque_quepan_en_una_sola_pagina(client):
    # `paginacion()` no renderiza nada con 1 sola página -- el total tiene
    # que seguir viéndose igual (issue 338, .scratch/pendientes-cliente).
    _login_admin(client)
    _sembrar(client, [FilaFuenteContacto(nombre="Juan Perez", telefonos=("3001111111",), fuente=FUENTE_GOOGLE_CONTACTS)])
    r = client.get("/administracion/contactos-externos")
    assert "1 contacto" in r.text
    assert "1 contactos" not in r.text


def _sembrar_n(client, n):
    _sembrar(
        client,
        [
            FilaFuenteContacto(nombre=f"Contacto {i:03d}", telefonos=(f"300{1000000 + i}",), fuente=FUENTE_GOOGLE_CONTACTS)
            for i in range(n)
        ],
    )


def test_con_varias_paginas_el_total_va_en_una_pildora_dentro_de_la_barra(client):
    # Issue 357 (.scratch/pendientes-cliente): el total es una píldora pegada
    # a la derecha de "Página X de Y" en la barra de paginación (desktop).
    _login_admin(client)
    _sembrar_n(client, 21)  # 20 por página -> 2 páginas

    r = client.get("/administracion/contactos-externos")
    assert re.search(
        r"Página <strong[^>]*>1</strong> de 2</span>\s*<span[^>]*rounded-full[^>]*>21 contactos</span>", r.text
    )


def test_con_varias_paginas_la_pildora_suelta_solo_se_ve_en_mobile(client):
    # La barra desktop no existe en mobile -- ahí el total sigue visible como
    # la misma píldora en su propia fila, escondida desde `md` (donde ya está
    # dentro de la barra).
    _login_admin(client)
    _sembrar_n(client, 21)

    r = client.get("/administracion/contactos-externos")
    assert re.search(
        r'<div class="flex justify-end[^"]*md:hidden">\s*<span[^>]*rounded-full[^>]*>21 contactos</span>', r.text
    )


def test_con_una_sola_pagina_la_pildora_se_ve_en_todos_los_tamanos(client):
    # Sin barra de paginación que la contenga (`total_paginas <= 1`) la píldora
    # va sola y NO lleva `md:hidden`.
    _login_admin(client)
    _sembrar_n(client, 3)

    r = client.get("/administracion/contactos-externos")
    assert re.search(
        r'<div class="flex justify-end[^"]*">\s*<span[^>]*rounded-full[^>]*>3 contactos</span>', r.text
    )
    assert not re.search(r'<div class="flex justify-end[^"]*md:hidden"', r.text)


def test_el_total_ya_no_es_texto_suelto(client):
    _login_admin(client)
    _sembrar_n(client, 3)

    r = client.get("/administracion/contactos-externos")
    assert '<p class="text-sm text-slate-600 mt-1 mb-1">' not in r.text


def test_con_busqueda_la_pildora_dice_encontrados(client):
    _login_admin(client)
    _sembrar_n(client, 3)

    r = client.get("/administracion/contactos-externos", params={"q": "Contacto 001"})
    assert "1 contacto encontrado</span>" in r.text


def test_la_barra_de_paginacion_de_otras_vistas_no_lleva_pildora(client):
    # `pildora` es opcional en `paginacion()` -- sin él la salida no cambia
    # (`/paquetes` y `/residentes` no pasan nada).
    from app.web.templating import templates

    macro = templates.get_template("components/_paginacion.html").module.paginacion
    html = str(macro(1, 3, "/residentes"))
    assert "Página" in html
    assert "rounded-full border border-slate-300 bg-slate-100" not in html


def test_peticion_en_vivo_devuelve_solo_el_fragmento(client):
    # Búsqueda en vivo (mismo mecanismo que /paquetes y /residentes, ver
    # test_customers_manage.py): el fetch de `_busqueda_filtros.html` marca
    # su petición con X-Requested-With: fetch, la ruta responde SOLO
    # tabla+paginación (sin el layout de la página completa).
    _login_admin(client)
    _sembrar(
        client,
        [FilaFuenteContacto(nombre="Juan Perez", telefonos=("3001234567",), fuente=FUENTE_GOOGLE_CONTACTS)],
    )

    normal = client.get("/administracion/contactos-externos")
    assert normal.status_code == 200
    assert "<h1" in normal.text
    assert "Juan Perez" in normal.text

    fragmento = client.get(
        "/administracion/contactos-externos", headers={"X-Requested-With": "fetch"}
    )
    assert fragmento.status_code == 200
    assert "<h1" not in fragmento.text
    assert "<html" not in fragmento.text
    assert "Juan Perez" in fragmento.text


# --- Botones Descargar/Exportar/Importar (issues 358 y 359) ---


def test_botones_de_plantilla_tienen_las_etiquetas_cortas(client):
    _login_admin(client)
    r = client.get("/administracion/contactos-externos")

    assert re.search(r'href="/administracion/contactos-externos/plantilla"[^>]*>Descargar</a>', r.text)
    assert re.search(r'href="/administracion/contactos-externos/exportar"[^>]*>Exportar</a>', r.text)
    assert re.search(r"data-import-toggle[^>]*>Importar</button>", r.text)
    # Las etiquetas largas de antes ya no se ven como texto del botón (sí
    # quedan como tooltip, en `title`).
    assert ">Descargar plantilla</a>" not in r.text
    assert ">Exportar CSV</a>" not in r.text
    assert ">Importar CSV</button>" not in r.text


def test_botones_de_plantilla_conservan_el_nombre_largo_como_tooltip(client):
    _login_admin(client)
    r = client.get("/administracion/contactos-externos")

    assert 'title="Descargar plantilla CSV"' in r.text
    assert 'title="Exportar contactos a CSV"' in r.text
    assert 'title="Importar contactos desde CSV"' in r.text


def test_botones_de_plantilla_y_formulario_de_import_se_ocultan_en_mobile(client):
    # Issue 359: el sistema de plantillas no se usa en mobile (`< md`) --
    # la fila de botones y el formulario que despliega "Importar" quedan
    # `hidden` bajo `md`.
    _login_admin(client)
    r = client.get("/administracion/contactos-externos")

    assert re.search(
        r'<div class="hidden md:flex[^"]*">\s*<a href="/administracion/contactos-externos/plantilla"', r.text
    )
    assert re.search(r'<div class="hidden md:block">\s*<form[^>]*data-import-form', r.text)


def test_placeholder_del_buscador_menciona_whatsapp(client):
    _login_admin(client)
    r = client.get("/administracion/contactos-externos")
    assert 'placeholder="Nombre, teléfono o WhatsApp"' in r.text


# --- WhatsApp en píldora bajo el nombre, mobile (issue 360) ---


def _filas_de_la_tabla(html):
    return re.findall(r'<tr class="hover:bg-slate-50">(.*?)</tr>', html, re.DOTALL)


def test_mobile_el_whatsapp_baja_a_una_pildora_bajo_el_nombre(client):
    _login_admin(client)
    _sembrar(
        client,
        [
            FilaFuenteContacto(
                nombre="Ana Gomez", telefonos=("3002222222",), whatsapps=("agomez.wa",), fuente=FUENTE_GOOGLE_CONTACTS
            )
        ],
    )

    r = client.get("/administracion/contactos-externos")
    (fila,) = _filas_de_la_tabla(r.text)
    celdas = fila.split("<td")

    # celda 1 = Nombre (+ píldora solo mobile), celda 2 = Teléfono(s)
    assert "Ana Gomez" in celdas[1]
    assert re.search(r'class="[^"]*sm:hidden"', celdas[1])
    assert "agomez.wa" in celdas[1]
    assert "agomez.wa" not in celdas[2]
    assert "3002222222" in celdas[2]


def test_mobile_un_contacto_con_dos_whatsapps_tiene_dos_pildoras(client):
    _login_admin(client)
    _sembrar(
        client,
        [
            FilaFuenteContacto(
                nombre="Ana Gomez",
                telefonos=(),
                whatsapps=("agomez.wa", "ana.otro"),
                fuente=FUENTE_GOOGLE_CONTACTS,
            )
        ],
    )

    r = client.get("/administracion/contactos-externos")
    (fila,) = _filas_de_la_tabla(r.text)
    nombre_y_pildoras = fila.split("<td")[1]
    assert nombre_y_pildoras.count('title="WhatsApp"') == 2
    assert "agomez.wa" in nombre_y_pildoras
    assert "ana.otro" in nombre_y_pildoras


def test_mobile_sin_whatsapp_no_dibuja_pildora(client):
    _login_admin(client)
    _sembrar(
        client,
        [FilaFuenteContacto(nombre="Juan Perez", telefonos=("3001234567",), fuente=FUENTE_GOOGLE_CONTACTS)],
    )

    r = client.get("/administracion/contactos-externos")
    assert 'title="WhatsApp"' not in r.text


def test_mobile_la_pildora_de_whatsapp_usa_la_receta_de_las_otras_vistas(client):
    # Issue 360 ronda 2: mismo tamaño/forma que las píldoras mobile de
    # `/residentes` y `/paquetes` (`px-3 py-1.5 text-sm font-semibold`,
    # `rounded-full`, verde de "Auto"), solo texto -- sin ícono.
    _login_admin(client)
    _sembrar(
        client,
        [
            FilaFuenteContacto(
                nombre="Ana Gomez", telefonos=(), whatsapps=("agomez.wa",), fuente=FUENTE_GOOGLE_CONTACTS
            )
        ],
    )

    r = client.get("/administracion/contactos-externos")
    (fila,) = _filas_de_la_tabla(r.text)
    celda_nombre = fila.split("<td")[1]
    (clases,) = re.findall(r'<span class="([^"]*)" title="WhatsApp">', celda_nombre)
    for clase in (
        "rounded-full",
        "px-3",
        "py-1.5",
        "text-sm",
        "font-semibold",
        "bg-emerald-100",
        "text-emerald-800",
        "border-emerald-300",
    ):
        assert clase in clases.split(), clase
    assert "text-xs" not in clases.split()
    assert "<svg" not in celda_nombre


def test_la_pildora_del_total_es_text_sm_y_del_alto_de_los_botones(client):
    # Issue 357 ronda 2 ("un poco más grande": `text-sm`, antes `text-xs`) y
    # ronda 3 ("ajustada al contenido que tiene a los lados": `h-8`, la
    # MISMA altura que los botones Anterior/Siguiente que la flanquean).
    _login_admin(client)
    _sembrar_n(client, 21)

    r = client.get("/administracion/contactos-externos")
    clases = re.findall(r'<span class="([^"]*)">21 contactos</span>', r.text)
    assert len(clases) == 2  # la de la barra y la suelta de mobile
    for c in clases:
        partes = c.split()
        assert "text-sm" in partes
        assert "h-8" in partes
        assert "justify-center" in partes  # texto centrado en la píldora
        assert "text-xs" not in partes
        assert "py-1" not in partes  # el alto ya no lo da el relleno


def test_en_la_barra_el_texto_de_pagina_y_la_pildora_comparten_eje_vertical(client):
    # Issue 357 ronda 3 ("centrada"): el centro de la barra es una fila flex
    # `items-center` -- antes la píldora iba inline con `align-middle`, que
    # alinea contra la línea base del texto y la dejaba 1.3px más abajo.
    _login_admin(client)
    _sembrar_n(client, 21)

    r = client.get("/administracion/contactos-externos")
    m = re.search(
        r'<span class="([^"]*)">\s*<span>Página <strong[^>]*>1</strong> de 2</span>\s*<span class="([^"]*)">21 contactos</span>',
        r.text,
    )
    assert m
    fila, pildora = m.group(1).split(), m.group(2).split()
    assert "flex" in fila and "items-center" in fila
    assert "align-middle" not in pildora


def test_la_columna_whatsapp_de_desktop_sigue_estando(client):
    # La píldora es solo mobile (`sm:hidden`); desde `sm` sigue la columna.
    _login_admin(client)
    _sembrar(
        client,
        [
            FilaFuenteContacto(
                nombre="Ana Gomez", telefonos=(), whatsapps=("agomez.wa",), fuente=FUENTE_GOOGLE_CONTACTS
            )
        ],
    )

    r = client.get("/administracion/contactos-externos")
    assert '<th class="hidden sm:table-cell px-4 py-2.5 text-left font-semibold">WhatsApp</th>' in r.text
    (fila,) = _filas_de_la_tabla(r.text)
    assert "agomez.wa" in fila.split("<td")[3]


# --- Fuentes numeradas (issue 362) ---

_URL = "/administracion/contactos-externos"


def _sembrar_tres_fuentes(client):
    """Las tres fuentes reales del cliente, importadas en este orden: 01
    Whatsapp, 02 Paquetes, 03 ACTUALIZACION -- todas para el mismo contacto,
    como en los datos reales (las guarda ordenadas por nombre)."""
    for fuente in ("Whatsapp", "Paquetes", "ACTUALIZACION"):
        _sembrar(
            client,
            [FilaFuenteContacto(nombre="Ana Gomez", telefonos=("3002222222",), fuente=fuente)],
        )


def _importar_csv(client, fuente, otra=None, fila="Juan Perez,3001234567,"):
    data = {"fuente": fuente}
    if otra is not None:
        data["fuente_otra"] = otra
    return client.post(
        f"{_URL}/importar",
        data=data,
        files={"archivo": ("c.csv", f"Nombre,Teléfonos,WhatsApp\n{fila}\n".encode("utf-8"), "text/csv")},
    )


def _texto(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_la_columna_fuentes_muestra_los_numeros_de_menor_a_mayor_con_tooltip(client):
    _login_admin(client)
    _sembrar_tres_fuentes(client)

    r = client.get(_URL)
    (fila,) = _filas_de_la_tabla(r.text)
    celda_fuentes = fila.split("<td")[4]  # Nombre, Teléfono(s), WhatsApp, Fuentes

    # Guardadas por nombre (ACTUALIZACION, Paquetes, Whatsapp), mostradas por
    # antigüedad -- con el nombre completo como tooltip de cada número.
    assert re.search(
        r'<span title="Whatsapp">01</span>\s*·\s*<span title="Paquetes">02</span>'
        r'\s*·\s*<span title="ACTUALIZACION">03</span>',
        celda_fuentes,
    )


def test_entre_sm_y_md_la_columna_fuentes_sigue_mostrando_los_nombres(client):
    # La numeración es solo desktop (`md`); antes de eso queda el texto de
    # siempre (issue 362: "solo debe funcionar para la vista desde el desktop").
    _login_admin(client)
    _sembrar_tres_fuentes(client)

    r = client.get(_URL)
    (fila,) = _filas_de_la_tabla(r.text)
    celda_fuentes = fila.split("<td")[4]

    m = re.search(r'<span class="md:hidden">(.*?)</span>', celda_fuentes, re.DOTALL)
    assert m and _texto(m.group(1)) == "ACTUALIZACION · Paquetes · Whatsapp"
    assert re.search(r'<span class="hidden md:inline">\s*<span title=', celda_fuentes)


def test_la_leyenda_lista_todas_las_fuentes_con_su_numero(client):
    _login_admin(client)
    _sembrar_tres_fuentes(client)

    r = client.get(_URL)

    m = re.search(r'<div class="hidden md:flex[^"]*" data-leyenda-fuentes>(.*?)</div>', r.text, re.DOTALL)
    assert m, "la leyenda es solo desktop (`hidden md:flex`)"
    assert _texto(m.group(1)) == "Fuentes: 01 Whatsapp · 02 Paquetes · 03 ACTUALIZACION"


def test_la_leyenda_lista_las_fuentes_aunque_la_busqueda_no_las_use(client):
    # Es la equivalencia completa, no solo lo que hay en la página actual.
    _login_admin(client)
    _sembrar_tres_fuentes(client)
    _sembrar(client, [FilaFuenteContacto(nombre="Otro Contacto", telefonos=("3003333333",), fuente="Cuarta")])

    r = client.get(_URL, params={"q": "Otro"})

    m = re.search(r'data-leyenda-fuentes>(.*?)</div>', r.text, re.DOTALL)
    assert _texto(m.group(1)) == "Fuentes: 01 Whatsapp · 02 Paquetes · 03 ACTUALIZACION · 04 Cuarta"


def test_sin_fuentes_registradas_no_hay_leyenda(client):
    _login_admin(client)
    r = client.get(_URL)
    assert "data-leyenda-fuentes" not in r.text


def test_la_peticion_en_vivo_trae_los_numeros_pero_no_repite_la_leyenda(client):
    _login_admin(client)
    _sembrar_tres_fuentes(client)

    r = client.get(_URL, headers={"X-Requested-With": "fetch"})

    assert '<span title="Whatsapp">01</span>' in r.text
    assert "data-leyenda-fuentes" not in r.text


def test_el_select_del_import_muestra_nombre_guion_numero_en_orden(client):
    _login_admin(client)
    _sembrar_tres_fuentes(client)

    r = client.get(_URL)

    opciones = re.findall(r'<option value="([^"]*)">([^<]*)</option>', r.text)
    assert opciones == [
        ("Whatsapp", "Whatsapp - 01"),
        ("Paquetes", "Paquetes - 02"),
        ("ACTUALIZACION", "ACTUALIZACION - 03"),
        ("__otra__", "Otra (especificar)…"),
    ]


def test_una_fuente_fuera_del_catalogo_se_muestra_tal_cual_sin_numero(client):
    _login_admin(client)
    client.db.add(ContactoExterno(nombre="Ana Gomez", fuentes=["Rara"]))
    client.db.commit()

    r = client.get(_URL)
    (fila,) = _filas_de_la_tabla(r.text)
    celda_fuentes = fila.split("<td")[4]

    m = re.search(r'<span class="hidden md:inline">(.*?)</span>\s*</td>', celda_fuentes, re.DOTALL)
    assert m and _texto(m.group(1)) == "Rara"


def test_importar_una_fuente_nueva_le_asigna_el_siguiente_numero(client):
    _login_admin(client)
    _sembrar(client, [FilaFuenteContacto(nombre="Ana Gomez", telefonos=("3002222222",), fuente="Whatsapp")])

    r = _importar_csv(client, "__otra__", otra="Facebook")

    assert r.status_code == 200
    assert '<option value="Facebook">Facebook - 02</option>' in r.text
    client.db.expire_all()
    assert [(f.numero, f.nombre) for f in client.db.query(FuenteContactoExterno).order_by(FuenteContactoExterno.numero)] == [
        (1, "Whatsapp"),
        (2, "Facebook"),
    ]


def test_sin_datos_la_primera_fuente_importada_es_la_01(client):
    _login_admin(client)

    r = _importar_csv(client, "__otra__", otra="Mi fuente")

    assert '<option value="Mi fuente">Mi fuente - 01</option>' in r.text
    assert '<span title="Mi fuente">01</span>' in r.text


def test_importar_con_otra_grafia_de_una_fuente_existente_no_crea_otra(client):
    _login_admin(client)
    _sembrar(client, [FilaFuenteContacto(nombre="Ana Gomez", telefonos=("3002222222",), fuente="Whatsapp")])

    _importar_csv(client, "__otra__", otra="  whatsapp ")

    client.db.expire_all()
    assert client.db.query(FuenteContactoExterno).count() == 1
    nuevo = client.db.query(ContactoExterno).filter_by(nombre="Juan Perez").one()
    assert nuevo.fuentes == ["Whatsapp"]


def test_importar_una_fuente_de_mas_de_40_caracteres_se_rechaza_sin_tocar_la_base(client):
    _login_admin(client)

    r = _importar_csv(client, "__otra__", otra="x" * 41)

    assert r.status_code == 200
    assert "no puede pasar de 40 caracteres" in r.text
    client.db.expire_all()
    assert client.db.query(ContactoExterno).count() == 0
    assert client.db.query(FuenteContactoExterno).count() == 0


# --- Plantilla (.scratch/contactos-externos-import-export) ---


def test_plantilla_requiere_admin(client):
    _login_operador(client)
    r = client.get("/administracion/contactos-externos/plantilla")
    assert r.status_code == 403


def test_plantilla_trae_solo_el_encabezado(client):
    _login_admin(client)
    r = client.get("/administracion/contactos-externos/plantilla")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    # Issue 381: BOM UTF-8 al inicio -- sin él, Excel en Windows abre el archivo como ANSI y rompe las tildes.
    assert r.content.startswith(b"\xef\xbb\xbf")
    assert "charset=utf-8" in r.headers["content-type"]
    assert r.text.lstrip("\ufeff").strip() == "Nombre,Teléfonos,WhatsApp"


# --- Exportar ---


def test_exportar_requiere_admin(client):
    _login_operador(client)
    r = client.get("/administracion/contactos-externos/exportar")
    assert r.status_code == 403


def test_exportar_trae_todos_los_contactos_con_las_columnas_de_la_plantilla(client):
    _login_admin(client)
    _sembrar(
        client,
        [
            FilaFuenteContacto(
                nombre="Juan Perez", telefonos=("3001234567",), whatsapps=("juan.whatsapp",), fuente=FUENTE_GOOGLE_CONTACTS
            )
        ],
    )

    r = client.get("/administracion/contactos-externos/exportar")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf")  # issue 381
    lineas = r.text.lstrip("\ufeff").strip().splitlines()
    assert lineas[0] == "Nombre,Teléfonos,WhatsApp"
    assert lineas[1] == "Juan Perez,+573001234567,juan.whatsapp"


def test_exportar_ignora_cualquier_busqueda_activa(client):
    # El export siempre trae TODO -- no toma `q`, a diferencia del listado.
    _login_admin(client)
    _sembrar(
        client,
        [
            FilaFuenteContacto(nombre="Juan Perez", telefonos=("3001111111",), fuente=FUENTE_GOOGLE_CONTACTS),
            FilaFuenteContacto(nombre="Ana Gomez", telefonos=("3002222222",), fuente=FUENTE_GOOGLE_CONTACTS),
        ],
    )

    r = client.get("/administracion/contactos-externos/exportar", params={"q": "Juan"})
    assert "Juan Perez" in r.text
    assert "Ana Gomez" in r.text


# --- Importar ---


def test_importar_requiere_admin(client):
    _login_operador(client)
    r = client.post(
        "/administracion/contactos-externos/importar",
        data={"fuente": "mi_fuente"},
        files={"archivo": ("contactos.csv", b"Nombre,Telefonos,WhatsApp\n", "text/csv")},
    )
    assert r.status_code == 403


def test_importar_csv_valido_crea_contacto_y_muestra_el_resumen(client):
    _login_admin(client)
    csv_bytes = "Nombre,Teléfonos,WhatsApp\nJuan Perez,3001234567,\n".encode("utf-8")

    r = client.post(
        "/administracion/contactos-externos/importar",
        data={"fuente": "__otra__", "fuente_otra": "whatsapp_business"},
        files={"archivo": ("contactos.csv", csv_bytes, "text/csv")},
    )
    assert r.status_code == 200
    assert "1 creado" in r.text
    assert "Juan Perez" in r.text

    client.db.expire_all()
    contacto = client.db.query(ContactoExterno).one()
    assert "whatsapp_business" in contacto.fuentes


def test_importar_archivo_con_columnas_que_no_calzan_se_rechaza(client):
    _login_admin(client)
    csv_bytes = "Nombre,Telefono\nJuan Perez,3001234567\n".encode("utf-8")  # columnas incorrectas

    r = client.post(
        "/administracion/contactos-externos/importar",
        data={"fuente": "__otra__", "fuente_otra": "mi_fuente"},
        files={"archivo": ("contactos.csv", csv_bytes, "text/csv")},
    )
    assert r.status_code == 200
    assert "no tiene las columnas" in r.text
    assert client.db.query(ContactoExterno).count() == 0


def test_importar_sin_fuente_se_rechaza_sin_tocar_la_base(client):
    _login_admin(client)
    csv_bytes = "Nombre,Teléfonos,WhatsApp\nJuan Perez,3001234567,\n".encode("utf-8")

    r = client.post(
        "/administracion/contactos-externos/importar",
        data={"fuente": "__otra__", "fuente_otra": "  "},
        files={"archivo": ("contactos.csv", csv_bytes, "text/csv")},
    )
    assert r.status_code == 200
    assert "Elegí o escribí una fuente" in r.text
    assert client.db.query(ContactoExterno).count() == 0


def test_reimportar_el_mismo_archivo_no_duplica(client):
    _login_admin(client)
    csv_bytes = "Nombre,Teléfonos,WhatsApp\nJuan Perez,3001234567,\n".encode("utf-8")

    for _ in range(2):
        client.post(
            "/administracion/contactos-externos/importar",
            data={"fuente": "__otra__", "fuente_otra": "mi_fuente"},
            files={"archivo": ("contactos.csv", csv_bytes, "text/csv")},
        )

    client.db.expire_all()
    assert client.db.query(ContactoExterno).count() == 1


def test_exportar_e_importar_de_nuevo_es_round_trip(client):
    """El CSV exportado se puede volver a subir tal cual sin editarlo --
    decisión explícita del cliente (grilling, pregunta 8)."""
    _login_admin(client)
    _sembrar(
        client,
        [
            FilaFuenteContacto(
                nombre="Juan Perez", telefonos=("3001234567",), whatsapps=("juan.whatsapp",), fuente=FUENTE_GOOGLE_CONTACTS
            )
        ],
    )

    exportado = client.get("/administracion/contactos-externos/exportar")
    r = client.post(
        "/administracion/contactos-externos/importar",
        data={"fuente": "__otra__", "fuente_otra": "reimport"},
        files={"archivo": ("contactos.csv", exportado.content, "text/csv")},
    )
    assert r.status_code == 200
    assert "no tiene las columnas" not in r.text

    client.db.expire_all()
    assert client.db.query(ContactoExterno).count() == 1  # no duplicó


# --- Issue 381 (.scratch/pendientes-cliente): CSV y Excel ---


def test_exportar_neutraliza_un_nombre_que_parece_formula(client):
    """Excel ejecutaría `=...` como fórmula al abrir el archivo: se antepone un apóstrofo."""
    _login_admin(client)
    _sembrar(
        client,
        [FilaFuenteContacto(nombre="=HYPERLINK(1)", telefonos=("3001234567",), fuente=FUENTE_GOOGLE_CONTACTS)],
    )

    r = client.get("/administracion/contactos-externos/exportar")

    assert "'=HYPERLINK(1)" in r.text
    assert "+573001234567" in r.text  # los teléfonos no se tocan


def test_reimportar_un_nombre_neutralizado_no_guarda_el_apostrofo(client):
    _login_admin(client)
    csv_texto = "Nombre,Teléfonos,WhatsApp\n'=Raro,3001234567,\n"

    client.post(
        "/administracion/contactos-externos/importar",
        data={"fuente": "__otra__", "fuente_otra": "excel"},
        files={"archivo": ("contactos.csv", csv_texto.encode("utf-8"), "text/csv")},
    )

    client.db.expire_all()
    assert client.db.query(ContactoExterno).one().nombre.upper() == "=RARO"


def test_importar_acepta_un_csv_guardado_por_excel_en_ansi_y_con_punto_y_coma(client):
    """Excel en Windows (configuración regional de Colombia) guarda "CSV" en ANSI y separado por `;`."""
    _login_admin(client)
    csv_texto = "Nombre;Teléfonos;WhatsApp\r\nJosé Muñoz;3001234567;\r\n"

    r = client.post(
        "/administracion/contactos-externos/importar",
        data={"fuente": "__otra__", "fuente_otra": "excel"},
        files={"archivo": ("contactos.csv", csv_texto.encode("cp1252"), "text/csv")},
    )

    assert "no tiene las columnas" not in r.text
    assert "no es un CSV" not in r.text
    client.db.expire_all()
    assert client.db.query(ContactoExterno).one().nombre.upper() == "JOSÉ MUÑOZ"
