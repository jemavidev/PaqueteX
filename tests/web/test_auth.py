# -*- coding: utf-8 -*-
"""
Capa web — autenticación de staff (login / sesión / current_staff).

Comportamiento observable por HTTP: login válido abre sesión, inválido no (mensaje
genérico), las rutas con privilegios se abren solo con sesión, logout cierra.

`require_admin` (ADMIN vs OPERADOR) se prueba sobre una ruta real en
`test_admin_staff.py` (`/admin/staff`) — el placeholder `/auth/admin/check` que
antes probaba esto aquí fue retirado (rebanada admin-staff).
"""

from app.domain.staff_service import create_initial_admin

_PW = "Contrasena1"


def _seed_admin(client, email="admin@club.com"):
    create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    return email


def test_get_login_renderiza_el_formulario(client):
    r = client.get("/ingresar")
    assert r.status_code == 200
    assert 'name="email"' in r.text and 'name="password"' in r.text


def test_login_valido_abre_sesion_y_me_muestra_al_staff(client):
    email = _seed_admin(client)
    r = client.post("/ingresar", data={"email": email, "password": _PW})
    assert r.status_code == 200  # siguió el redirect a /paquetes
    # sesión abierta: una ruta con privilegios ya no redirige y muestra al
    # staff -- issue 199 quitó el email de esta pantalla (redundante con el
    # dropdown del header), así que se confirma identidad vía el nombre
    # precargado en "Editar mi perfil".
    r2 = client.get("/mi-sesion")
    assert r2.status_code == 200
    assert 'value="ADMIN"' in r2.text


def test_login_invalido_no_abre_sesion_y_mensaje_generico(client):
    _seed_admin(client)
    r = client.post(
        "/ingresar", data={"email": "admin@club.com", "password": "mala12345"}
    )
    assert r.status_code == 400
    assert "incorrect" in r.text.lower()  # "Email o contraseña incorrectos."
    # Sin sesión: una ruta con privilegios manda al login.
    r2 = client.get("/mi-sesion", follow_redirects=False)
    assert r2.status_code == 303
    assert r2.headers["location"].endswith("/ingresar")


# --------------------------------------------------------------------------- #
# Foco condicional (versión móvil, `.scratch/pendientes-cliente`): autofocus
# SOLO en una carga limpia -- con error, activarlo dispara el teclado y tapa
# el mensaje de error en mobile.
# --------------------------------------------------------------------------- #
def test_get_ingresar_limpio_tiene_autofocus(client):
    r = client.get("/ingresar")
    assert "autofocus" in r.text


def test_post_ingresar_con_error_no_tiene_autofocus(client):
    _seed_admin(client)
    r = client.post(
        "/ingresar", data={"email": "admin@club.com", "password": "mala12345"}
    )
    assert r.status_code == 400
    assert "autofocus" not in r.text


def test_me_sin_sesion_redirige_a_login(client):
    r = client.get("/mi-sesion", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_logout_cierra_la_sesion(client):
    email = _seed_admin(client)
    client.post("/ingresar", data={"email": email, "password": _PW})
    # con sesión, /mi-sesion responde 200
    assert client.get("/mi-sesion").status_code == 200
    client.post("/salir")
    # tras logout, vuelve a redirigir al login
    r = client.get("/mi-sesion", follow_redirects=False)
    assert r.status_code == 303


# --------------------------------------------------------------------------- #
# /salir-todo (Grupo 10, Ronda 2) — el único botón de logout que el header
# muestra ahora, cierra staff Y cliente a la vez si ambas están abiertas.
# --------------------------------------------------------------------------- #
def test_salir_todo_cierra_la_sesion_de_staff(client):
    email = _seed_admin(client)
    client.post("/ingresar", data={"email": email, "password": _PW})
    assert client.get("/mi-sesion").status_code == 200

    r = client.post("/salir-todo", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/anunciar")

    assert client.get("/mi-sesion", follow_redirects=False).status_code == 303


def test_salir_todo_cierra_tambien_la_sesion_de_cliente_coexistente(client):
    from app.domain.otp_sender import DevOtpSender
    from app.domain.paquete_lifecycle import receive
    from app.domain.paquete_service import Destinatario, announce
    from app.domain.usuario import RolUsuario, Usuario
    from app.web.otp import get_otp_sender

    email = _seed_admin(client)
    client.post("/ingresar", data={"email": email, "password": _PW})

    # Corrección en vivo 2026-08-02: pedir OTP ahora exige elegibilidad
    # (un Paquete Recibido) -- se siembra uno antes de pedir el código.
    staff = Usuario(nombre="ActorElegibilidad", rol=RolUsuario.OPERADOR)
    client.db.add(staff)
    client.db.flush()
    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Cliente de prueba",
        destinatario=Destinatario.yo_mismo(),
    )
    receive(client.db, p, staff)
    client.db.commit()

    sender = DevOtpSender()
    client.app.dependency_overrides[get_otp_sender] = lambda: sender
    client.post("/otp/solicitar", data={"telefono": "3001234567"})
    codigo = sender.enviados["+573001234567"]
    client.post("/otp/verificar", data={"telefono": "3001234567", "codigo": codigo})

    assert client.get("/mi-sesion").status_code == 200
    assert client.get("/mis-datos").status_code == 200

    client.post("/salir-todo")

    assert client.get("/mi-sesion", follow_redirects=False).status_code == 303
    assert client.get("/mis-datos", follow_redirects=False).status_code == 303


# --------------------------------------------------------------------------- #
# .scratch/pendientes-cliente, issue 196 -- autoservicio: cualquier staff
# (OPERADOR incluido) cambia SU PROPIA contraseña desde "Mi perfil".
# --------------------------------------------------------------------------- #
def _login_operador(client, email="op@club.com"):
    from app.domain.staff_service import create_initial_admin, create_staff
    from app.domain.usuario import RolUsuario

    admin = create_initial_admin(client.db, "admin2@club.com", "Admin", _PW)
    create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})
    return email


