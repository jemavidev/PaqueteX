# -*- coding: utf-8 -*-
"""
Seam de navegador real — OTP de 6 dígitos: el código completo que pega el celular (autocompletado desde el SMS,
`autocomplete="one-time-code"`) se reparte en las 6 casillas y envía el formulario solo (issue 384,
`.scratch/pendientes-cliente`).
"""


def test_el_codigo_pegado_en_la_primera_casilla_se_reparte_y_se_envia_solo(app_viva, pagina):
    pagina.goto(f"{app_viva.url}/otp/verificar?telefono=%2B573001234567")

    with pagina.expect_navigation():
        pagina.fill('[data-otp-digito="0"]', "482913")  # lo que hace el autocompletado del celular

    # Se envió con el código completo: sin OTP vigente, vuelve con el mensaje genérico.
    assert "Código inválido o expirado." in pagina.content()


def test_teclear_digito_a_digito_sigue_funcionando(app_viva, pagina):
    pagina.goto(f"{app_viva.url}/otp/verificar?telefono=%2B573001234567")
    pagina.click('[data-otp-digito="0"]')

    pagina.keyboard.type("48291")
    valores = [pagina.input_value(f'[data-otp-digito="{i}"]') for i in range(6)]

    assert valores == ["4", "8", "2", "9", "1", ""]
    assert pagina.evaluate("() => document.activeElement.dataset.otpDigito") == "5"
