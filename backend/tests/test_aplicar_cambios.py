"""«Aplicar cambios»: el dato de ARCA elegido se escribe en una copia del Libro IVA
y el control se vuelve a cruzar con esa copia."""

from __future__ import annotations

from pathlib import Path

import openpyxl

from backend.app.auditor.corregir_libro import corregir_libro
from backend.app.services import ejecuciones as srv

from .test_errores_reales import _arca, _xubio

CUIT = "27479591992"


def _carpeta(tmp_path: Path) -> Path:
    """Un control terminado con los tres errores que se corrigen: letra, tipo e importe."""
    carpeta = tmp_path / "salidas" / "PICCININI" / "042026" / "Control de facturas" / "1er control"
    carpeta.mkdir(parents=True)
    _arca(
        carpeta / "Mis comprobantes - Recibidos - PICCININI - 04-26 al 04-26.xlsx",
        [
            {"cuit": CUIT, "numero": 6, "tipo": "11 - Factura C", "total": 1000},
            {"cuit": CUIT, "numero": 7, "tipo": "11 - Factura C", "total": 2000},
            {"cuit": CUIT, "numero": 8, "tipo": "11 - Factura C", "total": 3000},
        ],
    )
    _xubio(
        carpeta / "IVA COMPRAS - PICCININI - 01-04-2026 al 30-04-2026.xlsx",
        [
            {"cuit": "27-47959199-2", "comprobante": "A-0002-00000006", "total": 1000},
            {"cuit": "27-47959199-2", "comprobante": "C-0002-00000007", "tipo": "Nota Débito", "total": 2000},
            {"cuit": "27-47959199-2", "comprobante": "C-0002-00000008", "total": 3100},
        ],
    )
    return carpeta


def _ejecucion(carpeta: Path) -> srv.Ejecucion:
    errores: list[dict] = []
    resumenes: list[dict] = []
    srv.procesar_cruce(
        "PICCININI", "Compras", "01/04/2026", "30/04/2026", str(carpeta),
        log=lambda _: None, avisar_errores=errores.extend, avisar_resumen=resumenes.append,
    )
    ej = srv.Ejecucion(
        id="apl123",
        usuario="test",
        parametros={"desde": "01/04/2026", "hasta": "30/04/2026"},
        clientes=[srv.ClienteEnCurso("PICCININI", [], carpeta=str(carpeta), archivos=srv.listar_archivos(carpeta))],
        estado="finalizada",
        advertencias=[{**e, "cliente": "PICCININI"} for e in errores],
        resumenes=[{**r, "cliente": "PICCININI"} for r in resumenes],
    )
    with srv._lock:
        srv._ejecuciones[ej.id] = ej
    return ej


CAMBIOS = [
    {"cliente": "PICCININI", "operacion": "Compras", "campo": "letra", "cuit": CUIT, "pv": 2, "numero": 6, "comprobante": "A-00002-00000006", "valorArca": "C"},
    {"cliente": "PICCININI", "operacion": "Compras", "campo": "tipo", "cuit": CUIT, "pv": 2, "numero": 7, "comprobante": "C-00002-00000007", "valorArca": "11 - Factura C"},
    {"cliente": "PICCININI", "operacion": "Compras", "campo": "importe", "cuit": CUIT, "pv": 2, "numero": 8, "comprobante": "C-00002-00000008", "valorArca": 3000},
]


def test_corrige_las_celdas_en_una_copia(tmp_path: Path):
    carpeta = _carpeta(tmp_path)
    original = carpeta / "IVA COMPRAS - PICCININI - 01-04-2026 al 30-04-2026.xlsx"
    copia = tmp_path / "corregido.xlsx"
    cambios = [{**c, "valor_arca": c["valorArca"]} for c in CAMBIOS]
    cambios.append({"campo": "importe", "cuit": CUIT, "pv": 2, "numero": 99, "valor_arca": 1})

    r = corregir_libro(original, copia, cambios)

    assert len(r["aplicados"]) == 3
    assert [n["numero"] for n in r["no_aplicados"]] == [99]
    filas = list(openpyxl.load_workbook(copia).active.iter_rows(min_row=2, values_only=True))
    assert [(f[1], f[2], f[21]) for f in filas] == [
        ("Factura", "C-0002-00000006", 1000),
        ("Factura", "C-0002-00000007", 2000),
        ("Factura", "C-0002-00000008", 3000),
    ]
    # El de Xubio queda como estaba.
    assert openpyxl.load_workbook(original).active.cell(2, 3).value == "A-0002-00000006"


def test_aplicar_corrige_y_vuelve_a_cruzar(entorno, tmp_path: Path):
    client, _ = entorno
    carpeta = _carpeta(tmp_path)
    ej = _ejecucion(carpeta)
    antes = {a["tipo"] for a in ej.advertencias}
    assert {"letra_distinta", "tipo_distinto", "importe_distinto"} <= antes

    r = client.post("/api/ejecuciones/apl123/aplicar-cambios", json={"cambios": CAMBIOS})

    assert r.status_code == 200, r.text
    datos = r.json()
    [res] = datos["resultados"]
    assert res["aplicados"] == 3 and res["no_aplicados"] == []
    assert res["libro"] == "IVA COMPRAS - PICCININI - 01-04-2026 al 30-04-2026 - corregido.xlsx"
    assert res["aux"].startswith("AUX - Control de facturas Compras") and res["aux"].endswith(" - corregido.xlsx")
    # Re-chequeado: los tres pares ya no son errores.
    tipos = {a["tipo"] for a in datos["ejecucion"]["advertencias"]}
    assert not tipos & {"letra_distinta", "tipo_distinto", "importe_distinto", "faltante_en_xubio", "faltante_en_arca"}
    [resumen] = datos["ejecucion"]["resumenes"]
    assert resumen["coinciden"] == 3
    cli = datos["ejecucion"]["detalle_clientes"][0]
    nombres = {a["nombre"] for a in cli["archivos"]}
    assert {res["libro"], res["aux"]} <= nombres
    assert len(cli["corregidos"]["Compras"]["cambios"]) == 3
    # El AUX nuevo se baja y no pisa el original.
    assert client.get(f"/api/ejecuciones/apl123/clientes/0/archivos/{res['aux']}").status_code == 200
    assert len([n for n in nombres if n.startswith("AUX - Control de facturas Compras")]) == 2


def test_no_aplica_con_el_control_corriendo(entorno, tmp_path: Path):
    client, _ = entorno
    ej = _ejecucion(_carpeta(tmp_path))
    ej.estado = "en_curso"
    r = client.post("/api/ejecuciones/apl123/aplicar-cambios", json={"cambios": CAMBIOS})
    assert r.status_code == 409
