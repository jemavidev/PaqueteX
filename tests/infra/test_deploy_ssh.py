# -*- coding: utf-8 -*-
"""
`app/infra/deploy_ssh.py` -- mecanismo SSH+allowlist para aplicar
credenciales de proveedores (`.scratch/administracion-proveedores/spec.md`,
issue 04). Unidad, sin red ni servidor real -- `paramiko.SSHClient` va
mockeado en cada test.

Comportamiento observable: una clave fuera del allowlist se rechaza SIN
intentar ninguna conexión; un fallo de conexión o del comando remoto se
propaga como `ErrorAplicandoCredenciales`; el caso de éxito no lanza nada y
manda el payload en el formato exacto que espera el script remoto (issue 06).
"""

import paramiko
import pytest

from app.infra.deploy_ssh import (
    _RUTA_KNOWN_HOSTS,
    ErrorAplicandoCredenciales,
    VariableNoPermitida,
    aplicar_credenciales_proveedor,
)


class _EjecucionFalsa:
    """Simula el trío `(stdin, stdout, stderr)` que devuelve
    `SSHClient.exec_command` -- en `paramiko` real los tres comparten un
    mismo `Channel` subyacente; acá se colapsa a UN objeto que sirve de
    `stdin`/`stdout` a la vez (`.channel` apunta a sí mismo) y también de
    `stderr` (expone `.read()`), mismo espíritu de "un solo doble por límite
    externo" que ya usan `test_sns_sender.py`/`test_liwa_sender.py`."""

    def __init__(self, codigo_salida: int = 0, stderr: bytes = b""):
        self.channel = self
        self.escrito = b""
        self.cerrado_para_escritura = False
        self._codigo_salida = codigo_salida
        self._stderr = stderr

    def write(self, data: bytes):
        self.escrito += data

    def flush(self):
        pass

    def shutdown_write(self):
        self.cerrado_para_escritura = True

    def recv_exit_status(self):
        return self._codigo_salida

    def read(self):
        return self._stderr


class _ClienteSshFalso:
    """Doble de `paramiko.SSHClient` -- graba llamadas, nunca toca red."""

    def __init__(
        self, *, error_conexion=None, codigo_salida=0, stderr=b"", error_known_hosts=None
    ):
        self._error_conexion = error_conexion
        self._error_known_hosts = error_known_hosts
        self.conectado_con = None
        self.cerrado = False
        self.ruta_known_hosts_cargada = None
        self.ejecucion = _EjecucionFalsa(codigo_salida, stderr)

    def load_system_host_keys(self, filename=None):
        if self._error_known_hosts is not None:
            raise self._error_known_hosts
        self.ruta_known_hosts_cargada = filename

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, **kwargs):
        if self._error_conexion is not None:
            raise self._error_conexion
        self.conectado_con = kwargs

    def exec_command(self, comando, timeout=None):
        self.comando_pedido = comando
        return self.ejecucion, self.ejecucion, self.ejecucion

    def close(self):
        self.cerrado = True


def _monkeypatch_env_ssh(monkeypatch):
    monkeypatch.setenv("DEPLOY_SSH_HOST", "52.6.204.211")
    monkeypatch.setenv("DEPLOY_SSH_KEY_PATH", "/fake/key")


def test_variable_fuera_del_allowlist_rechaza_sin_conectar(monkeypatch):
    _monkeypatch_env_ssh(monkeypatch)

    def _no_debe_llamarse(*args, **kwargs):
        raise AssertionError("No debía intentarse ninguna conexión SSH")

    monkeypatch.setattr(paramiko, "SSHClient", _no_debe_llamarse)

    with pytest.raises(VariableNoPermitida, match="DATABASE_URL"):
        aplicar_credenciales_proveedor({"DATABASE_URL": "postgresql://evil"})


def test_valor_con_salto_de_linea_rechaza_sin_conectar(monkeypatch):
    _monkeypatch_env_ssh(monkeypatch)

    def _no_debe_llamarse(*args, **kwargs):
        raise AssertionError("No debía intentarse ninguna conexión SSH")

    monkeypatch.setattr(paramiko, "SSHClient", _no_debe_llamarse)

    with pytest.raises(ValueError, match="no puede contener"):
        aplicar_credenciales_proveedor({"AWS_ACCESS_KEY_ID": "linea1\nOTRA=inyectada"})


def test_fallo_de_conexion_propaga_excepcion(monkeypatch):
    _monkeypatch_env_ssh(monkeypatch)
    falso = _ClienteSshFalso(error_conexion=paramiko.SSHException("timed out"))
    monkeypatch.setattr(paramiko, "SSHClient", lambda: falso)

    with pytest.raises(ErrorAplicandoCredenciales, match="timed out"):
        aplicar_credenciales_proveedor({"AWS_ACCESS_KEY_ID": "AKIAFAKE"})

    assert falso.cerrado is True  # `finally: cliente.close()` corre igual


