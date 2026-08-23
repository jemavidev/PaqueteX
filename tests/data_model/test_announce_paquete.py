# -*- coding: utf-8 -*-
"""
Seam A — Servicio de dominio `announce` (anunciar Paquete con snapshot
congelado), contra el Postgres efímero construido con `alembic upgrade head`.

Se prueba comportamiento externo observable: distinguir Anunciante de
Destinatario, congelar el snapshot del contexto de entrega (teléfono del
anunciante, nombre/teléfono del destinatario y la terna del apartamento resuelto
EN EL INSTANTE del anuncio, copiada como texto), y que el Paquete nazca en estado
`ANUNCIADO`. No se inspeccionan internals de SQLAlchemy ni el DDL.

Los tres "a nombre de" del ticket:
  - yo mismo               → el Destinatario ES el Anunciante.
  - otra Persona registrada → recipient_name/phone = los de esa Persona; su
                              apartamento_actual alimenta el snapshot.
  - solo un nombre          → recipient_name bajo el tel del Anunciante,
                              recipient_phone nulo, sin crear Persona sin llave;
                              el snapshot es el del Anunciante.
"""

import pytest

from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona import Persona
from app.domain.persona_service import get_or_create_persona
from app.domain.apartamento_service import (
    resolver_apartamento,
    set_apartamento_actual,
)

pytestmark = pytest.mark.integration


def _total_personas(session) -> int:
    return session.query(Persona).count()


# --------------------------------------------------------------------------- #
# Caso 1 — anunciar para sí mismo (Destinatario ES el Anunciante)
# --------------------------------------------------------------------------- #
def test_anunciar_para_si_mismo_el_destinatario_es_el_anunciante(db_session):
    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )

    assert paquete.id is not None
    # El snapshot del anunciante y del destinatario coinciden por construcción.
    assert paquete.announced_by_phone == "+573001234567"
    assert paquete.recipient_name == "ANA"
    assert paquete.recipient_phone == "+573001234567"
    # Nace anunciado.
    assert paquete.estado == EstadoPaquete.ANUNCIADO
    # Una sola Persona: el propio anunciante.
    assert _total_personas(db_session) == 1


def test_anunciar_para_si_mismo_referencia_al_anunciante_por_fk(db_session):
    anunciante = get_or_create_persona(db_session, "3001234567", "Ana")

    paquete = announce(
        db_session,
        anunciante_telefono="+57 300 123 4567",  # otro formato → misma Persona
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )

    # El Anunciante se referencia por FK a la Persona (reutilizada, no duplicada).
    assert paquete.announced_by_persona_id == anunciante.id
    assert _total_personas(db_session) == 1


# --------------------------------------------------------------------------- #
# Caso 2 — a nombre de otra Persona registrada (Destinatario != Anunciante)
# --------------------------------------------------------------------------- #
def test_anunciar_a_nombre_de_otra_persona_registrada(db_session):
    # El destinatario ya está registrado (tiene teléfono propio).
    destino = get_or_create_persona(db_session, "3019999999", "Beto")

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.persona_registrada("3019999999"),
    )

    # Quien anuncia es Ana; a nombre de quién llega es Beto.
    assert paquete.announced_by_phone == "+573001234567"
    assert paquete.recipient_name == "BETO"
    assert paquete.recipient_phone == "+573019999999"
    # El destinatario ya existía: no se crea una tercera Persona.
    assert _total_personas(db_session) == 2
    # Y el destinatario congelado corresponde a la Persona registrada.
    assert destino.telefono == paquete.recipient_phone


def test_anunciar_a_persona_registrada_inexistente_lanza(db_session):
    # 'persona_registrada' exige que la Persona ya exista (tiene su propia llave).
    # Si no está registrada, no hay nombre con qué crearla: usar 'solo_nombre'.
    with pytest.raises(LookupError):
        announce(
            db_session,
            anunciante_telefono="3001234567",
            anunciante_nombre="Ana",
            destinatario=Destinatario.persona_registrada("3050000000"),
        )


# --------------------------------------------------------------------------- #
# Caso 3 — solo un nombre (sin teléfono): nombre bajo el tel del Anunciante
# --------------------------------------------------------------------------- #
def test_anunciar_solo_nombre_sin_telefono_no_crea_persona_sin_llave(db_session):
    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.solo_nombre("Carlos"),
    )

    # El nombre queda como recipient_name bajo el teléfono del Anunciante.
    assert paquete.recipient_name == "CARLOS"
    assert paquete.recipient_phone is None
    assert paquete.announced_by_phone == "+573001234567"
    # NO se inventa una Persona sin teléfono: solo existe el Anunciante.
    assert _total_personas(db_session) == 1


