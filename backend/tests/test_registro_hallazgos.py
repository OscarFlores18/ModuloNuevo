"""«Descargar hallazgos»: el registro en Excel, cada aviso como en la ventana y sin estilos."""

from __future__ import annotations

import base64
import io

from openpyxl import load_workbook

from backend.app.services import ejecuciones as srv


def _ejecucion():
    ej = srv.Ejecucion(
        id="hall123",
        usuario="test",
        parametros={"desde": "01/08/2026", "hasta": "31/08/2026"},
        clientes=[srv.ClienteEnCurso("Acme", [])],
        estado="finalizada",
    )
    with srv._lock:
        srv._ejecuciones[ej.id] = ej
    return ej


BLOQUE = {
    "cliente": "Acme",
    "operacion": "Compras",
    "insignia": "A revisar",
    "titulo": "En ARCA las columnas no suman el total (1 comprobante)",
    "que_hacer": "Consultale al cliente la diferencia",
    "detalle": "El control se hizo con el Imp. Total.",
    "columnas": ["Fecha", "Comprobante", "Razón social", "Neto gravado 21%", "IVA 21%", "Imp. Total", "Diferencia", "En Xubio"],
    "filas": [["12/08/2026", "A-00018-00076", "ESTACION", 76832.14, 16134.75, 112000.06, 19033.17, 112000.06]],
}


def test_como_en_la_ventana_y_sin_estilos(entorno):
    client, _ = entorno
    ej = _ejecucion()
    r = client.post(f"/api/ejecuciones/{ej.id}/hallazgos-excel", json={"cliente": "Acme", "bloques": [BLOQUE]})
    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["nombre"] == "Hallazgos - Acme - 08-26.xlsx"
    ws = load_workbook(io.BytesIO(base64.b64decode(datos["contenido"])))["Hallazgos"]
    valores = [[c.value for c in fila] for fila in ws.iter_rows()]
    planos = [v for fila in valores for v in fila if v is not None]
    assert "Hallazgos del control · Período 01/08/2026 al 31/08/2026" in planos
    assert "Hallazgo 1" in planos
    fila_numero = next(i for i, f in enumerate(valores) if f[0] == "Hallazgo 1")
    assert all(v is None for v in valores[fila_numero - 1]) and all(v is None for v in valores[fila_numero - 2])
    assert BLOQUE["titulo"] in planos and "Qué hacer" in planos and BLOQUE["detalle"] in planos
    cabecera = next(i for i, f in enumerate(valores) if f[0] == "Fecha")
    assert valores[cabecera][: len(BLOQUE["columnas"])] == BLOQUE["columnas"]
    assert valores[cabecera + 1][3] == 76832.14
    # Fondos claros como en la ventana.
    color = lambda c: c.fill.fgColor.rgb[-6:]
    assert color(ws.cell(fila_numero + 1, 1)) == "DBEAFE"
    assert color(ws.cell(fila_numero + 2, 1)) == "FEF3C7"  # «A revisar»
    assert color(ws.cell(cabecera + 1, 1)) == "F1F5F9"
    assert color(ws.cell(cabecera + 2, 7)) == "FEF3C7"  # diferencia
    assert color(ws.cell(cabecera + 2, 8)) == "DCFCE7"  # en Xubio con el mismo total


def test_sin_bloques_es_422(entorno):
    client, _ = entorno
    ej = _ejecucion()
    assert client.post(f"/api/ejecuciones/{ej.id}/hallazgos-excel", json={"bloques": []}).status_code == 422
