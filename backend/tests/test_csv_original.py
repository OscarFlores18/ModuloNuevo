"""«Subir a Xubio» con el CSV que bajó ARCA: solo las líneas faltantes, byte por byte."""

from __future__ import annotations

import base64
import io
import zipfile

import pytest

from backend.app.auditor.csv_arca import leer_faltantes_de_control
from backend.app.auditor.csv_original import CsvArcaIlegible, claves_de_faltantes, filtrar_csv_arca, unir_partes
from backend.app.services import ejecuciones as srv

from .test_csv_arca import _aux

ENCABEZADO = (
    '"Fecha de Emisión";"Tipo de Comprobante";"Punto de Venta";"Número Desde";"Número Hasta";'
    '"Cód. Autorización";"Tipo Doc. Emisor";"Nro. Doc. Emisor";"Denominación Emisor";"Imp. Total"\r\n'
)


def _fila(nro: int, cuit: str = "30500000000", tipo: str = "1", total: str = "121,00") -> str:
    return f"2025-03-03;{tipo};00001;{nro:08d};{nro:08d};123;80;{cuit};PROVEEDOR S.A.;{total}\r\n"


def _csv(*filas: str, codificacion: str = "cp1252") -> bytes:
    return (ENCABEZADO + "".join(filas)).encode(codificacion)


def _faltantes(n: int = 2):
    return leer_faltantes_de_control(io.BytesIO(_aux("Compras", n)), "Compras")


def test_deja_solo_las_faltantes_sin_tocarlas():
    original = _csv(_fila(9), _fila(10, total="121,0000"), _fila(11), _fila(10, cuit="20111111112"))
    salida, faltan = filtrar_csv_arca(original, "Compras", claves_de_faltantes(_faltantes(), "Compras"))
    lineas = original.splitlines(keepends=True)
    assert salida == lineas[0] + lineas[2] + lineas[3]  # encabezado y las dos faltantes, idénticas
    assert faltan == []


def test_respeta_las_destildadas_y_avisa_las_que_no_estan():
    original = _csv(_fila(10))
    buscadas = claves_de_faltantes(_faltantes(), "Compras", excluir=set())
    salida, faltan = filtrar_csv_arca(original, "Compras", buscadas)
    assert faltan == ["30500000000|A-00001-00000011"]
    sin_10 = claves_de_faltantes(_faltantes(), "Compras", excluir={"30500000000|A-00001-00000010"})
    assert list(sin_10.values()) == ["30500000000|A-00001-00000011"]


def test_utf8_con_bom_y_ultima_linea_sin_salto():
    original = _csv(_fila(10), codificacion="utf-8-sig").rstrip(b"\r\n")
    salida, _ = filtrar_csv_arca(original, "Compras", claves_de_faltantes(_faltantes(1), "Compras"))
    assert salida.startswith(b"\xef\xbb\xbf") and salida.endswith(b"\r\n")


def test_sin_encabezado_de_arca_falla():
    with pytest.raises(CsvArcaIlegible):
        filtrar_csv_arca(b"a;b;c\r\n1;2;3\r\n", "Compras", {})


def test_unir_partes_no_pierde_filas():
    unido = unir_partes([_csv(_fila(1)), _csv(_fila(2), _fila(3))])
    assert unido == _csv(_fila(1), _fila(2), _fila(3))


# --- Endpoint ------------------------------------------------------------------------


def _ejecucion(tmp_path, con_csv: bool = True):
    carpeta = tmp_path / "salidas" / "Acme" / "032025"
    carpeta.mkdir(parents=True)
    (carpeta / "AUX - Control de facturas Compras con AFIP - 03-25 - Acme - 1er control.xlsx").write_bytes(_aux("Compras", 2))
    (carpeta / "Mis comprobantes - Recibidos - Acme - 03-2025 al 03-2025.xlsx").write_bytes(b"x")
    if con_csv:
        (carpeta / "Mis comprobantes - Recibidos - Acme - 03-2025 al 03-2025.csv").write_bytes(_csv(_fila(9), _fila(10), _fila(11)))
    ej = srv.Ejecucion(
        id="csvx123", usuario="test", parametros={"desde": "01/03/2025", "hasta": "31/03/2025"},
        clientes=[srv.ClienteEnCurso("Acme", [], carpeta=str(carpeta), archivos=srv.listar_archivos(carpeta))],
        estado="finalizada",
    )
    with srv._lock:
        srv._ejecuciones[ej.id] = ej
    return ej


def test_endpoint_arma_el_zip_con_el_csv_de_arca(entorno, tmp_path):
    client, _ = entorno
    ej = _ejecucion(tmp_path)
    r = client.post(f"/api/ejecuciones/{ej.id}/clientes/0/csv-xubio", json={"operacion": "Compras", "excluir": ["30500000000|A-00001-00000011"]})
    assert r.status_code == 200, r.text
    item = r.json()["resultados"][0]
    assert item["error"] is None and item["partidas"] == {"Compras": 1} and item["archivos"][0].startswith("AUX - ")
    assert len(item["filas"]["Compras"]) == 1
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(item["zips"]["Compras"]["contenido"]))) as z:
        (nombre,) = z.namelist()
        assert nombre.startswith("Partidas de ARCA no encontradas en Libro IVA Compras - Acme")
        assert z.read(nombre) == _csv(_fila(10))


def test_endpoint_sin_csv_pide_volver_a_correr(entorno, tmp_path):
    client, _ = entorno
    ej = _ejecucion(tmp_path, con_csv=False)
    r = client.post(f"/api/ejecuciones/{ej.id}/clientes/0/csv-xubio", json={"operacion": "Compras"})
    assert r.status_code == 404 and "Volvé a correr el control" in r.text


def test_aplicar_cambios_sigue_tomando_el_excel_de_arca(tmp_path):
    ej = _ejecucion(tmp_path)
    assert srv._archivo_de(ej.clientes[0], "Mis comprobantes - Recibidos - ").endswith(".xlsx")