# --------------------------------------------------------------------------- #
# Snapshot del apartamento: resuelto EN EL INSTANTE del anuncio, copiado como texto
# --------------------------------------------------------------------------- #
def test_snapshot_congela_el_apartamento_del_anunciante(db_session):
    get_or_create_persona(db_session, "3001234567", "Ana")
    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    set_apartamento_actual(db_session, "3001234567", apto)

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )

    # La terna se copia como texto en forma canónica (MAYÚSCULAS, colapsada).
    assert (
        paquete.snapshot_conjunto,
        paquete.snapshot_torre,
        paquete.snapshot_apartamento,
    ) == ("EL CLUB", "TORRE 1", "101")


def test_snapshot_usa_el_apartamento_del_destinatario_registrado(db_session):
    # Anunciante y destinatario viven en apartamentos distintos.
    get_or_create_persona(db_session, "3001234567", "Ana")
    apto_ana = resolver_apartamento(db_session, "TORRE 1", "101")
    set_apartamento_actual(db_session, "3001234567", apto_ana)

    get_or_create_persona(db_session, "3019999999", "Beto")
    apto_beto = resolver_apartamento(db_session, "TORRE 2", "202")
    set_apartamento_actual(db_session, "3019999999", apto_beto)

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.persona_registrada("3019999999"),
    )

    # El snapshot es el del DESTINATARIO (Beto), no el del Anunciante (Ana).
    assert (
        paquete.snapshot_conjunto,
        paquete.snapshot_torre,
        paquete.snapshot_apartamento,
    ) == ("EL CLUB", "TORRE 2", "202")


def test_snapshot_solo_nombre_usa_el_apartamento_del_anunciante(db_session):
    get_or_create_persona(db_session, "3001234567", "Ana")
    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    set_apartamento_actual(db_session, "3001234567", apto)

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.solo_nombre("Carlos"),
    )

    # Un nombre sin teléfono se entrega bajo el apartamento del Anunciante.
    assert (
        paquete.snapshot_conjunto,
        paquete.snapshot_torre,
        paquete.snapshot_apartamento,
    ) == ("EL CLUB", "TORRE 1", "101")


def test_sin_apartamento_el_snapshot_queda_nulo(db_session):
    # El anunciante no tiene apartamento_actual: las columnas snapshot quedan NULL.
    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )

    assert paquete.snapshot_conjunto is None
    assert paquete.snapshot_torre is None
    assert paquete.snapshot_apartamento is None


def test_apartamento_explicito_override_congela_esa_terna(db_session):
    # Se puede resolver la entrega a un apartamento explícito (aunque la Persona
    # relevante tenga otro apartamento_actual): el snapshot congela el explícito.
    get_or_create_persona(db_session, "3001234567", "Ana")
    apto_actual = resolver_apartamento(db_session, "TORRE 1", "101")
    set_apartamento_actual(db_session, "3001234567", apto_actual)
    apto_entrega = resolver_apartamento(db_session, "TORRE 3", "303")

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
        apartamento=apto_entrega,
    )

    assert (
        paquete.snapshot_conjunto,
        paquete.snapshot_torre,
        paquete.snapshot_apartamento,
    ) == ("EL CLUB", "TORRE 3", "303")


def test_el_snapshot_no_se_reescribe_si_la_persona_se_muda_despues(db_session):
    # Congelado significa inmutable: mudar al residente DESPUÉS del anuncio no
    # toca el snapshot del paquete ya anunciado (ADR-0001).
    get_or_create_persona(db_session, "3001234567", "Ana")
    apto_viejo = resolver_apartamento(db_session, "TORRE 1", "101")
    set_apartamento_actual(db_session, "3001234567", apto_viejo)

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )

    # La Persona se muda a otra torre.
    apto_nuevo = resolver_apartamento(db_session, "TORRE 4", "404")
    set_apartamento_actual(db_session, "3001234567", apto_nuevo)
    db_session.refresh(paquete)

    # El paquete conserva el apartamento de ENTONCES, no el nuevo.
    assert (
        paquete.snapshot_conjunto,
        paquete.snapshot_torre,
        paquete.snapshot_apartamento,
    ) == ("EL CLUB", "TORRE 1", "101")


