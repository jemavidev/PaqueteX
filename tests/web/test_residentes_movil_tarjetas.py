# -*- coding: utf-8 -*-
"""
`/residentes` en móvil: una tarjeta por residente con 4 botones fijos en una sola fila (issue 400,
`.scratch/pendientes-cliente`), mismo patrón que `/paquetes` (issue 397).

Arriba: nombre + total de paquetes + basurero de Admin (aparte de los botones de uso diario); abajo: WhatsApp, Llamar,
Unidad y Asignar, SIEMPRE los 4 y en ese orden, activos o apagados. Solo < 640 px; la tabla de escritorio no cambia.
"""

import re

from app.domain.apartamento_service import resolver_apartamento
from app.domain.ocupante_service import agregar_ocupante
from app.domain.persona import Persona
from app.domain.persona_service import get_or_create_persona
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

_PW = "Contrasena1"


def _login(client, rol=RolUsuario.ADMIN):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    email = "admin@club.com"
    if rol == RolUsuario.OPERADOR:
        create_staff(client.db, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
        email = "op@club.com"
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _tarjeta(html, persona):
    m = re.search(rf'<div[^>]*data-tarjeta-residente="{persona.id}"[^>]*>(.*?)\n  </div>\n', html, re.S)
    assert m, f"no hay tarjeta móvil para {persona.nombre}"
    return m.group(0)


def _botones(tarjeta):
    fila = re.search(r'<div class="mt-2\.5 grid grid-cols-4[^"]*">(.*?)\n    </div>', tarjeta, re.S).group(1)
    return re.findall(r"<(a|span|button)\b[^>]*>.*?</\1>", fila, re.S), fila


def test_cada_residente_tiene_su_tarjeta_y_la_tabla_queda_solo_en_escritorio(client):
    _login(client)
    ana = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    html = client.get("/residentes").text

    assert re.search(r'<div class="sm:hidden[^"]*"[^>]*data-residentes-movil', html)
    assert re.search(r'<div class="hidden sm:block[^"]*">\s*<table', html)
    _tarjeta(html, ana)


def test_siempre_hay_4_botones_en_el_mismo_orden_aunque_esten_apagados(client):
    _login(client)
    solo_whatsapp = client.db.query(Persona).get(
        agregar_ocupante(client.db, resolver_apartamento(client.db, "TORRE 2", "301"), "Mafe", whatsapp_usuario="mafe.g").persona_id
    )
    client.db.commit()

    _, fila = _botones(_tarjeta(client.get("/residentes").text, solo_whatsapp))

    # Issue 401: solo ícono; el nombre de cada espacio queda en aria-label y no se ve texto.
    etiquetas = re.findall(r'<(?:a|span|button)\b[^>]*aria-label="([^":]+)', fila)
    assert [e.split()[0] for e in etiquetas] == ["Abrir", "Llamar", "Unidad", "Asignar"]
    assert re.sub(r"<[^>]+>|👫", "", fila).strip() == ""
    assert 'href="tel:' not in fila  # sin teléfono: Llamar apagado
    assert "modal-asignar-apto-" not in fila  # ya tiene apartamento: Asignar apagado


def test_sin_apartamento_asignar_esta_activo_y_con_unidad_compartida_unidad_tambien(client):
    _login(client)
    sin_apto = get_or_create_persona(client.db, "3002222222", "Jesus")
    apto = resolver_apartamento(client.db, "TORRE 4", "806")
    catalina = client.db.query(Persona).get(agregar_ocupante(client.db, apto, "Catalina", telefono="3001111111").persona_id)
    agregar_ocupante(client.db, apto, "Andres", telefono="3001111122")
    client.db.commit()
    html = client.get("/residentes").text

    _, fila_sin_apto = _botones(_tarjeta(html, sin_apto))
    _, fila_catalina = _botones(_tarjeta(html, catalina))

    assert f'data-open="modal-asignar-apto-{sin_apto.id}"' in fila_sin_apto
    assert f'href="/residentes/{catalina.id}?tab=residentes"' in fila_catalina
    assert 'href="tel:' in fila_catalina


def test_el_basurero_solo_lo_ve_el_admin_y_va_aparte_de_los_4_botones(client):
    _login(client, RolUsuario.OPERADOR)
    ana = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    tarjeta = _tarjeta(client.get("/residentes").text, ana)

    assert f'data-open="modal-eliminar-{ana.id}"' not in tarjeta


def test_el_admin_ve_el_basurero_fuera_de_la_fila_de_botones(client):
    _login(client)
    ana = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    tarjeta = _tarjeta(client.get("/residentes").text, ana)
    _, fila = _botones(tarjeta)

    assert f'data-open="modal-eliminar-{ana.id}"' in tarjeta
    assert "modal-eliminar-" not in fila
