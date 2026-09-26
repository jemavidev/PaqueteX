# -*- coding: utf-8 -*-
"""
Pantalla "Respaldos" (`.scratch/respaldos-y-restauracion`, ticket 08) -- Seam B por HTTP: solo ADMIN, lista de los
respaldos del disco del servidor, descarga `.zip` y comando de restauración listo para copiar.
"""

import io
import zipfile
from datetime import datetime, timezone

import pytest

from app.domain.operacion_respaldo_service import registrar_avance, terminar_operacion
from app.domain.respaldo_service import Instalacion, MotivoRespaldo, crear_respaldo
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

_PW = "Contrasena1"
_URL = "/administracion/respaldos"


def _login_admin(client, email="admin@club.com"):
    create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


@pytest.fixture()
def respaldos(tmp_path, monkeypatch, migrated_db_url):
    """Dos respaldos reales en una carpeta de respaldos propia de la prueba."""
    monkeypatch.setenv("RESPALDO_DIR", str(tmp_path))
    instalacion = Instalacion(database_url=migrated_db_url, dominio="test.papyrus.com.co", commit="abc1234")
    viejo = crear_respaldo(instalacion, tmp_path, MotivoRespaldo.DIARIO, ahora=datetime(2026, 9, 24, 8, 0, tzinfo=timezone.utc))
    nuevo = crear_respaldo(instalacion, tmp_path, MotivoRespaldo.A_PEDIDO, ahora=datetime(2026, 9, 25, 15, 30, tzinfo=timezone.utc))
    return viejo, nuevo