# --------------------------------------------------------------------------- #
# Estado del ciclo de vida y llaves de negocio
# --------------------------------------------------------------------------- #
def test_el_paquete_nace_en_estado_anunciado_con_su_timestamp(db_session):
    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )

    assert paquete.estado == EstadoPaquete.ANUNCIADO
    assert paquete.announced_at is not None
    # Las demás transiciones aún no ocurrieron.
    assert paquete.received_at is None
    assert paquete.delivered_at is None
    assert paquete.cancelled_at is None
    # El actor de la transición 'anunciar' es el cliente (Persona), no un Usuario:
    # las FK-actor hacia 'usuarios' quedan NULL en esta rebanada.
    assert paquete.announced_by_usuario_id is None
    assert paquete.received_by_usuario_id is None
    assert paquete.delivered_by_usuario_id is None
    assert paquete.cancelled_by_usuario_id is None
    # La guía no se captura al anunciar.
    assert paquete.guide_number is None


def test_access_code_presente_unico_y_con_alfabeto_correcto(db_session):
    p1 = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    p2 = announce(
        db_session,
        anunciante_telefono="3019999999",
        anunciante_nombre="Beto",
        destinatario=Destinatario.yo_mismo(),
    )

    assert p1.access_code and p2.access_code
    assert p1.access_code != p2.access_code  # únicos entre paquetes distintos

    for codigo in (p1.access_code, p2.access_code):
        assert len(codigo) == 4
        assert not set(codigo) & set("01OIL")
        assert "666" not in codigo


def test_dos_anuncios_producen_dos_paquetes(db_session):
    announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.solo_nombre("Carlos"),
    )

    assert db_session.query(Paquete).count() == 2
    # Pero una sola Persona (mismo anunciante, y 'Carlos' no es Persona).
    assert _total_personas(db_session) == 1


# --------------------------------------------------------------------------- #
# Ticket 08 (.scratch/mis-datos) — `declarado_por_cliente` con auto-match
# contra el roster de Ocupantes del apartamento del anunciante.
#
# Conversación 2026-08-15 (pedido explícito): el nombre declarado SOLO se
# honra si coincide con un co-residente de la MISMA unidad del Anunciante --
# sin apartamento propio, o sin esa coincidencia, el anuncio se hace
# individual (mismo resultado que `yo_mismo()`), nunca con el nombre tal
# cual lo escribió el cliente.
# --------------------------------------------------------------------------- #
def test_declarado_por_cliente_sin_apartamento_se_anuncia_individual(db_session):
    p = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.declarado_por_cliente("Cualquier Cosa"),
    )
    assert p.recipient_name == "ANA"  # el propio Anunciante, no "Cualquier Cosa"
    assert p.recipient_phone == "+573001234567"


def test_declarado_por_cliente_coincide_con_su_propio_nombre_de_ocupante(db_session):
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    agregar_ocupante(db_session, apto, "Ana", telefono="3001234567")

    p = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.declarado_por_cliente("Ana"),
    )
    assert p.recipient_name == "ANA"
    assert p.recipient_phone == "+573001234567"


def test_declarado_por_cliente_coincide_con_ocupante_sin_telefono_usa_tel_del_principal(db_session):
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante
    from app.domain.staff_service import create_initial_admin

    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    ana = agregar_ocupante(db_session, apto, "Ana", telefono="3001234567")
    admin = create_initial_admin(db_session, "admin@club.com", "Admin", "Contrasena1")
    confirmar_ocupante(db_session, ana, admin)  # Ana confirmada como principal
    agregar_ocupante(db_session, apto, "Hijo")  # sin teléfono

    p = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.declarado_por_cliente("Hijo"),
    )
    assert p.recipient_name == "HIJO"
    assert p.recipient_phone == "+573001234567"  # el del principal (Ana)


def test_declarado_por_cliente_coincide_con_ocupante_con_telefono_propio(db_session):
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    agregar_ocupante(db_session, apto, "Ana", telefono="3001234567")
    agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    p = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.declarado_por_cliente("Hija"),
    )
    assert p.recipient_name == "HIJA"
    assert p.recipient_phone == "+573021112233"  # el propio de Hija, no el de Ana


def test_declarado_por_cliente_no_coincide_con_nadie_de_su_unidad_se_anuncia_individual(db_session):
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    agregar_ocupante(db_session, apto, "Ana", telefono="3001234567")

    p = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.declarado_por_cliente("Nombre Que No Existe"),
    )
    # "Nombre Que No Existe" no es co-residente de la unidad de Ana -- se
    # ignora, el anuncio queda a nombre de Ana (no puede anunciar para
    # alguien fuera de su propia unidad).
    assert p.recipient_name == "ANA"
    assert p.recipient_phone == "+573001234567"


