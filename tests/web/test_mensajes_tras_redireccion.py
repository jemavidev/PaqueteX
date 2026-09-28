"""Issue 429 (.scratch/pendientes-cliente): redirecciones que agregaban un parámetro de confirmación que ninguna vista
leía -- el mensaje nunca se mostraba."""

from app.domain.staff_service import create_initial_admin

_PW = "Contrasena1"


def test_otp_explica_que_el_telefono_se_actualizo(client):
    r = client.get("/otp", params={"telefono_actualizado": "1"})

    assert "Tu teléfono se actualizó" in r.text


def test_otp_confirma_que_la_cuenta_fue_eliminada(client):
    r = client.get("/otp", params={"cuenta_eliminada": "1"})

    assert "Tu cuenta fue eliminada" in r.text


def test_otp_sin_parametros_no_muestra_ninguno_de_los_dos(client):
    r = client.get("/otp")

    assert "Tu teléfono se actualizó" not in r.text and "Tu cuenta fue eliminada" not in r.text


def test_residentes_confirma_que_el_residente_fue_eliminado(client):
    create_initial_admin(client.db, "staff@club.com", "Operador", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": "staff@club.com", "password": _PW})

    assert "Residente eliminado." in client.get("/residentes", params={"eliminado": "1"}).text
    assert "Residente eliminado." not in client.get("/residentes").text
