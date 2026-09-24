# -*- coding: utf-8 -*-
"""
Limpieza previa del staging (`.scratch/importador-v1-espejo`, ticket 10): antes
de la primera pasada del importador espejo se borra todo lo de residentes y
paquetes, y se conserva la configuración, el censo de apartamentos y los
contactos externos. Seam: `limpiar_datos_de_residentes`, contra el Postgres
efímero.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.apartamento import Apartamento
from app.domain.cobro import Cobro
from app.domain.contacto_externo import ContactoExterno
from app.domain.limpieza_staging_service import LimpiezaRechazada, limpiar_datos_de_residentes
from app.domain.motivo_cancelacion import MotivoCancelacion
from app.domain.ocupante import Ocupante
from app.domain.otp_cliente import OtpCliente
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_foto import PaqueteFoto
from app.domain.persona import Persona
from app.domain.preferencia_notificacion import CanalNotificacion, PersonaPreferenciaNotificacion
from app.domain.registro_sms import RegistroSms, TipoRegistroSms
from app.domain.saldo_contra_entrega import MovimientoSaldoContraEntrega
from app.domain.usuario import RolUsuario, Usuario

pytestmark = pytest.mark.integration

AHORA = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)


def _sembrar_de_todo(db):
    staff = Usuario(nombre="Staff", email="staff@club.test", rol=RolUsuario.ADMIN)
    persona = Persona(telefono="+573001112233", nombre="Residente de prueba")
    db.add_all([staff, persona])
    db.flush()
    paquete = Paquete(access_code="PRUE", announced_by_persona_id=persona.id, recipient_name="Residente",
                      estado=EstadoPaquete.ENTREGADO, announced_at=AHORA, delivered_at=AHORA)
    db.add(paquete)
    db.flush()
    apartamento = db.query(Apartamento).first()
    db.add_all([
        Cobro(paquete_id=paquete.id, monto_base=1500, monto_total=1500, cobrado_por_usuario_id=staff.id, cobrado_en=AHORA),
        PaqueteFoto(paquete_id=paquete.id, url="https://x.test/f.webp"),
        MovimientoSaldoContraEntrega(persona_id=persona.id, monto=5000, paquete_id=paquete.id,
                                     registrado_por_usuario_id=staff.id),
        PersonaPreferenciaNotificacion(persona_id=persona.id, canal=CanalNotificacion.SMS, evento="RECIBIDO", activo=True),
        Ocupante(apartamento_id=apartamento.id, persona_id=persona.id, nombre="Residente", es_principal=True),
        OtpCliente(telefono="+573001112233", codigo_hash="x", expira_en=AHORA + timedelta(minutes=5)),
        RegistroSms(tipo=list(TipoRegistroSms)[0], paquete_id=paquete.id, exitoso=True),
        ContactoExterno(nombre="Contacto externo", fuentes=["GOOGLE_CONTACTS"]),
    ])
    if not db.query(MotivoCancelacion).count():
        db.add(MotivoCancelacion(etiqueta="Otro"))
    db.flush()


_BORRADAS = (Paquete, Persona, Cobro, PaqueteFoto, MovimientoSaldoContraEntrega, PersonaPreferenciaNotificacion,
             Ocupante, OtpCliente, RegistroSms)


def test_borra_todo_lo_de_residentes_y_paquetes(db_session):
    _sembrar_de_todo(db_session)

    resumen = limpiar_datos_de_residentes(db_session)

    for modelo in _BORRADAS:
        assert db_session.query(modelo).count() == 0, modelo.__tablename__
    assert resumen.borrados["paquetes"] == 1
    assert resumen.borrados["personas"] == 1


def test_conserva_configuracion_apartamentos_y_contactos_externos(db_session):
    apartamentos_antes = db_session.query(Apartamento).count()
    _sembrar_de_todo(db_session)

    limpiar_datos_de_residentes(db_session)

    assert db_session.query(Usuario).count() == 1
    assert db_session.query(Apartamento).count() == apartamentos_antes > 0
    assert db_session.query(ContactoExterno).count() == 1
    assert db_session.query(MotivoCancelacion).count() >= 1


def test_simular_cuenta_sin_borrar(db_session):
    _sembrar_de_todo(db_session)

    resumen = limpiar_datos_de_residentes(db_session, simular=True)

    assert resumen.borrados["paquetes"] == 1
    assert resumen.conservados["apartamentos"] > 0
    assert db_session.query(Paquete).count() == 1
    assert db_session.query(Persona).count() == 1


def test_se_niega_si_ya_hay_datos_importados_de_la_v1(db_session):
    _sembrar_de_todo(db_session)
    db_session.query(Persona).one().origen_v1_id = "c-1"
    db_session.flush()

    with pytest.raises(LimpiezaRechazada):
        limpiar_datos_de_residentes(db_session)

    assert db_session.query(Paquete).count() == 1


def test_toda_tabla_del_esquema_esta_clasificada_como_borrar_o_conservar(db_session):
    """Una tabla nueva sin clasificar haría que la limpieza la ignore en
    silencio: esta prueba obliga a decidir explícitamente."""
    from sqlalchemy import text

    from app.domain.limpieza_staging_service import TABLAS_A_BORRAR, TABLAS_CONSERVADAS

    tablas = {
        t for (t,) in db_session.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
    } - {"alembic_version"}

    assert tablas == set(TABLAS_A_BORRAR) | set(TABLAS_CONSERVADAS)
