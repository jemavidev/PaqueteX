# -*- coding: utf-8 -*-
"""
Aplica cambios de credenciales de proveedores al `.env` del servidor de
despliegue vía SSH -- Fase 2, `.scratch/administracion-proveedores/spec.md`,
issue 04. Es el único lugar de la app que sostiene una llave capaz de tocar
el servidor; issue 05 (formulario de credenciales) es su único caller.

Decisión de grilling (spec, "Mecanismo de aplicación de credenciales"): la
llave SSH está restringida en el propio servidor por un `command=` forzado
en `authorized_keys` (issue 06, fuera de este repo) a UNA sola operación --
ni un socket de Docker montado en la app, ni secretos pasados por un
`workflow_dispatch` de GitHub Actions (quedarían visibles en el log de la
ejecución). Este módulo es la mitad "cliente" de ese contrato; valida el
allowlist ADEMÁS del lado servidor (defensa en profundidad -- no confía en
que el `command=` remoto sea la única barrera).

**Contrato del payload** (la otra mitad vive en el script remoto de issue 06,
en un repo/lugar distinto -- documentado acá porque no hay forma de
compartir código entre ambos): por cada llamada, se manda por stdin un
bloque de texto UTF-8, una línea por cambio, `CLAVE=VALOR\\n` -- sin líneas
en blanco, sin comentarios, `VALOR` nunca contiene un salto de línea (se
rechaza antes de conectar si lo trae). El comando que se le pide ejecutar al
servidor es irrelevante en la práctica (`authorized_keys` lo reemplaza por
el `command=` forzado) -- se manda un nombre descriptivo solo para que quede
legible en cualquier log de auditoría de SSH del lado servidor.

**Host key**: se exige que el host ya esté en `_RUTA_KNOWN_HOSTS` --
`RejectPolicy` falla cerrado ante un host no reconocido, nunca lo agrega
solo. Aprovisionar ese archivo (ej. `ssh-keyscan` contra el propio servidor
durante el aprovisionamiento) es responsabilidad de issue 06, igual que la
llave privada misma. Ruta explícita, NUNCA `load_system_host_keys()` sin
argumento -- verificado en vivo (issue 06): esa forma corta SOLO revisa
`~/.ssh/known_hosts` del usuario que corre el proceso (root en este
contenedor, sin ese archivo) -- a diferencia de lo que su propio docstring
sugiere, NO hace fallback a `/etc/ssh/ssh_known_hosts` por su cuenta.

Variables de entorno requeridas: `DEPLOY_SSH_HOST`, `DEPLOY_SSH_KEY_PATH`
(ruta al archivo de la llave privada). `DEPLOY_SSH_USER` es opcional,
default `"ubuntu"` (mismo usuario que ya usa el workflow de deploy existente,
`.github/workflows/ci.yml` del repo `jemavidev/PaqueteX`).
"""

import os

import paramiko

from app.domain.proveedores_catalogo import variables_permitidas

_TIMEOUT_SEGUNDOS = 15.0
_USUARIO_POR_DEFECTO = "ubuntu"
_COMANDO_DESCRIPTIVO = "aplicar-config-proveedores"
# Montado por docker-compose.yml del repo de deploy (issue 06) -- mismo
# archivo para cualquier usuario que corra el proceso, a diferencia de
# `~/.ssh/known_hosts` (depende de HOME, ver docstring del módulo).
_RUTA_KNOWN_HOSTS = "/etc/ssh/ssh_known_hosts"


class VariableNoPermitida(ValueError):
    """Una o más claves de `cambios` no están en el allowlist derivado del
    catálogo de proveedores (`proveedores_catalogo.variables_permitidas()`)
    -- rechazado ANTES de intentar conectar."""


class ErrorAplicandoCredenciales(Exception):
    """La conexión SSH o el comando remoto fallaron -- ninguna credencial
    queda aplicada de forma confiable (nunca un "guardado" silencioso;
    quien llama debe mostrar esto como error, no como éxito)."""


