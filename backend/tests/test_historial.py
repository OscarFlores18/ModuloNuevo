"""El historial de controles sobrevive a un reinicio del backend."""

from __future__ import annotations

import json

import pytest

from backend.app.core import config
from backend.app.services import ejecuciones as srv


@pytest.fixture(autouse=True)
def _memoria_limpia(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    _olvidar()
    yield
    _olvidar()


def _olvidar() -> None:
    """Lo que pasa al reiniciar el servidor: la memoria queda vacía."""
    with srv._lock:
        srv._ejecuciones.clear()
        srv._orden.clear()
        srv._historial_de = None


def _terminada(id_: str, creada: str, estado: str = "finalizada") -> srv.Ejecucion:
    paso = srv.Paso("arca_ventas", "ARCA · Ventas", estado="error", mensaje="Clave fiscal incorrecta.")
    cliente = srv.ClienteEnCurso("Cliente Uno SA", [paso], estado="con_errores", carpeta="C:/x")
    return srv.Ejecucion(
        id=id_,
        usuario="Ana",
        parametros={
            "desde": "01/10/2026",
            "hasta": "31/10/2026",
            "ventas": True,
            "compras": False,
            "pasos": {"arca": True, "xubio": True, "auditoria": True},
            "carpeta": "C:/salidas",
            "xubio_usuario": "ana@estudio.com",
        },
        clientes=[cliente],
        estado=estado,
        creada=creada,
        terminada=creada,
        mensaje="Terminó: 1 cliente(s) con errores o avisos.",
    )


def test_un_control_terminado_se_lee_despues_de_reiniciar():
    srv._guardar(_terminada("viejo", "2026-11-01T10:00:00"))
    srv._guardar(_terminada("nuevo", "2026-11-02T10:00:00"))
    _olvidar()

    lista = srv.listar()
    assert [e["id"] for e in lista] == ["nuevo", "viejo"]  # el más reciente primero
    assert lista[0]["totales"]["con_errores"] == 1

    detalle = srv.obtener("viejo").detalle()
    paso = detalle["detalle_clientes"][0]["pasos"][0]
    assert paso["estado"] == "error"
    assert paso["mensaje"] == "Clave fiscal incorrecta."
    assert detalle["parametros"]["desde"] == "01/10/2026"
    assert srv.activa() is None


def test_un_control_cortado_a_medias_queda_como_error():
    srv._guardar(_terminada("cortado", "2026-11-01T10:00:00", estado="en_curso"))
    _olvidar()

    ej = srv.obtener("cortado")
    assert ej.estado == "error"
    assert srv.activa() is None  # no bloquea un control nuevo


def test_un_archivo_roto_no_impide_ver_el_resto():
    srv._guardar(_terminada("sano", "2026-11-01T10:00:00"))
    (srv._carpeta_historial() / "roto.json").write_text("{no es json", encoding="utf-8")
    _olvidar()

    assert [e["id"] for e in srv.listar()] == ["sano"]


def test_se_olvidan_los_mas_viejos(monkeypatch):
    monkeypatch.setattr(srv, "MAX_HISTORIAL", 2)
    for i in range(3):
        srv._guardar(_terminada(f"e{i}", f"2026-11-0{i + 1}T10:00:00"))
    _olvidar()

    assert [e["id"] for e in srv.listar()] == ["e2", "e1"]
    assert not (srv._carpeta_historial() / "e0.json").exists()


def test_lo_guardado_no_incluye_claves():
    srv._guardar(_terminada("x", "2026-11-01T10:00:00"))
    texto = (srv._carpeta_historial() / "x.json").read_text(encoding="utf-8")
    datos = json.loads(texto)
    assert "_respuesta" not in datos and "_evento" not in datos
    assert "clave" not in datos["parametros"]


def test_eliminar_un_control_del_historial():
    """Se borra del historial y de disco; no vuelve después de reiniciar."""
    srv._guardar(_terminada("uno", "2026-11-01T10:00:00"))
    srv._guardar(_terminada("dos", "2026-11-02T10:00:00"))
    srv.eliminar("uno")
    assert [e["id"] for e in srv.listar()] == ["dos"]
    assert not (config.DATA_DIR / "ejecuciones" / "uno.json").exists()
    _olvidar()
    assert [e["id"] for e in srv.listar()] == ["dos"]
    with pytest.raises(srv.ErrorEjecucion) as e:
        srv.eliminar("uno")
    assert e.value.estado == 404


def test_no_se_borra_un_control_que_esta_corriendo():
    ej = _terminada("corre", "2026-11-03T10:00:00", estado="en_curso")
    with srv._lock:
        srv._ejecuciones[ej.id] = ej
        srv._orden.append(ej.id)
        srv._historial_de = srv._carpeta_historial()
    with pytest.raises(srv.ErrorEjecucion) as e:
        srv.eliminar("corre")
    assert e.value.estado == 409
