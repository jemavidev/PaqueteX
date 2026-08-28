# -*- coding: utf-8 -*-
"""
Seam A — Layout de marca de Email (`envolver_html`), `.scratch/plantillas-
notificacion-multicanal`, ticket 03.

Comportamiento observable: el HTML resultante incluye el asunto, el cuerpo
(ya resuelto, sin placeholders), el logo, y un enlace de "sitio" -- sin
asumir nada del markup exacto alrededor.
"""

from app.domain.notificacion_service import resolver_plantilla, variables_ejemplo
from app.domain.plantilla_email_html import envolver_html


def test_envolver_html_incluye_asunto_cuerpo_logo_y_enlace():
    html = envolver_html(
        asunto="Tu paquete llegó",
        cuerpo_texto="Hola Juan Pérez, ya está en portería.",
        base_url="https://paquetex.papyrus.com.co",
    )

    assert "Tu paquete llegó" in html
    assert "Hola Juan Pérez, ya está en portería." in html
    assert "https://paquetex.papyrus.com.co/static/branding/papyrus-logo.png" in html
    assert "https://paquetex.papyrus.com.co/consultar" in html
    assert "https://paquetex.papyrus.com.co/ayuda" in html


def test_envolver_html_escapa_html_del_contenido():
    html = envolver_html(
        asunto="<script>alert(1)</script>",
        cuerpo_texto="<b>hola</b>",
        base_url="https://example.com",
    )

    assert "<script>" not in html
    assert "<b>hola</b>" not in html
    assert "&lt;script&gt;" in html


def test_envolver_html_preserva_lineas_multiples():
    html = envolver_html(
        asunto="Asunto",
        cuerpo_texto="Primera línea.\nSegunda línea.",
        base_url="https://example.com",
    )

    assert "Primera línea." in html
    assert "Segunda línea." in html


def test_variables_ejemplo_incluye_nombre_codigo_y_motivo():
    variables = variables_ejemplo("NO_RECLAMADO")
    assert variables["recipient_name"]
    assert variables["access_code"]
    assert variables["motivo"] == "No reclamado"


def test_variables_ejemplo_sin_motivo_no_falla():
    variables = variables_ejemplo()
    assert variables["motivo"] == ""


def test_resolver_plantilla_sustituye_las_variables():
    resultado = resolver_plantilla("Hola {recipient_name}", {"recipient_name": "Ana"})
    assert resultado == "Hola Ana"


def test_resolver_plantilla_tolera_una_llave_suelta_sin_reventar():
    # Admin editando a medio escribir -- una `{` sin cerrar no debe tumbar
    # la vista previa completa.
    resultado = resolver_plantilla("Hola { esto no cierra", {"recipient_name": "Ana"})
    assert resultado == "Hola { esto no cierra"
