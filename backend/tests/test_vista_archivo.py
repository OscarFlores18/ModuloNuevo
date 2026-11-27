"""El ojo de «Archivos»: el Excel o el CSV como tabla, sin descargarlo."""

import io
import zipfile
from datetime import datetime

import pytest
from openpyxl import Workbook

from backend.app.core.vista_archivo import SinVista, vista


def test_excel_con_sus_hojas_y_formatos(tmp_path):
    libro = Workbook()
    hoja = libro.active
    hoja.title = "Libro Iva"
    hoja.append(["Fecha", "Neto", "Comprobante"])
    hoja.append([datetime(2026, 6, 1), 1234.5, "A-00002-00001699"])
    hoja.append([None, None, None])
    libro.create_sheet("Faltantes").append(["Nada"])
    ruta = tmp_path / "AUX.xlsx"
    libro.save(ruta)

    v = vista(ruta)
    assert [h["nombre"] for h in v["hojas"]] == ["Libro Iva", "Faltantes"]
    assert v["hojas"][0]["filas"] == [["Fecha", "Neto", "Comprobante"], ["01/06/2026", "1.234,50", "A-00002-00001699"]]
    assert v["hojas"][0]["total_filas"] == 2 and not v["hojas"][0]["cortado"]


def test_csv_de_arca_comprimido_y_con_retornos(tmp_path):
    texto = "Fecha;Tipo\r2026-06-01;1\r2026-06-02;6\r".encode("cp1252")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("comprobantes (montos expresados en pesos).csv", texto)
    ruta = tmp_path / "Mis comprobantes.csv"
    ruta.write_bytes(buffer.getvalue())
    assert vista(ruta)["hojas"][0]["filas"] == [["Fecha", "Tipo"], ["2026-06-01", "1"], ["2026-06-02", "6"]]


def test_corta_las_filas_largas(tmp_path, monkeypatch):
    from backend.app.core import vista_archivo

    monkeypatch.setattr(vista_archivo, "MAX_FILAS", 3)
    ruta = tmp_path / "largo.csv"
    ruta.write_text("\n".join(f"{i};x" for i in range(10)), encoding="utf-8")
    h = vista(ruta)["hojas"][0]
    assert len(h["filas"]) == 3 and h["total_filas"] == 10 and h["cortado"]


def test_otros_formatos_no(tmp_path):
    ruta = tmp_path / "x.pdf"
    ruta.write_bytes(b"%PDF")
    with pytest.raises(SinVista, match="descargalo"):
        vista(ruta)


def test_calcula_las_formulas_y_trae_los_colores(tmp_path):
    """openpyxl graba las fórmulas sin resultado: la vista las calcula (como Excel
    al abrir) y muestra el amarillo de «IVA Ajustado»."""
    from openpyxl.styles import Font, PatternFill

    amarillo = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
    libro = Workbook()
    mc = libro.active
    mc.title = "Mis Comprobantes"
    mc.append(["Clave", "Tipo", "IVA", "IVA Ajustado"])
    mc.append(["A-1", "3 - Nota de Crédito A", 21, None])
    mc["D2"] = '=IF(ISNUMBER(C2), ABS(C2) * IF(AND(ISNUMBER(SEARCH("Crédito", B2)), ISERR(SEARCH("Factura", B2))), -1, 1), 0)'
    mc["D1"].fill = amarillo
    mc["D2"].fill = amarillo
    li = libro.create_sheet("Libro Iva")
    li.append(["Clave", "IVA libro", "IVA ARCA", "Diferencia", "Estado"])
    li.append(["A-1", -21, None, None, None])
    li["C2"] = "=IFERROR(VLOOKUP(A2, 'Mis Comprobantes'!$A$2:$D$2, 4, 0), 0)"
    li["D2"] = "=B2-C2"
    li["E2"] = '=IF(D2=0, "Ok", "Revisar")&" ("&TEXT(7, "000")&")"'
    li["E2"].font = Font(bold=True, color="FFC00000")
    ruta = tmp_path / "AUX.xlsx"
    libro.save(ruta)

    v = vista(ruta)
    hoja_mc, hoja_li = v["hojas"]
    assert hoja_mc["filas"][1][3] == "-21"
    assert hoja_mc["formatos"]["0,3"] == {"fondo": "#FFFF00"}
    assert hoja_mc["formatos"]["1,3"] == {"fondo": "#FFFF00"}
    assert hoja_li["filas"][1][2:] == ["-21", "0", "Ok (007)"]
    assert hoja_li["formatos"]["1,4"] == {"negrita": True, "color": "#C00000"}


def test_formula_que_no_entiende_queda_vacia():
    from backend.app.core.formulas_excel import Libro

    libro = Libro({"H": {(1, 1): 5.0, (1, 2): "=XLOOKUP(A1, A1:A2, A1:A2)", (1, 3): "=A1/0", (1, 4): "=D1"}})
    assert libro.valor("H", 1, 2) is None
    assert libro.valor("H", 1, 3) == "#DIV/0!"
    assert libro.valor("H", 1, 4) == "#REF!"  # circular
