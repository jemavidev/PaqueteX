# -*- coding: utf-8 -*-
"""
Capa web — `/administracion/personal` (alta de cuentas de staff, ticket único).

Comportamiento observable por HTTP: gate require_admin (sin sesión redirige,
operador rechazado 403, admin ve el form); un alta válida crea el Usuario; los
rechazos de dominio (duplicado, contraseña débil) no crean nada. NO se re-testea
la regla de negocio de create_staff (ya cubierta en test_staff_service.py).
"""

from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario, Usuario

_PW = "Contrasena1"


def _login_admin(client, email="admin@club.com"):
    create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})
    return email


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def test_sin_sesion_redirige_a_login(client):
    r = client.get("/administracion/personal", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_operador_es_rechazado_403(client):
    _login_operador(client)
    r = client.get("/administracion/personal")
    assert r.status_code == 403


def test_admin_ve_el_formulario(client):
    _login_admin(client)
    r = client.get("/administracion/personal")
    assert r.status_code == 200
    assert 'name="email"' in r.text and 'name="rol"' in r.text


def test_alta_valida_crea_la_cuenta(client):
    _login_admin(client)

    r = client.post(
        "/administracion/personal",
        data={
            "email": "nuevo@club.com",
            "nombre": "Nuevo Operador",
            "password": _PW,
            "rol": "OPERADOR",
        },
    )
    assert r.status_code == 200
    assert "nuevo@club.com" in r.text

    client.db.expire_all()
    creado = (
        client.db.query(Usuario).filter(Usuario.email == "nuevo@club.com").one()
    )
    assert creado.rol == RolUsuario.OPERADOR
    assert creado.password_hash != _PW  # nunca en claro


def test_email_duplicado_no_crea_segunda_cuenta(client):
    _login_admin(client)
    client.post(
        "/administracion/personal",
        data={
            "email": "dup@club.com",
            "nombre": "Uno",
            "password": _PW,
            "rol": "OPERADOR",
        },
    )

    r = client.post(
        "/administracion/personal",
        data={
            "email": "dup@club.com",
            "nombre": "Dos",
            "password": _PW,
            "rol": "OPERADOR",
        },
    )
    assert r.status_code == 400

    client.db.expire_all()
    assert client.db.query(Usuario).filter(Usuario.email == "dup@club.com").count() == 1


def test_password_debil_no_crea_cuenta(client):
    _login_admin(client)
    r = client.post(
        "/administracion/personal",
        data={
            "email": "debil@club.com",
            "nombre": "Debil",
            "password": "corta",
            "rol": "OPERADOR",
        },
    )
    assert r.status_code == 400

    client.db.expire_all()
    assert (
        client.db.query(Usuario).filter(Usuario.email == "debil@club.com").count() == 0
    )


def test_campos_vacios_rechaza_antes_de_llamar_a_dominio(client):
    _login_admin(client)
    r = client.post(
        "/administracion/personal", data={"email": "", "nombre": "", "password": "", "rol": "OPERADOR"}
    )
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# Grupo 18 (Ronda 2) — gestión de cuentas existentes.
# --------------------------------------------------------------------------- #
def test_admin_ve_la_tabla_de_cuentas_existentes(client):
    _login_admin(client)
    r = client.get("/administracion/personal")
    assert r.status_code == 200
    assert "admin@club.com" in r.text
    assert "ADMIN" in r.text


def test_editar_actualiza_nombre_y_rol(client):
    _login_admin(client)
    admin = client.db.query(Usuario).filter(Usuario.email == "admin@club.com").one()
    op = create_staff(client.db, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()

    r = client.post(
        f"/administracion/personal/{op.id}/editar",
        data={"nombre": "Opa Editada", "rol": "ADMIN"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    editado = client.db.get(Usuario, op.id)
    assert editado.nombre == "OPA EDITADA"
    assert editado.rol == RolUsuario.ADMIN


def test_operador_no_puede_editar(client):
    _login_operador(client)
    admin = client.db.query(Usuario).filter(Usuario.email == "admin@club.com").one()
    r = client.post(
        f"/administracion/personal/{admin.id}/editar",
        data={"nombre": "Hackeado", "rol": "OPERADOR"},
    )
    assert r.status_code == 403


def test_admin_no_puede_degradarse_a_si_mismo_via_http(client):
    _login_admin(client)
    admin = client.db.query(Usuario).filter(Usuario.email == "admin@club.com").one()
    r = client.post(
        f"/administracion/personal/{admin.id}/editar",
        data={"nombre": "Admin", "rol": "OPERADOR"},
    )
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.get(Usuario, admin.id).rol == RolUsuario.ADMIN


def test_resetear_password_permite_iniciar_sesion_con_la_nueva(client):
    _login_admin(client)
    admin = client.db.query(Usuario).filter(Usuario.email == "admin@club.com").one()
    op = create_staff(client.db, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()

    r = client.post(
        f"/administracion/personal/{op.id}/resetear-password",
        data={"password": "NuevaClave2"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    r2 = client.post("/ingresar", data={"email": "op@club.com", "password": "NuevaClave2"})
    assert r2.status_code == 200


def test_desactivar_impide_el_login_y_activar_lo_restaura(client):
    _login_admin(client)
    admin = client.db.query(Usuario).filter(Usuario.email == "admin@club.com").one()
    op = create_staff(client.db, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()

    r = client.post(f"/administracion/personal/{op.id}/desactivar", follow_redirects=False)
    assert r.status_code == 303

    otro_client_login = client.post(
        "/ingresar", data={"email": "op@club.com", "password": _PW}
    )
    assert otro_client_login.status_code == 400  # cuenta desactivada, rechazo genérico

    client.post(f"/administracion/personal/{op.id}/activar")
    otro_client_login2 = client.post(
        "/ingresar", data={"email": "op@club.com", "password": _PW}
    )
    assert otro_client_login2.status_code == 200


def test_desactivar_cierra_una_sesion_ya_abierta_en_el_siguiente_request(client):
    """Hueco real encontrado en auditoría (.scratch/pendientes-cliente):
    `current_staff` no releía `usuario.activo` -- solo `staff_service.
    autenticar` lo chequeaba, al hacer login. Un ADMIN que desactivaba a
    alguien con sesión YA abierta no le cortaba el acceso hasta que esa
    cookie expirara (14 días por default) o cerrara sesión manualmente.
    Dos `TestClient` independientes sobre la MISMA app (cookies propias
    cada uno) simulan al admin y al operador como sesiones de navegador
    separadas y simultáneas."""
    from fastapi.testclient import TestClient

    _login_admin(client)
    admin = client.db.query(Usuario).filter(Usuario.email == "admin@club.com").one()
    op = create_staff(client.db, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()

    op_client = TestClient(client.app)
    with op_client:
        r_login = op_client.post("/ingresar", data={"email": "op@club.com", "password": _PW})
        assert r_login.status_code == 200

        # Sesión ya abierta y funcionando -- confirma el estado ANTES de
        # desactivar (sin esto, un 303 más abajo podría deberse a cualquier
        # otra cosa, no a la desactivación en sí).
        r_antes = op_client.get("/paquetes")
        assert r_antes.status_code == 200

        client.post(f"/administracion/personal/{op.id}/desactivar")

        r_despues = op_client.get("/paquetes", follow_redirects=False)
        assert r_despues.status_code == 303
        assert r_despues.headers["location"].endswith("/ingresar")


def test_admin_no_puede_desactivarse_a_si_mismo_via_http(client):
    _login_admin(client)
    admin = client.db.query(Usuario).filter(Usuario.email == "admin@club.com").one()
    r = client.post(f"/administracion/personal/{admin.id}/desactivar")
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.get(Usuario, admin.id).activo is True


def test_gestion_id_inexistente_da_404(client):
    _login_admin(client)
    import uuid

    r = client.post(f"/administracion/personal/{uuid.uuid4()}/desactivar")
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# .scratch/pendientes-cliente, issue 192 -- "Dar de alta staff" pasa de un
# formulario siempre visible a un botón "Agregar usuario" que abre un modal.
# --------------------------------------------------------------------------- #
def _tag_modal_agregar(html_text):
    inicio = html_text.index('id="modal-agregar-usuario"')
    inicio_tag = html_text.rindex("<div", 0, inicio)
    return html_text[inicio_tag : html_text.index(">", inicio) + 1]


def test_boton_agregar_usuario_existe_y_el_modal_arranca_cerrado(client):
    _login_admin(client)
    r = client.get("/administracion/personal")
    assert r.status_code == 200
    assert 'data-open="modal-agregar-usuario"' in r.text
    assert "hidden" in _tag_modal_agregar(r.text)


def test_error_de_alta_reabre_el_modal_con_los_campos_marcados(client):
    _login_admin(client)
    r = client.post(
        "/administracion/personal",
        data={"email": "debil@club.com", "nombre": "Debil", "password": "corta", "rol": "OPERADOR"},
    )
    assert r.status_code == 400
    # El modal se reabre (sin `hidden`) -- si no, los campos en rojo
    # quedarían invisibles detrás del modal cerrado.
    assert "hidden" not in _tag_modal_agregar(r.text)
    assert 'value="debil@club.com"' in r.text  # el email escrito se conserva


def test_alta_exitosa_deja_el_modal_cerrado(client):
    _login_admin(client)
    r = client.post(
        "/administracion/personal",
        data={"email": "nuevo2@club.com", "nombre": "Nuevo Dos", "password": _PW, "rol": "OPERADOR"},
    )
    assert r.status_code == 200
    assert "hidden" in _tag_modal_agregar(r.text)
