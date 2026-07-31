# -*- coding: utf-8 -*-
"""
`FailoverSmsSender` (ticket 01, `.scratch/sms-failover-twilio-sns/`) —
probado con senders falsos, independiente de cualquier proveedor real.
"""

import pytest

from app.domain.sms_failover import ErrorConectividadSms, FailoverSmsSender


class _SenderExitoso:
    def __init__(self):
        self.llamadas = []

    def enviar(self, destino, mensaje):
        self.llamadas.append((destino, mensaje))


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
    primero = _SenderExitoso()
    segundo = _SenderExitoso()

    FailoverSmsSender([primero, segundo]).enviar("+573001234567", "hola")

    assert primero.llamadas == [("+573001234567", "hola")]
    assert segundo.llamadas == []


def test_falla_de_conectividad_reintenta_con_el_siguiente():
    primero = _SenderQueFallaConectividad()
    segundo = _SenderExitoso()

    FailoverSmsSender([primero, segundo]).enviar("+573001234567", "hola")

    assert primero.llamadas == 1
    assert segundo.llamadas == [("+573001234567", "hola")]


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
