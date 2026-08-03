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
    get_or_create_apartamento,
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
    apto = get_or_create_apartamento(db_session, "Las Flores", "Torre A", "101")
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
    ) == ("LAS FLORES", "TORRE A", "101")


def test_snapshot_usa_el_apartamento_del_destinatario_registrado(db_session):
    # Anunciante y destinatario viven en apartamentos distintos.
    get_or_create_persona(db_session, "3001234567", "Ana")
    apto_ana = get_or_create_apartamento(db_session, "Las Flores", "Torre A", "101")
    set_apartamento_actual(db_session, "3001234567", apto_ana)

    get_or_create_persona(db_session, "3019999999", "Beto")
    apto_beto = get_or_create_apartamento(db_session, "Las Flores", "Torre B", "202")
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
    ) == ("LAS FLORES", "TORRE B", "202")


def test_snapshot_solo_nombre_usa_el_apartamento_del_anunciante(db_session):
    get_or_create_persona(db_session, "3001234567", "Ana")
    apto = get_or_create_apartamento(db_session, "Las Flores", "Torre A", "101")
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
    ) == ("LAS FLORES", "TORRE A", "101")


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
    apto_actual = get_or_create_apartamento(db_session, "Las Flores", "Torre A", "101")
    set_apartamento_actual(db_session, "3001234567", apto_actual)
    apto_entrega = get_or_create_apartamento(db_session, "Las Flores", "Torre C", "303")

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
    ) == ("LAS FLORES", "TORRE C", "303")


def test_el_snapshot_no_se_reescribe_si_la_persona_se_muda_despues(db_session):
    # Congelado significa inmutable: mudar al residente DESPUÉS del anuncio no
    # toca el snapshot del paquete ya anunciado (ADR-0001).
    get_or_create_persona(db_session, "3001234567", "Ana")
    apto_viejo = get_or_create_apartamento(db_session, "Las Flores", "Torre A", "101")
    set_apartamento_actual(db_session, "3001234567", apto_viejo)

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )

    # La Persona se muda a otra torre.
    apto_nuevo = get_or_create_apartamento(db_session, "Las Flores", "Torre D", "404")
    set_apartamento_actual(db_session, "3001234567", apto_nuevo)
    db_session.refresh(paquete)

    # El paquete conserva el apartamento de ENTONCES, no el nuevo.
    assert (
        paquete.snapshot_conjunto,
        paquete.snapshot_torre,
        paquete.snapshot_apartamento,
    ) == ("LAS FLORES", "TORRE A", "101")


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
# --------------------------------------------------------------------------- #
def test_declarado_por_cliente_sin_apartamento_cae_al_comportamiento_de_siempre(db_session):
    p = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.declarado_por_cliente("Cualquier Cosa"),
    )
    assert p.recipient_name == "CUALQUIER COSA"
    assert p.recipient_phone == "+573001234567"  # el del anunciante, como siempre


def test_declarado_por_cliente_coincide_con_su_propio_nombre_de_ocupante(db_session):
    from app.domain.ocupante_service import agregar_ocupante

    apto = get_or_create_apartamento(db_session, "Las Flores", "A", "101")
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
    from app.domain.ocupante_service import agregar_ocupante

    apto = get_or_create_apartamento(db_session, "Las Flores", "A", "101")
    agregar_ocupante(db_session, apto, "Ana", telefono="3001234567")
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

    apto = get_or_create_apartamento(db_session, "Las Flores", "A", "101")
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


def test_declarado_por_cliente_no_coincide_con_nadie_cae_al_comportamiento_de_siempre(db_session):
    from app.domain.ocupante_service import agregar_ocupante

    apto = get_or_create_apartamento(db_session, "Las Flores", "A", "101")
    agregar_ocupante(db_session, apto, "Ana", telefono="3001234567")

    p = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.declarado_por_cliente("Nombre Que No Existe"),
    )
    assert p.recipient_name == "NOMBRE QUE NO EXISTE"
    assert p.recipient_phone == "+573001234567"  # cae al comportamiento de siempre
