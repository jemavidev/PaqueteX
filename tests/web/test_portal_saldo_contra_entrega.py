# -*- coding: utf-8 -*-
"""
Capa web — el residente ve su propio saldo/historial contra entrega en
`/mis-datos` (`.scratch/dinero-contra-entrega`, ticket 05).
"""

from app.domain.otp_sender import DevOtpSender
from app.domain.paquete_lifecycle import receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona import Persona
from app.domain.saldo_contra_entrega_service import registrar_movimiento_saldo
from app.domain.telefono import normalizar_telefono
from app.domain.usuario import RolUsuario, Usuario
from app.web.otp import get_otp_sender


def _login_cliente(client, telefono):
    canon = normalizar_telefono(telefono)
    staff = Usuario(nombre="ActorElegibilidad", rol=RolUsuario.OPERADOR)
    client.db.add(staff)
    client.db.flush()
    p = announce(
        client.db,
        anunciante_telefono=telefono,
        anunciante_nombre="Cliente de prueba",
        destinatario=Destinatario.yo_mismo(),
    )
    receive(client.db, p, staff)
    client.db.commit()

    sender = DevOtpSender()
    client.app.dependency_overrides[get_otp_sender] = lambda: sender
    client.post("/otp/solicitar", data={"telefono": telefono})
    codigo = sender.enviados[canon]
    client.post("/otp/verificar", data={"telefono": telefono, "codigo": codigo})
    return client.db.query(Persona).filter(Persona.telefono == canon).one(), staff


def test_residente_ve_su_propio_saldo_e_historial(client):
    persona, staff = _login_cliente(client, "3001234567")
    registrar_movimiento_saldo(client.db, persona.id, 5000, staff)
    client.db.commit()

    r = client.get("/mis-datos")
    assert r.status_code == 200
    assert "Saldo (contra entrega)" in r.text  # issue 391: antes "Saldo a favor"
    assert "5,000" in r.text


def test_sin_ningun_movimiento_no_muestra_nada(client):
    _login_cliente(client, "3001234567")

    r = client.get("/mis-datos")
    assert "Saldo (contra entrega)" not in r.text


def test_no_ve_el_saldo_de_otro_residente_aunque_comparta_apartamento(client):
    from app.domain.apartamento_service import resolver_apartamento, set_apartamento_actual
    from app.domain.persona_service import get_or_create_persona

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    get_or_create_persona(client.db, "3002222222", "Beto")
    client.db.commit()
    persona_a, staff = _login_cliente(client, "3001111111")
    set_apartamento_actual(client.db, "3001111111", apto)
    set_apartamento_actual(client.db, "3002222222", apto)
    registrar_movimiento_saldo(client.db, persona_a.id, 9000, staff)
    client.db.commit()

    # Logueado como Persona A (con historial) -- ve el suyo.
    r = client.get("/mis-datos")
    assert "9,000" in r.text

    # Ahora se loguea B (sin historial propio, aunque comparte apartamento).
    _login_cliente(client, "3002222222")
    r2 = client.get("/mis-datos")
    assert "Saldo (contra entrega)" not in r2.text
    assert "9,000" not in r2.text


def test_el_saldo_es_una_pildora_que_despliega_el_historial(client):
    """Issue 391: la tarjeta del saldo pasa a una píldora "Saldo (contra entrega) $X" que al tocarla despliega el
    historial -- cerrada por defecto."""
    import re

    persona, staff = _login_cliente(client, "3001234567")
    registrar_movimiento_saldo(client.db, persona.id, 5000, staff)
    client.db.commit()

    r = client.get("/mis-datos")

    bloque = re.search(r"<details[^>]*data-saldo-contra-entrega[^>]*>(.*?)</details>", r.text, re.S)
    assert bloque is not None
    assert " open" not in bloque.group(0).split(">", 1)[0]  # cerrada por defecto
    resumen = re.search(r"<summary[^>]*>(.*?)</summary>", bloque.group(1), re.S).group(1)
    assert "Saldo (contra entrega)" in resumen and "$5,000" in resumen
    assert "+$5,000" in bloque.group(1)  # el historial vive dentro de lo desplegable
    assert "Saldo a favor" not in r.text


def test_el_aviso_de_privacidad_queda_debajo_de_autorizo_a_papyrus(client):
    _login_cliente(client, "3001234567")

    r = client.get("/mis-datos")

    autorizo = r.text.index("Autorizo a Papyrus para recibir todos los paquetes a mi nombre")
    privacidad = r.text.index("Tus datos se tratan según nuestra")
    assert autorizo < privacidad
    assert r.text.count("Tus datos se tratan según nuestra") == 1


def test_los_movimientos_dicen_que_paso_con_el_codigo_del_paquete_y_fecha_amigable(client):
    """Issue 392: antes "19/09/2026 · paquete 49a62449" (pedazo del UUID) y "$-3,500"."""
    import re

    from app.domain.paquete import Paquete

    persona, staff = _login_cliente(client, "3001234567")
    p = client.db.query(Paquete).one()
    registrar_movimiento_saldo(client.db, persona.id, 12000, staff)
    registrar_movimiento_saldo(client.db, persona.id, -3500, staff, paquete_id=p.id)
    registrar_movimiento_saldo(client.db, persona.id, 2000, staff, paquete_id=p.id)
    client.db.commit()

    html = client.get("/mis-datos").text
    bloque = re.search(r"<details[^>]*data-saldo-contra-entrega[^>]*>(.*?)</details>", html, re.S).group(1)

    assert "Abono a tu saldo" in bloque
    assert "Pagado al mensajero" in bloque
    assert "Pago recibido" in bloque
    assert re.search(rf"Paquete (<a [^>]*>)?{p.access_code}", bloque)  # issue 395: el código va enlazado
    assert str(p.id)[:8] not in bloque  # nunca el identificador interno
    assert "-$3,500" in bloque and "$-3,500" not in bloque
    assert "+$12,000" in bloque
    assert re.search(r"\d{1,2} (ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)\. \d{4} · \d{1,2}:\d{2} (a|p)\. m\.", bloque)


def test_el_codigo_del_paquete_en_un_movimiento_enlaza_a_consultar(client):
    """Issue 395: "Paquete DFDK" -- el código es un enlace a /consultar?q=DFDK."""
    from app.domain.paquete import Paquete

    persona, staff = _login_cliente(client, "3001234567")
    p = client.db.query(Paquete).one()
    registrar_movimiento_saldo(client.db, persona.id, -12000, staff, paquete_id=p.id)
    client.db.commit()

    html = client.get("/mis-datos").text

    assert f'href="/consultar?q={p.access_code}"' in html
    assert f">{p.access_code}</a>" in html