def test_operador_cambia_su_propia_password_sin_ser_admin(client):
    email = _login_operador(client)

    r = client.post(
        "/mi-sesion", data={"password": "NuevaClave2", "password_confirmacion": "NuevaClave2"}
    )
    assert r.status_code == 200

    r2 = client.post("/ingresar", data={"email": email, "password": "NuevaClave2"})
    assert r2.status_code == 200


def test_cambiar_password_rechaza_si_no_coinciden(client):
    _login_operador(client)
    r = client.post(
        "/mi-sesion", data={"password": "NuevaClave2", "password_confirmacion": "Distinta2"}
    )
    assert r.status_code == 400


def test_cambiar_password_rechaza_debil(client):
    _login_operador(client)
    r = client.post("/mi-sesion", data={"password": "corta", "password_confirmacion": "corta"})
    assert r.status_code == 400


def test_cambiar_password_sin_sesion_redirige_a_login(client):
    r = client.post(
        "/mi-sesion",
        data={"password": "NuevaClave2", "password_confirmacion": "NuevaClave2"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


# --------------------------------------------------------------------------- #
# .scratch/pendientes-cliente, issue 197 -- autoservicio: cualquier staff
# (OPERADOR incluido) edita su propio nombre, sin poder tocar su rol.
# --------------------------------------------------------------------------- #
def test_operador_edita_su_propio_nombre(client):
    from app.domain.usuario import RolUsuario, Usuario

    _login_operador(client)
    r = client.post("/mi-sesion/editar", data={"nombre": "Nombre Nuevo"})
    assert r.status_code == 200
    assert "NOMBRE NUEVO" in r.text

    client.db.expire_all()
    op = client.db.query(Usuario).filter_by(email="op@club.com").one()
    assert op.nombre == "NOMBRE NUEVO"
    assert op.rol == RolUsuario.OPERADOR  # sigue sin poder cambiar su rol


def test_editar_mi_perfil_no_tiene_campo_de_rol_en_el_form(client):
    _login_operador(client)
    r = client.get("/mi-sesion")
    # El form de "Editar mi perfil" (action=/mi-sesion/editar) no debe traer
    # ningún input `name="rol"` -- ni la posibilidad existe.
    assert 'name="rol"' not in r.text


def test_editar_mi_perfil_nombre_vacio_rechaza(client):
    _login_operador(client)
    r = client.post("/mi-sesion/editar", data={"nombre": "   "})
    assert r.status_code == 400


def test_editar_mi_perfil_sin_sesion_redirige_a_login(client):
    r = client.post("/mi-sesion/editar", data={"nombre": "Alguien"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


# --------------------------------------------------------------------------- #
# .scratch/notificaciones-enviar-prueba, ticket 01 -- teléfono/WhatsApp
# propios del staff (autoservicio, sin gate de rol, igual que el nombre).
# --------------------------------------------------------------------------- #
def test_operador_edita_su_propio_telefono_y_whatsapp(client):
    from app.domain.usuario import Usuario

    _login_operador(client)
    r = client.post(
        "/mi-sesion/editar",
        data={"nombre": "Opa", "telefono": "3001234567", "whatsapp": "3009876543"},
    )
    assert r.status_code == 200

    client.db.expire_all()
    op = client.db.query(Usuario).filter_by(email="op@club.com").one()
    assert op.telefono == "3001234567"
    assert op.whatsapp == "3009876543"


def test_mi_sesion_muestra_el_telefono_y_whatsapp_guardados(client):
    _login_operador(client)
    client.post(
        "/mi-sesion/editar",
        data={"nombre": "Opa", "telefono": "3001234567", "whatsapp": "3009876543"},
    )

    r = client.get("/mi-sesion")
    assert "3001234567" in r.text
    assert "3009876543" in r.text


def test_editar_mi_perfil_telefono_y_whatsapp_vacios_no_falla(client):
    _login_operador(client)
    r = client.post(
        "/mi-sesion/editar", data={"nombre": "Opa", "telefono": "", "whatsapp": ""}
    )
    assert r.status_code == 200
