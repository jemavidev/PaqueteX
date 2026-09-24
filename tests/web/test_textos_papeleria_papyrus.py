# -*- coding: utf-8 -*-
"""
Hacia el residente, el servicio lo presta "el personal de Papyrus" en "la papelería Papyrus" -- nunca "portería" ni
"portero" (issue 394, `.scratch/pendientes-cliente`): PAQUETEX no atiende la portería de un edificio, lo opera la
papelería Papyrus.
"""

import pytest

from app.domain.notificacion_service import asunto_por_defecto
from app.domain.paquete import EstadoPaquete


@pytest.mark.parametrize("ruta", ["/como-funciona", "/terminos", "/privacidad", "/anunciar", "/otp", "/consultar"])
def test_las_paginas_publicas_no_hablan_de_porteria(client, ruta):
    r = client.get(ruta)

    assert r.status_code == 200
    texto = r.text.lower()
    assert "portería" not in texto and "porteria" not in texto and "portero" not in texto


def test_como_funciona_nombra_a_la_papeleria_papyrus(client):
    r = client.get("/como-funciona")

    assert "la papelería Papyrus" in r.text
    assert "El personal de Papyrus lo registra" in r.text


def test_el_asunto_del_correo_de_recibido_nombra_a_la_papeleria():
    assert asunto_por_defecto(EstadoPaquete.RECIBIDO) == "Tu paquete ya está en la papelería Papyrus"
