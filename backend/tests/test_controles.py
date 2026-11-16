"""1er / 2do control: cada auditoría del mismo período genera un AUX nuevo."""

from __future__ import annotations

from pathlib import Path

from backend.app.auditor import procesar_cruce
from backend.app.core import config
from backend.app.core.controles import (
    base_aux,
    carpeta_periodo,
    ordinal,
    ruta_siguiente_control,
)

from .test_auditor import _crear_arca, _crear_xubio

ARCA = {"cuit_login": "20-23510216-2", "clave": "secreta", "cuit_representado": "30-71080988-3"}


def test_ordinales():
    assert [ordinal(n) for n in (1, 2, 3, 4, 7, 10, 11)] == [
        "1er", "2do", "3er", "4to", "7mo", "10mo", "11°",
    ]


def test_primer_control_en_carpeta_vacia(tmp_path: Path):
    ruta, numero, previos = ruta_siguiente_control(tmp_path, "Ventas", "01/10/2026", "Acme S.A.")
    assert numero == 1 and previos == []
    assert ruta.name == "AUX - Control de facturas Ventas con AFIP - 10-26 - Acme S.A - 1er control.xlsx"


def test_aux_viejo_sin_numero_cuenta_como_primero(tmp_path: Path):
    (tmp_path / f"{base_aux('Ventas', '01/10/2026', 'Acme')}.xlsx").write_bytes(b"x")
    ruta, numero, previos = ruta_siguiente_control(tmp_path, "Ventas", "01/10/2026", "Acme")
    assert numero == 2 and [p.numero for p in previos] == [1]
    assert ruta.name.endswith(" - 2do control.xlsx")


def test_no_mezcla_operaciones_ni_periodos(tmp_path: Path):
    for op, desde in (("Compras", "01/10/2026"), ("Ventas", "01/09/2026")):
        (tmp_path / f"{base_aux(op, desde, 'Acme')} - 1er control.xlsx").write_bytes(b"x")
    _, numero, _ = ruta_siguiente_control(tmp_path, "Ventas", "01/10/2026", "Acme")
    assert numero == 1


def test_auditar_dos_veces_conserva_los_dos(tmp_path: Path):
    arca, xubio = tmp_path / "arca.xlsx", tmp_path / "xubio.xlsx"
    _crear_arca(arca)
    _crear_xubio(xubio)
    log: list[str] = []
    kwargs = dict(ruta_arca_manual=str(arca), ruta_xubio_manual=str(xubio), log=log.append)

    primero = procesar_cruce("Acme", "Ventas", "01/03/2025", "31/03/2025", str(tmp_path), **kwargs)
    segundo = procesar_cruce("Acme", "Ventas", "01/03/2025", "31/03/2025", str(tmp_path), **kwargs)

    assert Path(primero).name.endswith(" - 1er control.xlsx")
    assert Path(segundo).name.endswith(" - 2do control.xlsx")
    assert Path(primero).is_file() and Path(segundo).is_file()
    assert any("1er control del período" in linea for linea in log)
    assert any("2do control (el 1er fue el" in linea for linea in log)


def test_endpoint_controles(entorno):
    client, _ = entorno
    client.post("/api/clientes", json={"nombre": "Acme", "arca": ARCA})
    carpeta = carpeta_periodo(config.OUTPUT_DIR, "Acme", "01/10/2026")
    carpeta.mkdir(parents=True)
    (carpeta / f"{base_aux('Ventas', '01/10/2026', 'Acme')} - 1er control.xlsx").write_bytes(b"x")

    r = client.post("/api/controles", json={"clientes": ["Acme", "No existe"], "desde": "01/10/2026"})
    assert r.status_code == 200
    datos = r.json()
    assert list(datos) == ["Acme"]
    assert datos["Acme"]["ventas"]["realizados"] == 1
    assert datos["Acme"]["ventas"]["proximo_texto"] == "2do control"
    assert datos["Acme"]["compras"]["proximo_texto"] == "1er control"
    assert datos["Acme"]["compras"]["ultimo"] is None


def test_endpoint_controles_fecha_invalida(entorno):
    client, _ = entorno
    r = client.post("/api/controles", json={"clientes": [], "desde": "2026-10-01"})
    assert r.status_code == 422


def test_endpoint_cuenta_los_aux_de_control_de_facturas(entorno):
    """Los AUX se guardan en <Cliente>/<mmaaaa>/Control de facturas: el contador
    tiene que encontrarlos ahí (antes miraba solo <mmaaaa> y siempre decía 1er)."""
    client, _ = entorno
    client.post("/api/clientes", json={"nombre": "Acme", "arca": {}})
    periodo = Path(config.OUTPUT_DIR) / "Acme" / "102026"
    nueva = periodo / "Control de facturas"
    nueva.mkdir(parents=True)
    base = base_aux("Ventas", "01/10/2026", "Acme")
    # Uno de antes de la subcarpeta (en el período) y uno nuevo (en la subcarpeta).
    (periodo / f"{base}.xlsx").write_bytes(b"x")
    (nueva / f"{base} - 2do control.xlsx").write_bytes(b"x")

    r = client.post("/api/controles", json={"clientes": ["Acme"], "desde": "01/10/2026"})

    ventas = r.json()["Acme"]["ventas"]
    assert ventas["realizados"] == 2
    assert ventas["proximo_texto"] == "3er control"


def test_el_proximo_aux_sigue_la_numeracion_del_anterior(tmp_path: Path):
    from backend.app.core.controles import ruta_siguiente_control

    periodo = tmp_path / "Acme" / "102026"
    nueva = periodo / "Control de facturas"
    nueva.mkdir(parents=True)
    (periodo / f"{base_aux('Compras', '01/10/2026', 'Acme')}.xlsx").write_bytes(b"x")

    ruta, numero, previos = ruta_siguiente_control(nueva, "Compras", "01/10/2026", "Acme")

    assert numero == 2 and len(previos) == 1
    assert ruta.parent == nueva and ruta.name.endswith("2do control.xlsx")
