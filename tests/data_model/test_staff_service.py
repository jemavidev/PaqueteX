# -*- coding: utf-8 -*-
"""
Seam A — Credenciales de staff (crear / verificar), contra el Postgres efímero.

Comportamiento observable: solo un ADMIN crea staff; el bootstrap siembra el
primer admin; el email es único; la contraseña se guarda hasheada (no en claro) y
debe ser fuerte; `verify_credentials` acepta la correcta y rechaza mala/inexistente.
"""

import pytest

from app.domain.staff_service import (
    create_initial_admin,
    create_staff,
    editar_mi_perfil,
    editar_staff,
    listar_staff,
    resetear_password,
    set_activo_staff,
    verify_credentials,
)
from app.domain.usuario import RolUsuario

pytestmark = pytest.mark.integration

_PW = "Contrasena1"  # >=10, con letra y dígito


def _admin(session, email="admin@club.com"):
    return create_initial_admin(session, email, "Admin", _PW)


def test_bootstrap_crea_el_primer_admin(db_session):
    admin = create_initial_admin(db_session, "admin@club.com", "Admin", _PW)
    assert admin.rol == RolUsuario.ADMIN
    assert admin.email == "admin@club.com"


def test_bootstrap_no_crea_un_segundo_admin(db_session):
    create_initial_admin(db_session, "admin@club.com", "Admin", _PW)
    with pytest.raises(RuntimeError):
        create_initial_admin(db_session, "otro@club.com", "Otro", _PW)


def test_un_admin_crea_staff(db_session):
    admin = _admin(db_session)
    op = create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    assert op.rol == RolUsuario.OPERADOR
    assert op.email == "op@club.com"


def test_un_operador_no_puede_crear_staff(db_session):
    admin = _admin(db_session)
    op = create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    with pytest.raises(PermissionError):
        create_staff(db_session, op, "op2@club.com", "Opb", _PW, RolUsuario.OPERADOR)


def test_email_duplicado_rechazado_normalizando(db_session):
    admin = _admin(db_session)
    create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    with pytest.raises(ValueError):
        # mismo email en otro casing/espacios → normaliza igual → duplicado
        create_staff(db_session, admin, "  OP@Club.com ", "Otro", _PW, RolUsuario.OPERADOR)


def test_password_debil_rechazada(db_session):
    admin = _admin(db_session)
    with pytest.raises(ValueError):
        create_staff(db_session, admin, "a@club.com", "A", "corta1", RolUsuario.OPERADOR)
    with pytest.raises(ValueError):
        create_staff(db_session, admin, "b@club.com", "B", "solo-letras", RolUsuario.OPERADOR)


def test_password_se_guarda_hasheada_no_en_claro(db_session):
    admin = _admin(db_session)
    op = create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    assert op.password_hash and op.password_hash != _PW
    assert _PW not in op.password_hash


def test_verify_acepta_la_correcta_y_normaliza_email(db_session):
    admin = _admin(db_session)
    create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    u = verify_credentials(db_session, "  OP@Club.com ", _PW)
    assert u is not None and u.email == "op@club.com"


def test_verify_rechaza_password_mala(db_session):
    admin = _admin(db_session)
    create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    assert verify_credentials(db_session, "op@club.com", "otra-mala1") is None


def test_verify_rechaza_email_inexistente(db_session):
    assert verify_credentials(db_session, "nadie@club.com", _PW) is None


# --------------------------------------------------------------------------- #
# Grupo 18 (Ronda 2) — CRUD completo de cuentas de staff.
# --------------------------------------------------------------------------- #
def test_listar_staff_activas_primero_por_nombre(db_session):
    admin = _admin(db_session)
    op_b = create_staff(db_session, admin, "b@club.com", "Beto", _PW, RolUsuario.OPERADOR)
    create_staff(db_session, admin, "a@club.com", "Alicia", _PW, RolUsuario.OPERADOR)
    set_activo_staff(db_session, admin, op_b, False)

    nombres = [u.nombre for u in listar_staff(db_session)]
    assert nombres.index("ALICIA") < nombres.index("BETO")  # activas antes que inactivas


def test_editar_staff_actualiza_nombre_y_rol(db_session):
    admin = _admin(db_session)
    op = create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)

    editar_staff(db_session, admin, op, nombre="Opa Editada", rol=RolUsuario.ADMIN)

    assert op.nombre == "OPA EDITADA"
    assert op.rol == RolUsuario.ADMIN


def test_editar_staff_un_operador_no_puede(db_session):
    admin = _admin(db_session)
    op = create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    with pytest.raises(PermissionError):
        editar_staff(db_session, op, admin, nombre="Hackeado")


def test_admin_no_puede_degradarse_a_si_mismo(db_session):
    admin = _admin(db_session)
    with pytest.raises(ValueError):
        editar_staff(db_session, admin, admin, rol=RolUsuario.OPERADOR)