# --------------------------------------------------------------------------- #
# `.scratch/pendientes-cliente` (issue "límite de anuncios por teléfono") --
# contar cuántos ANUNCIADO tiene ya un teléfono, base del tope de
# `/anunciar`.
# --------------------------------------------------------------------------- #
def test_contar_anunciados_activos_arranca_en_cero(db_session):
    from app.domain.paquete_service import contar_anunciados_activos_de_telefono

    assert contar_anunciados_activos_de_telefono(db_session, "+573001234567") == 0


def test_contar_anunciados_activos_cuenta_solo_ese_telefono(db_session):
    from app.domain.paquete_service import contar_anunciados_activos_de_telefono

    announce(
        db_session, "3001234567", "Ana", Destinatario.declarado_por_cliente("Ana")
    )
    announce(
        db_session, "3001234567", "Ana", Destinatario.declarado_por_cliente("Ana")
    )
    announce(
        db_session, "3019999999", "Beto", Destinatario.declarado_por_cliente("Beto")
    )

    assert contar_anunciados_activos_de_telefono(db_session, "+573001234567") == 2
    assert contar_anunciados_activos_de_telefono(db_session, "+573019999999") == 1


def test_contar_anunciados_activos_no_cuenta_recibido_entregado_ni_cancelado(db_session):
    from app.domain.paquete_lifecycle import cancel, deliver, receive
    from app.domain.paquete_service import contar_anunciados_activos_de_telefono
    from app.domain.staff_service import create_initial_admin

    staff = create_initial_admin(db_session, "admin@club.com", "Admin", "Contrasena1")

    p1 = announce(
        db_session, "3001234567", "Ana", Destinatario.declarado_por_cliente("Ana")
    )
    p2 = announce(
        db_session, "3001234567", "Ana", Destinatario.declarado_por_cliente("Ana")
    )
    p3 = announce(
        db_session, "3001234567", "Ana", Destinatario.declarado_por_cliente("Ana")
    )
    announce(  # el único que se queda en ANUNCIADO
        db_session, "3001234567", "Ana", Destinatario.declarado_por_cliente("Ana")
    )
    receive(db_session, p1, staff)
    receive(db_session, p2, staff)
    deliver(db_session, p2, staff)
    cancel(db_session, p3, staff, "NO_RECLAMADO")

    assert contar_anunciados_activos_de_telefono(db_session, "+573001234567") == 1


# --------------------------------------------------------------------------- #
# ADR-0007 / ticket 03 (.scratch/announce-rapido) -- Anunciante solo-WhatsApp.
# --------------------------------------------------------------------------- #
def test_anunciar_con_whatsapp_deja_announced_by_phone_nulo(db_session):
    paquete = announce(
        db_session,
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
        anunciante_whatsapp="ana.whats",
    )

    assert paquete.announced_by_phone is None
    persona = db_session.get(Persona, paquete.announced_by_persona_id)
    assert persona.telefono is None
    assert persona.whatsapp_usuario == "ana.whats"
    assert paquete.recipient_name == "ANA"
    assert paquete.recipient_phone is None  # el destinatario tampoco tiene teléfono
    assert paquete.estado == EstadoPaquete.ANUNCIADO


def test_anunciar_con_whatsapp_reutiliza_la_misma_persona(db_session):
    from app.domain.persona_service import get_or_create_persona_por_whatsapp

    anunciante = get_or_create_persona_por_whatsapp(db_session, "ana.whats", "Ana")

    paquete = announce(
        db_session,
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
        anunciante_whatsapp="ana.whats",
    )

    assert paquete.announced_by_persona_id == anunciante.id
    assert _total_personas(db_session) == 1


def test_anunciar_sin_telefono_ni_whatsapp_falla(db_session):
    with pytest.raises(ValueError):
        announce(db_session, anunciante_nombre="Ana", destinatario=Destinatario.yo_mismo())


def test_anunciar_sin_destinatario_falla_con_value_error(db_session):
    # `destinatario: Destinatario = None` en la firma no significa que sea
    # opcional -- sin este guard, omitirlo revienta con un AttributeError
    # crudo (`.tipo` sobre `None`) en vez de un ValueError controlado.
    with pytest.raises(ValueError):
        announce(db_session, anunciante_telefono="3001234567", anunciante_nombre="Ana")


