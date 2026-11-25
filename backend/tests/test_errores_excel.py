"""«Descargar errores Excel» en la ventana de hallazgos: las partidas del Libro IVA
no encontradas en ARCA (bloque «A revisar») de un cliente y una operación."""

from __future__ import annotations

import base64
import io
from datetime import datetime

from openpyxl import Workbook, load_workbook

from backend.app.api.routes import web
from backend.app.services import ejecuciones as srv

NOMBRE = "AUX - Control de facturas {op} con AFIP - 03-25 - {cliente} - 1er control.xlsx"


def _aux(op: str, filas: list[list]) -> bytes:
    """Un AUX con una partida cruzada y, al pie, el bloque de las no encontradas en ARCA."""
    parte = "Emisor" if op == "Compras" else "Receptor"
    wb = Workbook()
    ws = wb.active
    ws.append(["Clave_Conciliacion", "Fecha", "Tipo", f"Nro. Doc. {parte}", f"Denominación {parte}", "Neto", "IVA", "Imp. Total"])
    ws.append(["A-00001-00000001", "01/03/2025", "1 - Factura A", 30500000000, "CRUZADO", 100, 21, 121])
    if filas:
        ws.append([])
        ws.append([f"Partidas del Libro IVA {op} no encontradas en ARCA"])
        ws.append(["=A1", "=B1", "=C1", "=D1", "=E1", "=F1", "=G1", "=H1"])
        for f in filas:
            ws.append(f)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


COMPRAS_ACME = [
    ["A-00011-00000025", "05/03/2025", "Tique", "30-71580481-2", "CORREDORES VIALES", 1000, 210, "=SUM(F6:G6)"],
    ["B-00002-00000107", "12/03/2025", "6 - Factura B", "20-04567890-1", "PROVEEDOR CERO", 500.5, 0, 500.5],
]
VENTAS_ACME = [["A-00003-00000009", "20/03/2025", "1 - Factura A", "30707310657", "CLIENTE SA", 200, 42, 242]]
COMPRAS_BETA = [["C-00099-00000001", "02/03/2025", "11 - Factura C", "27111111119", "SOLO DE BETA", 50, 0, 50]]


def _ejecucion(tmp_path, ventas=VENTAS_ACME):
    clientes = []
    for cliente, archivos in (("Acme", {"Compras": COMPRAS_ACME, "Ventas": ventas}), ("Beta", {"Compras": COMPRAS_BETA})):
        carpeta = tmp_path / "salidas" / cliente / "032025"
        carpeta.mkdir(parents=True)
        for op, filas in archivos.items():
            (carpeta / NOMBRE.format(op=op, cliente=cliente)).write_bytes(_aux(op, filas))
        clientes.append(srv.ClienteEnCurso(cliente, [], carpeta=str(carpeta), archivos=srv.listar_archivos(carpeta)))
    ej = srv.Ejecucion(id="err123", usuario="test", parametros={"desde": "01/03/2025", "hasta": "31/03/2025"},
                       clientes=clientes, estado="finalizada")
    with srv._lock:
        srv._ejecuciones[ej.id] = ej
    return ej


def _pedir(client, ej, indice, operacion):
    return client.get(f"/api/ejecuciones/{ej.id}/clientes/{indice}/errores-excel", params={"operacion": operacion})


def _libro(respuesta):
    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    return datos["nombre"], load_workbook(io.BytesIO(base64.b64decode(datos["contenido"])))["Compras" if "Compras" in datos["nombre"] else "Ventas"]


def _filas(ws):
    return [[c.value for c in fila] for fila in ws.iter_rows(min_row=2)]


def test_compras_columnas_y_valores(entorno, tmp_path):
    client, _ = entorno
    ej = _ejecucion(tmp_path)
    nombre, ws = _libro(_pedir(client, ej, 0, "Compras"))
    assert nombre == "Errores - Compras - Acme.xlsx"
    assert [c.value for c in ws[1]] == ["Fecha", "Tipo", "Comprobante", "CUIT", "Razón social", "Total"]
    assert _filas(ws) == [
        [datetime(2025, 3, 5), "Tique", "A-00011-00000025", "30715804812", "CORREDORES VIALES", 1210],
        [datetime(2025, 3, 12), "6 - Factura B", "B-00002-00000107", "20045678901", "PROVEEDOR CERO", 500.5],
    ]
    # Formatos: fecha, texto (comprobante y CUIT) y número.
    assert ws["A2"].number_format == "dd/mm/yyyy"
    assert ws["C2"].number_format == "@" and ws["D2"].number_format == "@"
    assert ws["F2"].number_format == "#,##0.00" and isinstance(ws["F2"].value, (int, float))
    assert ws.column_dimensions["E"].width >= 30 and ws.freeze_panes == "A2"


