"""Pruebas del cifrado en reposo y del servicio interno de descifrado.

Cubren los seis puntos pedidos:

1. La API no puede devolver contraseñas.
2. Angular (los endpoints que consume) no recibe credenciales.
3. Los JSON contienen únicamente valores cifrados.
4. Los bots reciben las contraseñas originales en memoria.
5. Un usuario que consulta la configuración no obtiene las credenciales.
6. Los errores y logs no exponen contraseñas.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.core import config
from backend.app.core.crypto import PREFIJO, cifrar, descifrar, esta_cifrado
from backend.app.core.errors import CredencialInvalida
from backend.app.core.models import BotResult, JobRequest
from backend.app.core.storage import ClientesRepository, CredencialesXubioRepository
from backend.app.services import control_facturas
from backend.app.services.credenciales import (
    CredencialesArca,
    CredencialesXubio,
    ServicioCredenciales,
)

CLAVE = "ClaveFiscal-SuperSecreta-123"
PASS = "PassXubio-SuperSecreta-456"


@pytest.fixture(autouse=True)
def clave_local(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.delenv("MODULONUEVO_MASTER_KEY", raising=False)


@pytest.fixture
def repos(tmp_path: Path):
    return (
        ClientesRepository(
            tmp_path / "config.json", tmp_path / "xubio_credenciales.json"
        ),
        CredencialesXubioRepository(tmp_path / "xubio_credenciales.json"),
    )


def _request(pasos: list[str], **cambios) -> JobRequest:
    datos = {
        "clientes": ["Acme"],
        "fecha_desde": "01/03/2025",
        "fecha_hasta": "31/03/2025",
        "ventas": True,
        "compras": False,
        "pasos": pasos,
    }
    datos.update(cambios)
    return JobRequest(**datos)


def _crear_datos(clave: str = CLAVE) -> dict:
    return {
        "arca": {
            "cuit_login": "20111111112",
            "clave": clave,
            "cuit_representado": "20222222223",
        },
        "xubio": {"url": "/NXV/acme"},
    }


def _claves(obj) -> set[str]:
    """Todas las claves de un JSON anidado, para detectar campos prohibidos."""
    encontradas: set[str] = set()
    if isinstance(obj, dict):
        for clave, valor in obj.items():
            encontradas.add(str(clave))
            encontradas |= _claves(valor)
    elif isinstance(obj, list):
        for valor in obj:
            encontradas |= _claves(valor)
    return encontradas


# ---------------------------------------------------------------------------
# 1 y 2. La API y Angular nunca reciben credenciales
# ---------------------------------------------------------------------------


def test_api_no_puede_devolver_contrasenas(entorno):
    client, _ = entorno
    respuestas = []

    alta = client.post(
        "/api/clientes",
        json={"nombre": "Acme", **_crear_datos()},
    )
    assert alta.status_code == 201, alta.text
    respuestas.append(alta)

    respuestas.append(client.get("/api/clientes"))
    respuestas.append(client.get("/api/clientes/Acme"))

    editado = client.put(
        "/api/clientes/Acme",
        json={"nombre": "Acme", "arca": {"cuit_login": "20111111112", "clave": ""}},
    )
    assert editado.status_code == 200, editado.text
    respuestas.append(editado)

    respuestas.append(client.post("/api/clientes/Acme/renombrar", json={"nuevo": "Acme"}))

    guardadas = client.put(
        "/api/xubio/credenciales",
        json={"user": "operador", "pass": PASS, "recordar": True},
    )
    assert guardadas.status_code == 200, guardadas.text
    respuestas.append(guardadas)
    respuestas.append(client.get("/api/xubio/credenciales"))
    respuestas.append(client.get("/api/ajustes"))
    respuestas.append(client.get("/api/yo"))

    for respuesta in respuestas:
        assert respuesta.status_code < 500
        assert CLAVE not in respuesta.text
        assert PASS not in respuesta.text
        claves = _claves(respuesta.json())
        assert "clave" not in claves
        assert "pass" not in claves


def test_angular_solo_recibe_indicadores(entorno, tmp_path):
    client, _ = entorno
    client.post("/api/clientes", json={"nombre": "Acme", **_crear_datos()})
    client.put(
        "/api/xubio/credenciales",
        json={"user": "operador", "pass": PASS, "recordar": True},
    )

    clientes = client.get("/api/clientes").json()
    assert clientes[0]["arca"] == {
        "cuit_login": "20111111112",
        "cuit_representado": "20222222223",
        "tiene_clave": True,
    }
    assert clientes[0]["faltantes"] == []

    ajustes = client.get("/api/ajustes").json()
    assert ajustes["xubio"]["tiene_clave"] is True
    assert "pass" not in ajustes["xubio"]
    assert "clave" not in ajustes["xubio"]

    xubio = client.get("/api/xubio/credenciales").json()
    assert xubio == {"user": "operador", "recordar": True, "tiene_pass": True}

    # El archivo que sirve Angular está compilado y no consulta /data.
    assert CLAVE not in json.dumps(ajustes)


# ---------------------------------------------------------------------------
# 3. En disco solo hay valores cifrados
# ---------------------------------------------------------------------------


def test_json_contiene_solo_valores_cifrados(repos, tmp_path: Path):
    clientes, xubio = repos
    clientes.guardar("Acme", _crear_datos())
    xubio.guardar("operador", PASS, recordar=True)

    config_path = tmp_path / "config.json"
    xubio_path = tmp_path / "xubio_credenciales.json"
    texto_config = config_path.read_text(encoding="utf-8")
    texto_xubio = xubio_path.read_text(encoding="utf-8")

    assert CLAVE not in texto_config
    assert PASS not in texto_xubio

    guardado = json.loads(texto_config)
    clave = guardado["clientes"]["Acme"]["arca"]["clave"]
    assert esta_cifrado(clave)
    assert clave.startswith(PREFIJO)
    assert descifrar(clave) == CLAVE

    pass_guardada = json.loads(texto_xubio)["pass"]
    assert esta_cifrado(pass_guardada)
    assert descifrar(pass_guardada) == PASS

    # Cifrar dos veces no cambia lo ya cifrado (idempotente).
    assert cifrar(clave) == clave
    assert cifrar(PASS) != PASS


def test_migra_datos_en_texto_plano(repos, tmp_path: Path):
    clientes, xubio = repos
    (tmp_path / "config.json").write_text(
        json.dumps({"clientes": {"Acme": _crear_datos(CLAVE)}}), encoding="utf-8"
    )
    (tmp_path / "xubio_credenciales.json").write_text(
        json.dumps({"user": "operador", "pass": PASS, "recordar": True}),
        encoding="utf-8",
    )

    servicio = ServicioCredenciales(clientes, xubio)
    assert servicio.migrar() == 2
    assert servicio.migrar() == 0

    assert CLAVE not in (tmp_path / "config.json").read_text(encoding="utf-8")
    assert PASS not in (tmp_path / "xubio_credenciales.json").read_text(
        encoding="utf-8"
    )
    assert servicio.arca("Acme").clave == CLAVE
    assert servicio.xubio().clave == PASS


# ---------------------------------------------------------------------------
# 4. Los bots reciben la contraseña original en memoria
# ---------------------------------------------------------------------------


class _ServicioFalso:
    def is_closed(self) -> bool:
        return False

    def close(self) -> None:
        pass


class _SesionArcaFalsa:
    def abrir_mis_comprobantes(self, cuit_representado: str = "") -> _ServicioFalso:
        return _ServicioFalso()

    def descargar(
        self,
        servicio,
        tipo_comprobante,
        fecha_desde,
        fecha_hasta,
        nombre_cliente,
        directorio_guardado,
        **kwargs,
    ) -> BotResult:
        ruta = Path(directorio_guardado) / f"Mis comprobantes - {tipo_comprobante}.xlsx"
        ruta.write_bytes(b"x")
        return BotResult(archivos=[str(ruta)])

    def renovar(self) -> None:
        pass

    def cerrar(self) -> None:
        pass


class _GestorArcaFalso:
    def __init__(self, log=print) -> None:
        self.llamadas: list[tuple[str, str]] = []

    def arca(self, cuit_login: str, clave: str) -> _SesionArcaFalsa:
        self.llamadas.append((cuit_login, clave))
        return _SesionArcaFalsa()

    def cerrar(self) -> None:
        pass


def test_el_bot_de_arca_recibe_la_clave_original(repos, tmp_path: Path):
    clientes, xubio = repos
    clientes.guardar("Acme", _crear_datos())
    servicio = ServicioCredenciales(clientes, xubio)
    gestor = _GestorArcaFalso()

    resultado = control_facturas.ejecutar_control_facturas(
        request=_request(["arca"]),
        clientes=clientes.listar_publico(),
        xubio_user="",
        xubio_pass="",
        base_salida=tmp_path,
        log=lambda mensaje: None,
        sesiones=gestor,
        credenciales=servicio,
    )

    assert resultado.errores == []
    assert gestor.llamadas == [("20111111112", CLAVE)]
    # La credencial descifrada vive solo en memoria y no se imprime.
    credencial = servicio.arca("Acme")
    assert credencial.clave == CLAVE
    assert CLAVE not in repr(credencial)
    assert CLAVE not in repr(CredencialesXubio("operador", CLAVE))


class _SesionXubioFalsa:
    def cambiar_empresa(self, nombre_cliente: str) -> None:
        pass

    def descargar(self, *args, **kwargs) -> BotResult:
        carpeta = Path(args[5])
        ruta = carpeta / "IVA VENTAS.xlsx"
        ruta.write_bytes(b"x")
        return BotResult(archivos=[str(ruta)])

    def cerrar(self) -> None:
        pass


class _GestorXubioFalso:
    def __init__(self, log=print) -> None:
        self.llamadas: list[tuple[str, str]] = []

    def xubio(self, usuario: str, password: str) -> _SesionXubioFalsa:
        self.llamadas.append((usuario, password))
        return _SesionXubioFalsa()

    def descartar_xubio(self) -> None:
        pass

    def cerrar(self) -> None:
        pass


def test_el_bot_de_xubio_recibe_la_password_original(repos, tmp_path: Path):
    clientes, xubio = repos
    clientes.guardar("Acme", _crear_datos())
    xubio.guardar("operador", PASS, recordar=True)
    servicio = ServicioCredenciales(clientes, xubio)
    credencial = servicio.xubio()
    assert credencial.usuario == "operador"
    assert credencial.clave == PASS

    gestor = _GestorXubioFalso()
    resultado = control_facturas.ejecutar_control_facturas(
        request=_request(["xubio"]),
        clientes=clientes.listar_publico(),
        xubio_user=credencial.usuario,
        xubio_pass=credencial.clave,
        base_salida=tmp_path,
        log=lambda mensaje: None,
        sesiones=gestor,
        credenciales=servicio,
    )

    assert resultado.errores == []
    assert gestor.llamadas == [("operador", PASS)]


# ---------------------------------------------------------------------------
# 5. Consultar configuración no entrega credenciales
# ---------------------------------------------------------------------------


def test_usuario_de_configuracion_no_obtiene_credenciales(entorno, tmp_path):
    client, _ = entorno
    client.post("/api/clientes", json={"nombre": "Acme", **_crear_datos()})
    client.put(
        "/api/xubio/credenciales",
        json={"user": "operador", "pass": PASS, "recordar": True},
    )

    publico = client.get("/api/clientes/Acme").json()
    assert publico["arca"]["tiene_clave"] is True
    assert "clave" not in publico["arca"]

    # El repositorio público tampoco puede entregarla ni siquiera por código.
    repo = ClientesRepository(
        tmp_path / "data" / "config.json", tmp_path / "data" / "xubio_credenciales.json"
    )
    assert "clave" not in repo.obtener_publico("Acme")["arca"]
    assert "clave" not in repo.listar_publico()["Acme"]["arca"]

    xubio_repo = CredencialesXubioRepository(tmp_path / "data" / "xubio_credenciales.json")
    assert "pass" not in xubio_repo.obtener()


# ---------------------------------------------------------------------------
# 6. Errores y logs sin secretos
# ---------------------------------------------------------------------------


def _corromper(ruta: Path, ruta_json: list[str]) -> str:
    """Rompe el token cifrado indicado y devuelve el token original."""
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    actual = datos
    for clave in ruta_json[:-1]:
        actual = actual[clave]
    token = actual[ruta_json[-1]]
    actual[ruta_json[-1]] = PREFIJO + "token-invalido"
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    return token


def test_errores_no_exponen_contrasenas(repos, tmp_path: Path):
    clientes, xubio = repos
    clientes.guardar("Acme", _crear_datos())
    token = _corromper(
        tmp_path / "config.json", ["clientes", "Acme", "arca", "clave"]
    )
    servicio = ServicioCredenciales(clientes, xubio)

    with pytest.raises(CredencialInvalida) as info:
        servicio.arca("Acme")
    mensaje = str(info.value)
    assert CLAVE not in mensaje
    assert token not in mensaje
    assert "token-invalido" not in mensaje
    assert CLAVE not in json.dumps(info.value.to_dict())


def test_logs_de_la_ejecucion_no_exponen_contrasenas(repos, tmp_path: Path):
    clientes, xubio = repos
    clientes.guardar("Acme", _crear_datos())
    _corromper(tmp_path / "config.json", ["clientes", "Acme", "arca", "clave"])
    servicio = ServicioCredenciales(clientes, xubio)
    logs: list[str] = []

    resultado = control_facturas.ejecutar_control_facturas(
        request=_request(["arca"]),
        clientes=clientes.listar_publico(),
        xubio_user="",
        xubio_pass="",
        base_salida=tmp_path,
        log=logs.append,
        credenciales=servicio,
    )

    texto_logs = "\n".join(logs)
    assert CLAVE not in texto_logs
    assert "token-invalido" not in texto_logs
    assert resultado.errores
    assert resultado.errores[0].code == "credencial_invalida"
    assert CLAVE not in resultado.errores[0].mensaje


def test_password_de_xubio_corrupta_no_se_filtra(repos, tmp_path: Path):
    clientes, xubio = repos
    xubio.guardar("operador", PASS, recordar=True)
    token = _corromper(tmp_path / "xubio_credenciales.json", ["pass"])
    servicio = ServicioCredenciales(clientes, xubio)

    with pytest.raises(CredencialInvalida) as info:
        servicio.xubio()
    assert PASS not in str(info.value)
    assert token not in str(info.value)
    assert "token-invalido" not in str(info.value)


# ---------------------------------------------------------------------------
# Separación de capas
# ---------------------------------------------------------------------------


def test_la_api_no_importa_el_servicio_de_descifrado():
    api = Path(__file__).resolve().parents[1] / "app" / "api"
    for archivo in api.rglob("*.py"):
        texto = archivo.read_text(encoding="utf-8")
        assert "services.credenciales" not in texto, archivo
        assert "core.crypto" not in texto, archivo
        assert "descifrar(" not in texto, archivo


def test_repr_de_credenciales_oculta_los_secretos():
    arca = CredencialesArca(cuit_login="20", clave=CLAVE, cuit_representado="21")
    xubio = CredencialesXubio(usuario="operador", clave=PASS)
    assert CLAVE not in repr(arca)
    assert PASS not in repr(xubio)
    assert arca.clave == CLAVE
    assert xubio.clave == PASS