def test_anunciar_con_telefono_y_whatsapp_juntos_falla(db_session):
    with pytest.raises(ValueError):
        announce(
            db_session,
            anunciante_telefono="3001234567",
            anunciante_nombre="Ana",
            destinatario=Destinatario.yo_mismo(),
            anunciante_whatsapp="ana.whats",
        )


def test_contar_anunciados_activos_ignora_anunciantes_solo_whatsapp(db_session):
    from app.domain.paquete_service import contar_anunciados_activos_de_telefono

    announce(
        db_session,
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
        anunciante_whatsapp="ana.whats",
    )

    # No revienta, y no cuenta -- NULL nunca matchea un teléfono real.
    assert contar_anunciados_activos_de_telefono(db_session, "+573001234567") == 0


# --------------------------------------------------------------------------- #
# `Destinatario.ocupante(id)` -- generaliza la resolución que antes solo
# ocurría por nombre dentro de `declarado_por_cliente` (ver casos arriba).
# --------------------------------------------------------------------------- #
def test_destinatario_ocupante_con_telefono_propio(db_session):
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    agregar_ocupante(db_session, apto, "Ana", telefono="3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.ocupante(hija.id),
    )

    assert paquete.recipient_name == "HIJA"
    assert paquete.recipient_phone == "+573021112233"


def test_destinatario_ocupante_solo_whatsapp_recipient_phone_cae_a_quien_llamo(db_session):
    # Issue 163 (.scratch/pendientes-cliente): "siempre debe haber un
    # número... responsable" -- `recipient_phone` sigue siendo estrictamente
    # Teléfono, pero ya no se rinde en seco si el Ocupante solo tiene
    # WhatsApp propio. Acá todavía no hay Principal CONFIRMADO en la unidad
    # (Ana está pending) -- `telefono_notificacion_ocupante` no encuentra a
    # quién caer, así que el último recurso de `announce()` (el Anunciante,
    # quien llamó) es lo que resuelve esto, no un Principal.
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    agregar_ocupante(db_session, apto, "Ana", telefono="3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", whatsapp_usuario="hija.whats")

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.ocupante(hija.id),
    )

    assert paquete.recipient_name == "HIJA"
    assert paquete.recipient_phone == "+573001234567"  # el de Ana, quien llamó (último recurso)


def test_destinatario_ocupante_solo_whatsapp_recipient_phone_cae_al_principal_confirmado(db_session):
    # Issue 163 -- a diferencia del test anterior, acá SÍ hay un Principal
    # confirmado en la unidad, distinto de quien anuncia: `telefono_
    # notificacion_ocupante` debe encontrarlo y usarlo, sin necesitar el
    # último recurso del Anunciante.
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante
    from app.domain.staff_service import create_initial_admin

    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    mama = agregar_ocupante(db_session, apto, "Mamá", telefono="3001234567")
    admin = create_initial_admin(db_session, "admin@club.com", "Admin", "Contrasena1")
    confirmar_ocupante(db_session, mama, admin)  # Mamá confirmada como principal
    hija = agregar_ocupante(db_session, apto, "Hija", whatsapp_usuario="hija.whats")

    paquete = announce(
        db_session,
        anunciante_telefono="3007654321",
        anunciante_nombre="Vecino",
        destinatario=Destinatario.ocupante(hija.id),
    )

    assert paquete.recipient_name == "HIJA"
    assert paquete.recipient_phone == "+573001234567"  # el de Mamá (principal), no el de quien llamó


def test_destinatario_ocupante_sin_contacto_propio_cae_al_principal(db_session):
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante
    from app.domain.staff_service import create_initial_admin

    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    ana = agregar_ocupante(db_session, apto, "Ana", telefono="3001234567")
    admin = create_initial_admin(db_session, "admin@club.com", "Admin", "Contrasena1")
    confirmar_ocupante(db_session, ana, admin)  # Ana confirmada como principal
    hijo = agregar_ocupante(db_session, apto, "Hijo")  # sin contacto propio

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.ocupante(hijo.id),
    )

    assert paquete.recipient_name == "HIJO"
    assert paquete.recipient_phone == "+573001234567"  # el del principal (Ana)