def test_ventas(entorno, tmp_path):
    client, _ = entorno
    ej = _ejecucion(tmp_path)
    nombre, ws = _libro(_pedir(client, ej, 0, "Ventas"))
    assert nombre == "Errores - Ventas - Acme.xlsx"
    assert _filas(ws) == [[datetime(2025, 3, 20), "1 - Factura A", "A-00003-00000009", "30707310657", "CLIENTE SA", 242]]


def test_solo_las_del_cliente_pedido(entorno, tmp_path):
    client, _ = entorno
    ej = _ejecucion(tmp_path)
    nombre, ws = _libro(_pedir(client, ej, 1, "Compras"))
    assert nombre == "Errores - Compras - Beta.xlsx"
    assert [f[2] for f in _filas(ws)] == ["C-00099-00000001"]
    _, ws_acme = _libro(_pedir(client, ej, 0, "Compras"))
    assert "C-00099-00000001" not in [f[2] for f in _filas(ws_acme)]


def test_conserva_los_ceros_de_adelante(entorno, tmp_path):
    client, _ = entorno
    ej = _ejecucion(tmp_path)
    _, ws = _libro(_pedir(client, ej, 0, "Compras"))
    # «20-04567890-1»: la CUIT sigue con su 0 y como texto; el comprobante también.
    assert ws["D3"].value == "20045678901" and ws["D3"].data_type == "s"
    assert ws["C3"].value == "B-00002-00000107" and ws["C3"].data_type == "s"


def test_no_toca_el_aux(entorno, tmp_path):
    client, _ = entorno
    ej = _ejecucion(tmp_path)
    ruta = tmp_path / "salidas" / "Acme" / "032025" / NOMBRE.format(op="Compras", cliente="Acme")
    antes = ruta.read_bytes()
    _libro(_pedir(client, ej, 0, "Compras"))
    assert ruta.read_bytes() == antes
    assert sorted(p.name for p in ruta.parent.iterdir()) == sorted(NOMBRE.format(op=o, cliente="Acme") for o in ("Compras", "Ventas"))


def test_sin_partidas_es_404_y_no_arma_archivo(entorno, tmp_path):
    client, _ = entorno
    ej = _ejecucion(tmp_path, ventas=[])
    r = _pedir(client, ej, 0, "Ventas")
    assert r.status_code == 404 and "No hay partidas" in r.json()["detail"]["mensaje"]
    # Beta no tiene AUX de Ventas: lo mismo.
    assert _pedir(client, ej, 1, "Ventas").status_code == 404


def test_pedidos_invalidos(entorno, tmp_path):
    client, _ = entorno
    ej = _ejecucion(tmp_path)
    assert _pedir(client, ej, 0, "Otra").status_code == 422
    assert client.get(f"/api/ejecuciones/{ej.id}/clientes/0/errores-excel").status_code == 422
    assert _pedir(client, ej, 9, "Compras").status_code == 404
    assert client.get("/api/ejecuciones/noexiste/clientes/0/errores-excel", params={"operacion": "Compras"}).status_code == 404


def test_error_al_armar_el_excel_se_avisa(entorno, tmp_path, monkeypatch):
    client, _ = entorno
    ej = _ejecucion(tmp_path)

    def falla(*_):
        raise ValueError("disco lleno")

    monkeypatch.setattr(web, "excel_errores", falla)
    r = _pedir(client, ej, 0, "Compras")
    assert r.status_code == 500 and "disco lleno" in r.json()["detail"]["mensaje"]


def test_aux_ilegible_se_avisa(entorno, tmp_path):
    client, _ = entorno
    ej = _ejecucion(tmp_path)
    (tmp_path / "salidas" / "Acme" / "032025" / NOMBRE.format(op="Compras", cliente="Acme")).write_bytes(b"no es un xlsx")
    r = _pedir(client, ej, 0, "Compras")
    assert r.status_code == 500 and "No se pudo leer el AUX de Compras" in r.json()["detail"]["mensaje"]