def test_comando_remoto_con_codigo_de_salida_no_cero_propaga_excepcion(monkeypatch):
    _monkeypatch_env_ssh(monkeypatch)
    falso = _ClienteSshFalso(codigo_salida=1, stderr=b"variable rechazada por el servidor")
    monkeypatch.setattr(paramiko, "SSHClient", lambda: falso)

    with pytest.raises(ErrorAplicandoCredenciales, match="variable rechazada por el servidor"):
        aplicar_credenciales_proveedor({"AWS_ACCESS_KEY_ID": "AKIAFAKE"})


def test_exito_no_lanza_nada_y_manda_el_payload_esperado(monkeypatch):
    _monkeypatch_env_ssh(monkeypatch)
    falso = _ClienteSshFalso(codigo_salida=0)
    monkeypatch.setattr(paramiko, "SSHClient", lambda: falso)

    aplicar_credenciales_proveedor(
        {"AWS_ACCESS_KEY_ID": "AKIAFAKE", "AWS_SECRET_ACCESS_KEY": "shh"}
    )

    assert falso.ejecucion.escrito == b"AWS_ACCESS_KEY_ID=AKIAFAKE\nAWS_SECRET_ACCESS_KEY=shh\n"
    assert falso.ejecucion.cerrado_para_escritura is True
    assert falso.cerrado is True


def test_carga_known_hosts_con_ruta_explicita(monkeypatch):
    """`load_system_host_keys()` sin argumento SOLO revisa `~/.ssh/known_hosts`
    del usuario que corre el proceso (root en el contenedor, sin ese archivo) --
    verificado en vivo contra el paramiko real desplegado (issue 06). Debe
    pasarse SIEMPRE la ruta explícita al `known_hosts` montado por
    docker-compose.yml del repo de deploy."""
    _monkeypatch_env_ssh(monkeypatch)
    falso = _ClienteSshFalso(codigo_salida=0)
    monkeypatch.setattr(paramiko, "SSHClient", lambda: falso)

    aplicar_credenciales_proveedor({"AWS_REGION": "us-east-1"})

    assert falso.ruta_known_hosts_cargada == _RUTA_KNOWN_HOSTS


def test_known_hosts_faltante_propaga_como_error_aplicando_credenciales(monkeypatch):
    """A diferencia de la forma sin argumento (que traga `IOError`), pasar una
    ruta explícita SÍ deja escapar `IOError` si el archivo no está montado --
    debe envolverse igual que cualquier otro fallo, nunca un `OSError` crudo."""
    _monkeypatch_env_ssh(monkeypatch)
    falso = _ClienteSshFalso(error_known_hosts=IOError("No such file or directory"))
    monkeypatch.setattr(paramiko, "SSHClient", lambda: falso)

    with pytest.raises(ErrorAplicandoCredenciales, match="No such file or directory"):
        aplicar_credenciales_proveedor({"AWS_REGION": "us-east-1"})

    assert falso.cerrado is True  # `finally: cliente.close()` corre igual


def test_usa_las_variables_de_entorno_para_conectar(monkeypatch):
    monkeypatch.setenv("DEPLOY_SSH_HOST", "test.papyrus.com.co")
    monkeypatch.setenv("DEPLOY_SSH_KEY_PATH", "/etc/paquetex/deploy_key")
    monkeypatch.setenv("DEPLOY_SSH_USER", "ubuntu")
    falso = _ClienteSshFalso(codigo_salida=0)
    monkeypatch.setattr(paramiko, "SSHClient", lambda: falso)

    aplicar_credenciales_proveedor({"LIWA_API_KEY": "fake"})

    assert falso.conectado_con["hostname"] == "test.papyrus.com.co"
    assert falso.conectado_con["key_filename"] == "/etc/paquetex/deploy_key"
    assert falso.conectado_con["username"] == "ubuntu"


def test_sin_variables_de_entorno_lanza_error_aplicando_credenciales_no_keyerror(monkeypatch):
    monkeypatch.delenv("DEPLOY_SSH_HOST", raising=False)
    monkeypatch.delenv("DEPLOY_SSH_KEY_PATH", raising=False)

    def _no_debe_llamarse(*args, **kwargs):
        raise AssertionError("No debía intentarse ninguna conexión SSH")

    monkeypatch.setattr(paramiko, "SSHClient", _no_debe_llamarse)

    with pytest.raises(ErrorAplicandoCredenciales, match="incompleta"):
        aplicar_credenciales_proveedor({"AWS_ACCESS_KEY_ID": "AKIAFAKE"})


def test_cambios_vacio_no_conecta(monkeypatch):
    _monkeypatch_env_ssh(monkeypatch)

    def _no_debe_llamarse(*args, **kwargs):
        raise AssertionError("Nada que cambiar -- no debía intentarse ninguna conexión SSH")

    monkeypatch.setattr(paramiko, "SSHClient", _no_debe_llamarse)

    aplicar_credenciales_proveedor({})
