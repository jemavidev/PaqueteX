# -*- coding: utf-8 -*-
"""
Layout de marca para correos de PaqueteX -- logo de Papyrus + enlace a
Consultar paquetes, envolviendo un asunto+cuerpo YA RESUELTOS (sin
placeholders crudos). `.scratch/plantillas-notificacion-multicanal`,
ticket 03.

Misma función que usará el envío real de Email para eventos de paquete el
día que se conecte (fuera de esta rebanada) -- así la vista previa de
`/administracion/notificaciones` y el envío real nunca divergen en cómo se
ve el correo. Estilos inline a propósito, mismo criterio que
`_cuerpo_correo_reset_html` (`web/routes/password_reset.py`): la mayoría de
clientes de correo ignoran o recortan `<style>`/clases CSS.

`base_url` se recibe como parámetro en vez de leerse acá (`public_base_url()`
vive en `app.web.config` -- el dominio no importa de la capa web) para que
esta función se quede pura y testeable sin depender de variables de
entorno."""

import html

_NOMBRE_REMITENTE = "PaqueteX - Papyrus"  # mismo texto que `smtp_email_sender.py`


def envolver_html(asunto: str, cuerpo_texto: str, base_url: str) -> str:
    """Envuelve `asunto`+`cuerpo_texto` (ya resueltos) en el layout de marca:
    logo centrado arriba, el cuerpo como párrafo(s) -- una `<p>` por línea de
    `cuerpo_texto` --, y un pie con enlaces a `/consultar` (seguimiento de
    paquetes) y `/ayuda` (ambas páginas públicas reales, sin sesión) más el
    nombre del remitente."""
    logo = f"{base_url}/static/branding/papyrus-logo.png"
    enlace_consultar = f"{base_url}/consultar"
    enlace_ayuda = f"{base_url}/ayuda"
    asunto_seguro = html.escape(asunto)
    lineas = cuerpo_texto.splitlines() or [cuerpo_texto]
    parrafos = "".join(
        f'<p style="margin:0 0 16px;">{html.escape(linea)}</p>' for linea in lineas
    )
    return f"""\
<!doctype html>
<html lang="es">
<body style="margin:0;padding:0;background-color:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f3f4f6;padding:32px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background-color:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1);">
          <tr>
            <td align="center" style="padding:32px 32px 16px;">
              <img src="{logo}" alt="PAPYRUS" style="max-width:180px;height:auto;">
            </td>
          </tr>
          <tr>
            <td style="padding:0 32px 32px;color:#1a1a1a;font-size:15px;line-height:1.6;">
              <h1 style="margin:0 0 16px;font-size:17px;font-weight:700;color:#1a1a1a;">{asunto_seguro}</h1>
              {parrafos}
              <p style="margin:24px 0 0;font-size:13px;color:#6b7280;">
                <a href="{enlace_consultar}" style="color:#1e40af;">Consultar mis paquetes</a>
                &nbsp;·&nbsp;
                <a href="{enlace_ayuda}" style="color:#1e40af;">Ayuda</a>
              </p>
              <p style="margin:8px 0 0;font-size:12px;color:#9ca3af;">{html.escape(_NOMBRE_REMITENTE)}</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
