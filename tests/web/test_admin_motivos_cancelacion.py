# -*- coding: utf-8 -*-
"""
Capa web — `/administracion/motivos-cancelacion` (issue 403, .scratch/pendientes-cliente).

Pantalla propia para el catálogo de motivos de cancelación, mismo molde que
`/administracion/motivos-bloqueo` y `/administracion/motivos-anulacion-cobro`
(antes vivía embebido en el modal CANCELADO de `/administracion/notificaciones`).
Conserva lo que ya tenía ese catálogo y las otras dos no: Editar, y nunca
quedarse sin motivos (cancelar un paquete exige uno).

`motivos_cancelacion` NO se trunca entre tests (la migración lo siembra una
sola vez por sesión, ver `tests/web/conftest.py`) -- cada test deja el
catálogo EXACTAMENTE como lo encontró.
"""

from app.domain.motivo_cancelacion_service import crear_motivo, eliminar_motivo, listar_motivos
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

_PW = "Contrasena1"
_URL = "/administracion/motivos-cancelacion"


def _login_admin(client, email="admin@club.com"):
    create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _crear_motivo_dominio(client, etiqueta):
    m = crear_motivo(client.db, etiqueta)
    client.db.commit()
    return m


def _eliminar_motivo_dominio(client, motivo_id):
    eliminar_motivo(client.db, motivo_id)
    client.db.commit()


def test_sin_sesion_redirige_a_login(client):
    r = client.get(_URL, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_operador_no_puede_ver_ni_tocar_el_catalogo(client):
    _login_operador(client)
    motivo = listar_motivos(client.db)[0]

    assert client.get(_URL).status_code == 403
    assert client.post(_URL, data={"etiqueta": "Motivo nuevo"}).status_code == 403
    assert client.post(f"{_URL}/{motivo.id}/editar", data={"etiqueta": "Cambiado"}).status_code == 403
    assert client.post(f"{_URL}/{motivo.id}/eliminar").status_code == 403

    client.db.expire_all()
    etiquetas = [m.etiqueta for m in listar_motivos(client.db)]
    assert "Motivo nuevo" not in etiquetas and "Cambiado" not in etiquetas
    assert motivo.etiqueta in etiquetas


def test_admin_ve_el_catalogo_con_formulario_para_crear(client):
    _login_admin(client)
    r = client.get(_URL)
    assert r.status_code == 200
    assert "Nuevo motivo de cancelación" in r.text
    assert f'action="{_URL}"' in r.text
    for m in listar_motivos(client.db):
        assert m.etiqueta in r.text


def test_el_menu_de_administracion_enlaza_la_pantalla(client):
    _login_admin(client)
    r = client.get(_URL)
    assert f'href="{_URL}"' in r.text
    assert "Motivos de cancelación" in r.text


def test_crear_motivo_aparece_en_la_lista(client):
    _login_admin(client)
    etiqueta = "Motivo web crear"

    r = client.post(_URL, data={"etiqueta": etiqueta})
    assert r.status_code == 200
    assert etiqueta in r.text
    assert "Motivo creado." in r.text

    client.db.expire_all()
    creado = next(m for m in listar_motivos(client.db) if m.etiqueta == etiqueta)
    _eliminar_motivo_dominio(client, creado.id)


def test_crear_motivo_vacio_o_duplicado_rechaza_sin_alterar_catalogo(client):
    _login_admin(client)
    antes = sorted(m.etiqueta for m in listar_motivos(client.db))

    assert client.post(_URL, data={"etiqueta": "   "}).status_code == 400
    assert client.post(_URL, data={"etiqueta": antes[0]}).status_code == 400

    client.db.expire_all()
    assert sorted(m.etiqueta for m in listar_motivos(client.db)) == antes


def test_editar_motivo_actualiza_la_lista(client):
    _login_admin(client)
    motivo = _crear_motivo_dominio(client, "Motivo web editar original")

    r = client.post(f"{_URL}/{motivo.id}/editar", data={"etiqueta": "Motivo web editar nuevo"})
    assert r.status_code == 200
    assert "Motivo web editar nuevo" in r.text
    assert "Motivo web editar original" not in r.text
    assert "Motivo actualizado." in r.text

    _eliminar_motivo_dominio(client, motivo.id)


def test_editar_motivo_a_etiqueta_duplicada_rechaza_y_reabre_su_modal(client):
    _login_admin(client)
    existente = listar_motivos(client.db)[0].etiqueta
    motivo = _crear_motivo_dominio(client, "Motivo web editar duplicado")

    r = client.post(f"{_URL}/{motivo.id}/editar", data={"etiqueta": existente})
    assert r.status_code == 400
    i = r.text.index(f'id="modal-motivo-cancelacion-editar-{motivo.id}"')
    assert "hidden" not in r.text[i : r.text.index(">", i)]

    client.db.expire_all()
    assert client.db.get(type(motivo), motivo.id).etiqueta == "Motivo web editar duplicado"

    _eliminar_motivo_dominio(client, motivo.id)


def test_borrar_motivo_lo_quita_de_la_lista(client):
    _login_admin(client)
    etiqueta = "Motivo web borrar"
    motivo = _crear_motivo_dominio(client, etiqueta)

    r = client.post(f"{_URL}/{motivo.id}/eliminar")
    assert r.status_code == 200
    assert etiqueta not in r.text
    assert "Motivo eliminado." in r.text

    client.db.expire_all()
    assert etiqueta not in [m.etiqueta for m in listar_motivos(client.db)]


def test_con_un_solo_motivo_no_se_ofrece_eliminar_y_la_ruta_lo_rechaza(client):
    _login_admin(client)
    originales = [(m.id, m.etiqueta) for m in listar_motivos(client.db)]
    for mid, _ in originales[1:]:
        _eliminar_motivo_dominio(client, mid)

    ultimo_id, ultimo_etiqueta = originales[0]
    assert f"modal-motivo-cancelacion-eliminar-{ultimo_id}" not in client.get(_URL).text

    r = client.post(f"{_URL}/{ultimo_id}/eliminar")
    assert r.status_code == 400
    assert "No se puede borrar el último motivo de cancelación." in r.text

    client.db.expire_all()
    assert [m.etiqueta for m in listar_motivos(client.db)] == [ultimo_etiqueta]

    for _, etiqueta in originales[1:]:
        _crear_motivo_dominio(client, etiqueta)