def _validar_allowlist(cambios: dict[str, str]) -> None:
    no_permitidas = sorted(set(cambios) - variables_permitidas())
    if no_permitidas:
        raise VariableNoPermitida(
            "Variable(s) fuera del allowlist de proveedores: " + ", ".join(no_permitidas)
        )


def _armar_payload(cambios: dict[str, str]) -> bytes:
    lineas = []
    for clave, valor in cambios.items():
        if "\n" in valor or "\r" in valor:
            raise ValueError(f"El valor de {clave!r} no puede contener saltos de línea.")
        lineas.append(f"{clave}={valor}")
    if not lineas:
        return b""  # `cambios` vacío -- nunca una línea en blanco suelta.
    return ("\n".join(lineas) + "\n").encode("utf-8")


def _config() -> tuple[str, str, str]:
    """`(host, usuario, ruta_llave)` -- mismo criterio que `liwa_sender._config()`:
    valida TODAS las variables antes de intentar cualquier red, y lanza el
    mismo tipo de excepción que el resto de la función (nunca un `KeyError`
    crudo que `except (paramiko.SSHException, OSError)` no atraparía)."""
    host = os.environ.get("DEPLOY_SSH_HOST")
    ruta_llave = os.environ.get("DEPLOY_SSH_KEY_PATH")
    if not (host and ruta_llave):
        raise ErrorAplicandoCredenciales(
            "Configuración de despliegue SSH incompleta -- se requieren "
            "DEPLOY_SSH_HOST y DEPLOY_SSH_KEY_PATH."
        )
    return host, os.environ.get("DEPLOY_SSH_USER", _USUARIO_POR_DEFECTO), ruta_llave


def aplicar_credenciales_proveedor(cambios: dict[str, str]) -> None:
    """Aplica `cambios` (`{NOMBRE_VARIABLE: valor_nuevo}`) al `.env` del
    servidor y reinicia el contenedor para que las recargue.

    Sin nada que cambiar (`cambios` vacío), no conecta -- no hay razón para
    tocar el servidor por un guardado que no tocó ninguna credencial.

    Raises:
        VariableNoPermitida: alguna clave de `cambios` no es una variable de
            proveedor conocida -- no se intenta ninguna conexión.
        ErrorAplicandoCredenciales: configuración de despliegue incompleta,
            fallo de conexión, timeout, o el script remoto terminó con
            código de salida distinto de cero -- SIEMPRE este único tipo,
            nunca un `KeyError`/`OSError` crudo escapando sin envolver.
    """
    _validar_allowlist(cambios)
    if not cambios:
        return
    payload = _armar_payload(cambios)
    host, usuario, ruta_llave = _config()

    cliente = paramiko.SSHClient()
    cliente.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        # Ruta explícita adentro del `try`: `HostKeys.load()` con un
        # filename explícito SÍ deja escapar `IOError` (a diferencia de la
        # forma sin argumento, que la traga) -- si el archivo no está
        # montado, esto debe convertirse en `ErrorAplicandoCredenciales`
        # como cualquier otro fallo, no un `OSError` crudo.
        cliente.load_system_host_keys(_RUTA_KNOWN_HOSTS)
        cliente.connect(
            hostname=host,
            username=usuario,
            key_filename=ruta_llave,
            timeout=_TIMEOUT_SEGUNDOS,
        )
        stdin, stdout, stderr = cliente.exec_command(
            _COMANDO_DESCRIPTIVO, timeout=_TIMEOUT_SEGUNDOS
        )
        stdin.write(payload)
        stdin.flush()
        stdin.channel.shutdown_write()

        codigo_salida = stdout.channel.recv_exit_status()
        if codigo_salida != 0:
            detalle = stderr.read().decode("utf-8", errors="replace").strip()
            mensaje = f"El script remoto terminó con código {codigo_salida}"
            raise ErrorAplicandoCredenciales(f"{mensaje}: {detalle}" if detalle else mensaje)
    except (paramiko.SSHException, OSError) as error:
        raise ErrorAplicandoCredenciales(
            f"No se pudo conectar o ejecutar por SSH: {error}"
        ) from error
    finally:
        cliente.close()
