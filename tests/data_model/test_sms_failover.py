# -*- coding: utf-8 -*-
"""
`FailoverSmsSender` (ticket 01, `.scratch/sms-failover-twilio-sns/`) —
probado con senders falsos, independiente de cualquier proveedor real.
"""

import pytest

from app.domain.sms_failover import ErrorConectividadSms, FailoverSmsSender


class _SenderExitoso:
    def __init__(self, nombre="PROVEEDOR"):
        self.llamadas = []
        self.nombre = nombre

    def enviar(self, destino, mensaje):
        self.llamadas.append((destino, mensaje))
        return self.nombre


class _SenderQueFallaConectividad:
    def __init__(self):
        self.llamadas = 0

    def enviar(self, destino, mensaje):
        self.llamadas += 1
        raise ErrorConectividadSms("no alcanzable")


class _SenderQueRechaza:
    def __init__(self):
        self.llamadas = 0

    def enviar(self, destino, mensaje):
        self.llamadas += 1
        raise RuntimeError("saldo insuficiente")


def test_primero_exitoso_el_segundo_nunca_se_llama():
    primero = _SenderExitoso(nombre="PRIMERO")
    segundo = _SenderExitoso(nombre="SEGUNDO")

    resultado = FailoverSmsSender([primero, segundo]).enviar("+573001234567", "hola")

    assert primero.llamadas == [("+573001234567", "hola")]
    assert segundo.llamadas == []
    assert resultado == "PRIMERO"


def test_falla_de_conectividad_reintenta_con_el_siguiente():
    primero = _SenderQueFallaConectividad()
    segundo = _SenderExitoso(nombre="SEGUNDO")

    resultado = FailoverSmsSender([primero, segundo]).enviar("+573001234567", "hola")

    assert primero.llamadas == 1
    assert segundo.llamadas == [("+573001234567", "hola")]
    # El registro de envíos SMS (ticket 11) necesita saber que fue el
    # SEGUNDO el que de verdad entregó, no el primero de la lista.
    assert resultado == "SEGUNDO"


def test_rechazo_explicito_no_reintenta_y_propaga():
    primero = _SenderQueRechaza()
    segundo = _SenderExitoso()

    with pytest.raises(RuntimeError, match="saldo insuficiente"):
        FailoverSmsSender([primero, segundo]).enviar("+573001234567", "hola")

    assert primero.llamadas == 1
    assert segundo.llamadas == []  # nunca se prueba: el rechazo no es reintentable


def test_todos_fallan_conectividad_propaga_el_ultimo_error():
    primero = _SenderQueFallaConectividad()
    segundo = _SenderQueFallaConectividad()

    with pytest.raises(ErrorConectividadSms):
        FailoverSmsSender([primero, segundo]).enviar("+573001234567", "hola")

    assert primero.llamadas == 1
    assert segundo.llamadas == 1


def test_lista_vacia_lanza_valueerror():
    with pytest.raises(ValueError):
        FailoverSmsSender([])
