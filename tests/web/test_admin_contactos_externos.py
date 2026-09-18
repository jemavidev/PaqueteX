# -*- coding: utf-8 -*-
"""
Capa web — `/administracion/contactos-externos` (`.scratch/contactos-externos`
+ `.scratch/contactos-externos-import-export`). Buscador + paginación,
import/export CSV, exclusiva de admin.
"""

from app.domain.contacto_externo import ContactoExterno
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
    assert r.text.strip() == "Nombre,Teléfonos,WhatsApp"


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
    lineas = r.text.strip().splitlines()
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
