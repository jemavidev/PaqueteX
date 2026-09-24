# -*- coding: utf-8 -*-
"""
Ingreso del staff con usuario, además del correo (issue 396, `.scratch/pendientes-cliente`).

El usuario es lo que va antes de la "@" del correo (`jveyes@gmail.com` → `jveyes`): no se guarda aparte, así que no
hay migración y todo el staff lo tiene desde ya. Un usuario nunca puede ser ambiguo: crear personal rechaza un correo
cuyo usuario ya use otra cuenta.
"""

import pytest

from app.domain.staff_service import create_initial_admin, create_staff, verify_credentials
from app.domain.usuario import RolUsuario, Usuario

_PW = "Contrasena1"


def _sembrar(client):
    admin = create_initial_admin(client.db, "admin@papyrus.com.co", "Admin", _PW)
    create_staff(client.db, admin, "jveyes@gmail.com", "Jesus Veyes", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    return admin


def _ingresar(client, identificador, password=_PW):
    return client.post("/ingresar", data={"email": identificador, "password": password}, follow_redirects=False)


def test_ingresa_con_el_usuario_sacado_del_correo(client):
    _sembrar(client)

    r = _ingresar(client, "jveyes")

    assert r.status_code == 303
    assert client.get("/paquetes", follow_redirects=False).status_code == 200


def test_el_usuario_no_distingue_mayusculas(client):
    _sembrar(client)

    assert _ingresar(client, "  JVeyes ").status_code == 303


def test_el_correo_completo_sigue_funcionando(client):
    _sembrar(client)

    assert _ingresar(client, "jveyes@gmail.com").status_code == 303


def test_usuario_con_clave_mala_da_el_mismo_error_generico(client):
    _sembrar(client)

    r = _ingresar(client, "jveyes", "OtraClave99")

    assert r.status_code == 400
    assert "Email o contraseña incorrectos." in r.text


def test_un_usuario_que_no_existe_da_el_mismo_error_generico(client):
    _sembrar(client)

    r = _ingresar(client, "nadie")

    assert r.status_code == 400
    assert "Email o contraseña incorrectos." in r.text


def test_el_formulario_acepta_escribir_un_usuario(client):
    r = client.get("/ingresar")

    assert 'placeholder="Email o usuario"' in r.text or "Email o usuario" in r.text
    assert 'type="email"' not in r.text  # el navegador rechazaría "jveyes" sin arroba


def test_crear_personal_con_un_usuario_ya_usado_se_rechaza(client):
    admin = _sembrar(client)

    with pytest.raises(ValueError, match="jveyes"):
        create_staff(client.db, admin, "JVeyes@papyrus.com.co", "Otro Jesus", _PW, RolUsuario.OPERADOR)


def test_si_dos_cuentas_viejas_comparten_usuario_ese_usuario_no_entra_pero_el_correo_si(client):
    """Cuentas creadas antes de esta regla: no se adivina a cuál de las dos entrar."""
    _sembrar(client)
    client.db.add(Usuario(nombre="Duplicado", email="jveyes@papyrus.com.co",
                          password_hash=client.db.query(Usuario).filter(Usuario.email == "jveyes@gmail.com").one().password_hash,
                          rol=RolUsuario.OPERADOR))
    client.db.commit()

    assert verify_credentials(client.db, "jveyes", _PW) is None
    assert verify_credentials(client.db, "jveyes@gmail.com", _PW) is not None
