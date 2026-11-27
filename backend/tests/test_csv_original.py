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


def _zip_arca(csv: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(ZIP_ARCA[:-4] + ".csv", csv)
    return buf.getvalue()


def test_arca_manda_el_csv_dentro_de_un_zip():
    original = _csv(_fila(9), _fila(10), _fila(11), codificacion="utf-8").replace(b"\r\n", b"\n")
    salida, faltan = filtrar_csv_arca(_zip_arca(original), "Compras", claves_de_faltantes(_faltantes(), "Compras"))
    lineas = original.splitlines(keepends=True)
    assert salida == lineas[0] + lineas[2] + lineas[3] and faltan == []
    assert unir_partes([_zip_arca(_csv(_fila(1))), _zip_arca(_csv(_fila(2)))]) == _csv(_fila(1), _fila(2))


def test_sin_encabezado_de_arca_falla():
    with pytest.raises(CsvArcaIlegible):
        filtrar_csv_arca(b"a;b;c\r\n1;2;3\r\n", "Compras", {})


def test_unir_partes_no_pierde_filas():
    unido = unir_partes([_csv(_fila(1)), _csv(_fila(2), _fila(3))])
    assert unido == _csv(_fila(1), _fila(2), _fila(3))


# --- Endpoint ------------------------------------------------------------------------


ZIP_ARCA = "comprobantes_consulta_csv_recibidos_1_30500000000_20251201-1200 (montos expresados en pesos).zip"


def _ejecucion(tmp_path, con_csv: bool = True, viejo: bool = False):
    carpeta = tmp_path / "salidas" / "Acme" / "032025"
    carpeta.mkdir(parents=True)
    (carpeta / "AUX - Control de facturas Compras con AFIP - 03-25 - Acme - 1er control.xlsx").write_bytes(_aux("Compras", 2))
    (carpeta / "Mis comprobantes - Recibidos - Acme - 03-2025 al 03-2025.xlsx").write_bytes(b"x")
    if con_csv:
        nombre = "Mis comprobantes - Recibidos - Acme - 03-2025 al 03-2025.csv" if viejo else ZIP_ARCA
        (carpeta / nombre).write_bytes(_zip_arca(_csv(_fila(9), _fila(10), _fila(11))))
    ej = srv.Ejecucion(
        id="csvx123", usuario="test", parametros={"desde": "01/03/2025", "hasta": "31/03/2025"},
        clientes=[srv.ClienteEnCurso("Acme", [], carpeta=str(carpeta), archivos=srv.listar_archivos(carpeta))],
        estado="finalizada",
    )
    with srv._lock:
        srv._ejecuciones[ej.id] = ej
    return ej, carpeta


@pytest.mark.parametrize("viejo", [False, True])
def test_endpoint_devuelve_el_zip_de_arca_sin_tocar(entorno, tmp_path, viejo):
    client, _ = entorno
    ej, carpeta = _ejecucion(tmp_path, viejo=viejo)
    nombre = "Mis comprobantes - Recibidos - Acme - 03-2025 al 03-2025.csv" if viejo else ZIP_ARCA
    original = (carpeta / nombre).read_bytes()
    r = client.post(f"/api/ejecuciones/{ej.id}/clientes/0/csv-xubio", json={"operacion": "Compras"})
    assert r.status_code == 200, r.text
    item = r.json()["resultados"][0]
    assert item["error"] is None and item["partidas"] == {"Compras": 3}
    z = item["zips"]["Compras"]
    assert z["original"] is True and z["nombre"] == ZIP_ARCA
    assert base64.b64decode(z["contenido"]) == original  # byte por byte
    assert len(item["filas"]["Compras"]) == 2


def test_endpoint_sin_csv_pide_volver_a_correr(entorno, tmp_path):
    client, _ = entorno
    ej, _ = _ejecucion(tmp_path, con_csv=False)
    r = client.post(f"/api/ejecuciones/{ej.id}/clientes/0/csv-xubio", json={"operacion": "Compras"})
    assert r.status_code == 404 and "Volvé a correr el control" in r.text


def test_subir_original_no_rearma_el_zip(monkeypatch):
    from backend.app.services import importacion_xubio as imp

    vistos = {}
    monkeypatch.setattr(imp, "_validar", lambda *a: None)
    monkeypatch.setattr(imp, "_correr", lambda cli, op, nombre, contenido, accion, *a: vistos.update(n=nombre, c=contenido, a=accion) or {})
    datos = _zip_arca(_csv(_fila(1)))
    imp.subir_original("Acme", "Compras", "Mis comprobantes - Recibidos - Acme.csv", datos, None, None)
    assert vistos == {"n": ZIP_ARCA, "c": datos, "a": "importar_arca"}


def test_aplicar_cambios_sigue_tomando_el_excel_de_arca(tmp_path):
    ej, _ = _ejecucion(tmp_path)
    assert srv._archivo_de(ej.clientes[0], "Mis comprobantes - Recibidos - ").endswith(".xlsx")