def test_sin_sesion_redirige_a_login(client, respaldos):
    r = client.get(_URL, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/ingresar")


def test_un_operador_no_puede_ver_ni_descargar_respaldos(client, respaldos):
    _login_operador(client)
    assert client.get(_URL).status_code == 403
    assert client.get(f"{_URL}/{respaldos[0].carpeta.name}/descargar").status_code == 403


def test_el_admin_ve_los_respaldos_del_disco_del_mas_nuevo_al_mas_viejo(client, respaldos):
    _login_admin(client)
    html = client.get(_URL).text

    viejo, nuevo = respaldos
    assert html.index(nuevo.carpeta.name) < html.index(viejo.carpeta.name)
    # Issue 411: fechas amigables ("vie 25 sep" / "10:30 a. m.") en vez de "2026-09-25 10:30".
    assert "vie 25 sep" in html and "10:30 a. m." in html and "A pedido" in html
    assert "jue 24 sep" in html and "3:00 a. m." in html and "Diario" in html
    assert "paquetex-respaldos" in html  # dónde están los anteriores (S3)


def test_cada_respaldo_muestra_el_comando_exacto_para_restaurarlo(client, respaldos):
    _login_admin(client)
    html = client.get(_URL).text

    nombre = respaldos[0].carpeta.name
    assert f"scripts/respaldos/restaurar.sh /home/ubuntu/paquetex-respaldos/{nombre}" in html


def test_descargar_un_respaldo_entrega_un_zip_con_todos_sus_archivos(client, respaldos):
    _login_admin(client)
    carpeta = respaldos[1].carpeta

    r = client.get(f"{_URL}/{carpeta.name}/descargar")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert f'filename="{carpeta.name}.zip"' in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert sorted(z.namelist()) == sorted(f"{carpeta.name}/{a.name}" for a in carpeta.iterdir())
        assert z.read(f"{carpeta.name}/base_datos.dump") == (carpeta / "base_datos.dump").read_bytes()


@pytest.mark.parametrize("nombre", ["no_existe", "..", "%2E%2E%2Fetc"])
def test_no_se_puede_descargar_nada_que_no_sea_un_respaldo(client, respaldos, nombre):
    _login_admin(client)
    assert client.get(f"{_URL}/{nombre}/descargar").status_code == 404


def test_el_menu_de_datos_enlaza_la_pantalla_de_respaldos(client, respaldos):
    _login_admin(client)
    html = client.get("/paquetes").text
    i = html.index('data-cat-panel="datos"')
    assert f'href="{_URL}"' in html[i : html.index("</div>", i)]


# --------------------------------------------------------------------------------------------------------------------
# Ticket 09 -- "Respaldar ahora": se lanza en segundo plano y la pantalla muestra su estado (guardado en la base).
# --------------------------------------------------------------------------------------------------------------------
class LanzadorFalso:
    """Reemplaza el proceso en segundo plano: anota qué se lanzó y, si se le pide, lo termina en el acto."""

    def __init__(self, session):
        self.session = session
        self.lanzadas = []
        self.terminar_con = None  # None = queda en curso; (ok, detalle) = termina así, como lo haría el proceso

    def __call__(self, operacion_id, tipo):
        self.lanzadas.append((operacion_id, tipo.value))
        if self.terminar_con is not None:
            terminar_operacion(self.session, operacion_id, *self.terminar_con)
            self.session.commit()


@pytest.fixture()
def lanzador(client):
    from app.web.routes.admin_respaldos import get_lanzador_respaldo

    falso = LanzadorFalso(client.db)
    client.app.dependency_overrides[get_lanzador_respaldo] = lambda: falso
    return falso


def test_respaldar_ahora_lanza_el_respaldo_y_la_pantalla_lo_muestra_en_curso(client, respaldos, lanzador):
    _login_admin(client)

    r = client.post(f"{_URL}/ahora", follow_redirects=False)

    assert r.status_code == 303 and r.headers["location"] == _URL
    assert len(lanzador.lanzadas) == 1
    assert "Respaldo a pedido en curso" in client.get(_URL).text


def test_cuando_termina_la_pantalla_dice_si_salio_bien_o_mal(client, respaldos, lanzador):
    _login_admin(client)
    lanzador.terminar_con = (False, "subida a S3: S3 no responde")

    client.post(f"{_URL}/ahora")

    html = client.get(_URL).text
    assert "Respaldo a pedido: FALLÓ" in html and "S3 no responde" in html


def test_no_se_lanza_un_segundo_respaldo_mientras_otro_esta_en_curso(client, respaldos, lanzador):
    _login_admin(client)
    client.post(f"{_URL}/ahora")

    r = client.post(f"{_URL}/ahora")

    assert len(lanzador.lanzadas) == 1
    assert "Ya hay un respaldo en curso" in r.text


def test_un_operador_no_puede_lanzar_respaldos(client, respaldos, lanzador):
    _login_operador(client)
    assert client.post(f"{_URL}/ahora").status_code == 403
    assert lanzador.lanzadas == []



# --------------------------------------------------------------------------------------------------------------------
# Ticket 10 -- copia incremental de las fotos al servidor, en segundo plano y con avance visible.
# --------------------------------------------------------------------------------------------------------------------
def test_copiar_las_fotos_se_lanza_aparte_y_muestra_el_avance(client, respaldos, lanzador):
    _login_admin(client)

    r = client.post(f"{_URL}/fotos/copiar", follow_redirects=False)

    assert r.status_code == 303
    (operacion_id, tipo), = lanzador.lanzadas
    assert tipo == "copia_fotos"
    registrar_avance(client.db, operacion_id, 1234, 7511)
    client.db.commit()
    assert "Copiando fotos: 1.234 de 7.511" in client.get(_URL).text


def test_la_copia_de_fotos_no_bloquea_ni_es_bloqueada_por_un_respaldo(client, respaldos, lanzador):
    _login_admin(client)
    client.post(f"{_URL}/ahora")

    client.post(f"{_URL}/fotos/copiar")

    assert [t for _, t in lanzador.lanzadas] == ["respaldo", "copia_fotos"]


def test_un_operador_no_puede_copiar_fotos(client, respaldos, lanzador):
    _login_operador(client)
    assert client.post(f"{_URL}/fotos/copiar").status_code == 403


# --------------------------------------------------------------------------------------------------------------------
# Ticket 11 -- descargar las fotos copiadas al servidor: "solo las nuevas" (desde la última descarga) o "todas".
# --------------------------------------------------------------------------------------------------------------------
_BASE_S3 = "https://paquetex-staging-fotos.s3.us-east-1.amazonaws.com/"


@pytest.fixture()
def fotos(client, tmp_path, monkeypatch):
    """Fotos registradas en el sistema y ya copiadas al servidor (misma estructura que en S3)."""
    from app.domain.paquete import EstadoPaquete, Paquete
    from app.domain.paquete_foto import PaqueteFoto
    from app.domain.persona import Persona

    copia = tmp_path / "fotos-copia"
    monkeypatch.setenv("RESPALDO_FOTOS_DIR", str(copia))
    persona = Persona(telefono="+573001112233", nombre="Ana")
    client.db.add(persona)
    client.db.flush()
    paquete = Paquete(access_code="FOTO", announced_by_persona_id=persona.id, recipient_name="Ana", estado=EstadoPaquete.RECIBIDO)
    client.db.add(paquete)
    client.db.flush()

    def agregar(clave, datos, creada):
        client.db.add(PaqueteFoto(paquete_id=paquete.id, url=_BASE_S3 + clave, created_at=creada))
        client.db.commit()
        (copia / clave).parent.mkdir(parents=True, exist_ok=True)
        (copia / clave).write_bytes(datos)

    agregar("paquetes-recibidos-imagenes/a1.jpg", b"foto a1", datetime(2026, 9, 1, tzinfo=timezone.utc))
    agregar("paquetes-recibidos-imagenes/a2.jpg", b"foto a2", datetime(2026, 9, 2, tzinfo=timezone.utc))
    return agregar


def _nombres_zip(r):
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        return sorted(z.namelist())


def test_descargar_todas_las_fotos_trae_la_misma_estructura_de_s3(client, respaldos, fotos):
    _login_admin(client)

    r = client.get(f"{_URL}/fotos/descargar?cuales=todas")

    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert _nombres_zip(r) == ["paquetes-recibidos-imagenes/a1.jpg", "paquetes-recibidos-imagenes/a2.jpg"]


def test_solo_las_nuevas_trae_lo_llegado_desde_la_ultima_descarga(client, respaldos, fotos):
    _login_admin(client)
    assert len(_nombres_zip(client.get(f"{_URL}/fotos/descargar?cuales=nuevas"))) == 2  # la primera vez: todas

    fotos("paquetes-recibidos-imagenes/b1.jpg", b"foto nueva", datetime.now(timezone.utc))
    r = client.get(f"{_URL}/fotos/descargar?cuales=nuevas")

    assert _nombres_zip(r) == ["paquetes-recibidos-imagenes/b1.jpg"]
    # "Todas" sigue trayendo todo, sin importar la marca.
    assert len(_nombres_zip(client.get(f"{_URL}/fotos/descargar?cuales=todas"))) == 3


def test_la_pantalla_dice_cuantas_fotos_nuevas_hay_desde_la_ultima_descarga(client, respaldos, fotos):
    _login_admin(client)
    assert "Nunca se han descargado" in client.get(_URL).text

    client.get(f"{_URL}/fotos/descargar?cuales=nuevas")
    fotos("paquetes-recibidos-imagenes/b1.jpg", b"x" * 2_500_000, datetime.now(timezone.utc))

    html = client.get(_URL).text
    assert "1 foto nueva (≈ 2.5 MB)" in html and "desde el" in html


def test_un_operador_no_puede_descargar_fotos(client, respaldos, fotos):
    _login_operador(client)
    assert client.get(f"{_URL}/fotos/descargar?cuales=todas").status_code == 403


def test_una_foto_que_aun_no_estaba_copiada_sale_en_la_siguiente_descarga_de_nuevas(client, respaldos, fotos, tmp_path):
    from app.domain.paquete import Paquete
    from app.domain.paquete_foto import PaqueteFoto

    _login_admin(client)
    # Registrada, pero la copia al servidor todavía no la trajo.
    paquete = client.db.query(Paquete).filter_by(access_code="FOTO").one()
    client.db.add(PaqueteFoto(paquete_id=paquete.id, url=_BASE_S3 + "paquetes-recibidos-imagenes/tarde.jpg",
                              created_at=datetime(2026, 9, 3, tzinfo=timezone.utc)))
    client.db.commit()
    assert "paquetes-recibidos-imagenes/tarde.jpg" not in _nombres_zip(client.get(f"{_URL}/fotos/descargar?cuales=nuevas"))

    # Se copia después al servidor: la siguiente "solo las nuevas" la trae.
    copia = tmp_path / "fotos-copia" / "paquetes-recibidos-imagenes"
    (copia / "tarde.jpg").write_bytes(b"llego tarde")
    assert "paquetes-recibidos-imagenes/tarde.jpg" in _nombres_zip(client.get(f"{_URL}/fotos/descargar?cuales=nuevas"))


def test_cada_respaldo_de_la_lista_dice_si_se_subio_a_s3(client, tmp_path, monkeypatch, migrated_db_url):
    from app.domain.email_sender import ConsoleEmailSender
    from app.domain.respaldo_service import Avisos, ejecutar_respaldo

    class Destino:
        def subir(self, clave, ruta):
            pass

    monkeypatch.setenv("RESPALDO_DIR", str(tmp_path))
    instalacion = Instalacion(database_url=migrated_db_url, dominio="test.papyrus.com.co", commit="abc1234")
    avisos = Avisos(sender=ConsoleEmailSender(), destinatarios=[], uso_disco=lambda _c: 0.1)
    subido = ejecutar_respaldo(instalacion, tmp_path, MotivoRespaldo.DIARIO, Destino(), avisos,
                               ahora=datetime(2026, 9, 24, 8, 0, tzinfo=timezone.utc))
    local = ejecutar_respaldo(instalacion, tmp_path, MotivoRespaldo.A_PEDIDO, None, avisos,
                              ahora=datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc))
    _login_admin(client)

    html = client.get(_URL).text

    def fila(nombre):
        i = html.index(f'data-respaldo="{nombre}"')
        return html[i : html.index("</article>", i)]  # issue 411: cada respaldo es una tarjeta

    assert "En S3: diario" in fila(subido.carpeta.name)
    assert "Solo en el servidor" in fila(local.carpeta.name)


def test_si_no_se_puede_iniciar_el_proceso_la_operacion_no_queda_en_curso(client, respaldos):
    from app.web.routes.admin_respaldos import get_lanzador_respaldo

    def lanzador_roto(operacion_id, tipo):
        raise OSError("sin memoria para un proceso nuevo")

    client.app.dependency_overrides[get_lanzador_respaldo] = lambda: lanzador_roto
    _login_admin(client)

    r = client.post(f"{_URL}/ahora")

    assert "No se pudo iniciar" in r.text
    assert "Respaldo a pedido: FALLÓ" in client.get(_URL).text