def test_admin_puede_degradar_a_otro_admin(db_session):
    admin = _admin(db_session)
    otro_admin = create_staff(db_session, admin, "otro@club.com", "Otro", _PW, RolUsuario.ADMIN)
    editar_staff(db_session, admin, otro_admin, rol=RolUsuario.OPERADOR)
    assert otro_admin.rol == RolUsuario.OPERADOR


# --------------------------------------------------------------------------- #
# .scratch/pendientes-cliente, issue 197 -- autoservicio de nombre, sin rol.
# --------------------------------------------------------------------------- #
def test_editar_mi_perfil_un_operador_cambia_su_propio_nombre(db_session):
    admin = _admin(db_session)
    op = create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)

    editar_mi_perfil(db_session, op, "Opa Nuevo Nombre")

    assert op.nombre == "OPA NUEVO NOMBRE"
    assert op.rol == RolUsuario.OPERADOR  # el rol nunca se toca acá


def test_editar_mi_perfil_no_acepta_parametro_de_rol():
    import inspect

    firma = inspect.signature(editar_mi_perfil)
    assert "rol" not in firma.parameters  # ni siquiera se puede pasar


def test_editar_mi_perfil_nombre_vacio_rechaza(db_session):
    admin = _admin(db_session)
    op = create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    with pytest.raises(ValueError):
        editar_mi_perfil(db_session, op, "   ")


# --------------------------------------------------------------------------- #
# .scratch/notificaciones-enviar-prueba, ticket 01 -- teléfono/WhatsApp
# propios del staff (contacto, sin relación con la identidad de Persona).
# --------------------------------------------------------------------------- #
def test_editar_mi_perfil_guarda_telefono_y_whatsapp(db_session):
    admin = _admin(db_session)
    op = create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)

    editar_mi_perfil(db_session, op, "Opa", telefono="3001234567", whatsapp="3009876543")

    assert op.telefono == "3001234567"
    assert op.whatsapp == "3009876543"


def test_editar_mi_perfil_telefono_y_whatsapp_vacios_quedan_en_none(db_session):
    admin = _admin(db_session)
    op = create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    editar_mi_perfil(db_session, op, "Opa", telefono="3001234567", whatsapp="3009876543")

    editar_mi_perfil(db_session, op, "Opa", telefono="   ", whatsapp="")

    assert op.telefono is None
    assert op.whatsapp is None


def test_editar_mi_perfil_sin_telefono_ni_whatsapp_no_falla(db_session):
    admin = _admin(db_session)
    op = create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)

    editar_mi_perfil(db_session, op, "Opa")

    assert op.telefono is None
    assert op.whatsapp is None


def test_resetear_password_cambia_el_hash(db_session):
    admin = _admin(db_session)
    op = create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    hash_original = op.password_hash

    resetear_password(db_session, admin, op, "NuevaClave2")

    assert op.password_hash != hash_original
    assert verify_credentials(db_session, "op@club.com", "NuevaClave2") is not None
    assert verify_credentials(db_session, "op@club.com", _PW) is None


def test_resetear_password_un_operador_no_puede(db_session):
    admin = _admin(db_session)
    op = create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    with pytest.raises(PermissionError):
        resetear_password(db_session, op, admin, "NuevaClave2")


def test_desactivar_staff_le_impide_iniciar_sesion(db_session):
    admin = _admin(db_session)
    op = create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)

    set_activo_staff(db_session, admin, op, False)

    assert op.activo is False
    assert verify_credentials(db_session, "op@club.com", _PW) is None


def test_reactivar_staff_le_permite_iniciar_sesion_de_nuevo(db_session):
    admin = _admin(db_session)
    op = create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    set_activo_staff(db_session, admin, op, False)

    set_activo_staff(db_session, admin, op, True)

    assert verify_credentials(db_session, "op@club.com", _PW) is not None


def test_admin_no_puede_desactivarse_a_si_mismo(db_session):
    admin = _admin(db_session)
    with pytest.raises(ValueError):
        set_activo_staff(db_session, admin, admin, False)


def test_admin_puede_desactivar_a_otro_admin(db_session):
    admin = _admin(db_session)
    otro_admin = create_staff(db_session, admin, "otro@club.com", "Otro", _PW, RolUsuario.ADMIN)
    set_activo_staff(db_session, admin, otro_admin, False)
    assert otro_admin.activo is False


def test_desactivar_no_borra_la_fila_las_fk_de_actor_siguen_validas(db_session):
    from app.domain.paquete_lifecycle import receive
    from app.domain.paquete_service import Destinatario, announce

    admin = _admin(db_session)
    op = create_staff(db_session, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    p = announce(db_session, "3001234567", "Ana", Destinatario.yo_mismo())
    receive(db_session, p, op)

    set_activo_staff(db_session, admin, op, False)

    assert p.received_by_usuario_id == op.id  # la FK sigue apuntando a una fila real
