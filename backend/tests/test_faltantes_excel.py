"""El Excel de partidas de ARCA no encontradas que se adjunta al mail «Pedir que las carguen»."""

from __future__ import annotations

import base64
import io

from openpyxl import load_workbook

from backend.app.services import ejecuciones as srv

from .test_csv_arca import _aux


def _ejecucion(tmp_path):
    carpeta = tmp_path / "salidas" / "Acme" / "032025"
    carpeta.mkdir(parents=True)
    nombre = "AUX - Control de facturas {} con AFIP - 03-25 - Acme - 1er control.xlsx"
    (carpeta / nombre.format("Compras")).write_bytes(_aux("Compras", 2))
    (carpeta / nombre.format("Ventas")).write_bytes(_aux("Ventas", 1))
    ej = srv.Ejecucion(
        id="falt123",
        usuario="test",
        parametros={"desde": "01/03/2025", "hasta": "31/03/2025"},
        clientes=[srv.ClienteEnCurso("Acme", [], carpeta=str(carpeta), archivos=srv.listar_archivos(carpeta))],
        estado="finalizada",
    )
    with srv._lock:
        srv._ejecuciones[ej.id] = ej
    return ej


def _libro(respuesta) -> tuple[str, object]:
    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    return datos["nombre"], load_workbook(io.BytesIO(base64.b64decode(datos["contenido"])))


def test_una_operacion_con_el_formato_de_mis_comprobantes(entorno, tmp_path):
    client, _ = entorno
    ej = _ejecucion(tmp_path)
    nombre, wb = _libro(client.get(f"/api/ejecuciones/{ej.id}/clientes/0/faltantes-excel", params={"operacion": "Compras"}))
    assert nombre == "Partidas de ARCA no encontradas en Libro IVA - Acme - 03-25.xlsx"
    assert wb.sheetnames == ["Compras"]
    ws = wb["Compras"]
    assert ws.max_row == 3
    assert ws["A1"].value == "Fecha de Emisión" and ws["B1"].value == "Tipo de Comprobante"


def test_sin_operacion_van_las_dos(entorno, tmp_path):
    client, _ = entorno
    ej = _ejecucion(tmp_path)
    _, wb = _libro(client.get(f"/api/ejecuciones/{ej.id}/clientes/0/faltantes-excel"))
    assert wb.sheetnames == ["Ventas", "Compras"]


def test_sin_partidas_es_404(entorno, tmp_path):
    client, _ = entorno
    ej = _ejecucion(tmp_path)
    (tmp_path / "salidas" / "Acme" / "032025" / "AUX - Control de facturas Ventas con AFIP - 03-25 - Acme - 1er control.xlsx").write_bytes(_aux("Ventas", 0))
    r = client.get(f"/api/ejecuciones/{ej.id}/clientes/0/faltantes-excel", params={"operacion": "Ventas"})
    assert r.status_code == 404