def test_destinatario_ocupante_usa_el_apartamento_del_ocupante_sin_override(db_session):
    from app.domain.ocupante_service import agregar_ocupante

    # El Anunciante (staff, sin Persona con apartamento propio relevante acá)
    # vive en otra unidad -- el snapshot debe ser el del OCUPANTE, no el del
    # anunciante, cuando no se pasa `apartamento` explícito.
    apto_hija = resolver_apartamento(db_session, "TORRE 2", "202")
    hija = agregar_ocupante(db_session, apto_hija, "Hija", telefono="3021112233")

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Staff Anunciando",
        destinatario=Destinatario.ocupante(hija.id),
    )

    assert (
        paquete.snapshot_conjunto,
        paquete.snapshot_torre,
        paquete.snapshot_apartamento,
    ) == ("EL CLUB", "TORRE 2", "202")


def test_destinatario_ocupante_anunciante_explicito_no_se_resuelve_por_el_ocupante(db_session):
    # Ticket 02 (.scratch/announce-residente-correcto): cuando YA se sabe
    # quién anuncia (identificado por Teléfono/WhatsApp, camino nuevo de
    # /announce), el Anunciante es esa Persona -- incluso si es CO-
    # RESIDENTE de la MISMA unidad que el Ocupante elegido como
    # Destinatario. announce() no necesita ningún cambio para esto: el
    # Anunciante se resuelve de sus propios parámetros, nunca a partir del
    # Destinatario -- este test lo deja explícito (antes solo estaba
    # cubierto por casualidad vía test_destinatario_ocupante_usa_el_
    # apartamento_del_ocupante_sin_override, con un anunciante de una
    # unidad DISTINTA).
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    mama = agregar_ocupante(db_session, apto, "Mamá", telefono="3001234567")
    hijo = agregar_ocupante(db_session, apto, "Hijo")  # sin contacto propio

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",  # Mamá -- quien llamó
        destinatario=Destinatario.ocupante(hijo.id),  # el paquete es para Hijo
    )

    assert paquete.announced_by_persona_id == mama.persona_id
    assert paquete.announced_by_phone == "+573001234567"
    assert paquete.recipient_name == "HIJO"


def test_destinatario_ocupante_inexistente_lanza(db_session):
    import uuid

    with pytest.raises(LookupError):
        announce(
            db_session,
            anunciante_telefono="3001234567",
            anunciante_nombre="Ana",
            destinatario=Destinatario.ocupante(uuid.uuid4()),
        )


# --------------------------------------------------------------------------- #
# `paquetes_abiertos_de_persona` (issue 164, .scratch/pendientes-cliente) --
# identificar a un residente en /announce y listarle sus paquetes en curso.
# --------------------------------------------------------------------------- #
def test_paquetes_abiertos_encuentra_por_recipient_phone(db_session):
    from app.domain.paquete_service import paquetes_abiertos_de_persona

    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    paquete = announce(
        db_session, anunciante_telefono="3001234567", anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )

    encontrados = paquetes_abiertos_de_persona(db_session, ana)

    assert [p.id for p in encontrados] == [paquete.id]


def test_paquetes_abiertos_encuentra_por_announced_by_persona_id_solo_whatsapp(db_session):
    # Issue 164 -- la vía que SÍ cubre a un destinatario solo-WhatsApp:
    # si anunció su propio paquete (YO_MISMO), `announced_by_persona_id` es
    # una FK real, no depende de tener Teléfono.
    from app.domain.persona_service import get_or_create_persona_por_whatsapp
    from app.domain.paquete_service import paquetes_abiertos_de_persona

    ana = get_or_create_persona_por_whatsapp(db_session, "ana.whats", "Ana")
    paquete = announce(
        db_session, anunciante_whatsapp="ana.whats", anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )

    encontrados = paquetes_abiertos_de_persona(db_session, ana)

    assert [p.id for p in encontrados] == [paquete.id]


def test_paquetes_abiertos_filtra_entregados_y_cancelados(db_session):
    from app.domain.paquete_lifecycle import deliver, receive
    from app.domain.staff_service import create_initial_admin
    from app.domain.paquete_service import paquetes_abiertos_de_persona

    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    admin = create_initial_admin(db_session, "admin@club.com", "Admin", "Contrasena1")
    entregado = announce(
        db_session, anunciante_telefono="3001234567", anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    receive(db_session, entregado, admin)
    deliver(db_session, entregado, admin)
    en_curso = announce(
        db_session, anunciante_telefono="3001234567", anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )

    encontrados = paquetes_abiertos_de_persona(db_session, ana)

    assert [p.id for p in encontrados] == [en_curso.id]


def test_paquetes_abiertos_sin_nada_da_lista_vacia(db_session):
    from app.domain.paquete_service import paquetes_abiertos_de_persona

    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    assert paquetes_abiertos_de_persona(db_session, ana) == []
