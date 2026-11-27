"""«Vista previa» de un archivo del control: sus hojas como tablas, para mirarlo en
la app sin descargarlo. Excel (.xlsx/.xls), CSV y el .zip con el CSV que exporta
ARCA. Solo lee: el archivo no se toca."""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, datetime
from pathlib import Path

MAX_FILAS = 500
MAX_COLUMNAS = 80


class VistaNoDisponible(ValueError):
    """No se puede mostrar ese tipo de archivo."""


def _texto_de(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y") if not (valor.hour or valor.minute or valor.second) else valor.strftime("%d/%m/%Y %H:%M")
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor)


def _hoja(nombre: str, filas: list[list]) -> dict:
    """{nombre, filas (las primeras), total, columnas}: la 1.ª fila suele ser el encabezado."""
    # Sin las filas vacías del final (Excel deja muchas).
    while filas and not any(_texto_de(c).strip() for c in filas[-1]):
        filas.pop()
    total = len(filas)
    recortadas = [[_texto_de(c) for c in f[:MAX_COLUMNAS]] for f in filas[:MAX_FILAS]]
    ancho = max((len(f) for f in recortadas), default=0)
    for f in recortadas:
        while len(f) > ancho:
            f.pop()
        while f and len(f) < ancho:
            f.append("")
    return {"nombre": nombre, "filas": recortadas, "total": total, "recortada": total > MAX_FILAS}


def _csv(nombre: str, contenido: bytes) -> dict:
    for codificacion in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            texto = contenido.decode(codificacion)
            break
        except UnicodeDecodeError:
            continue
    primera = texto.split("\n", 1)[0]
    separador = ";" if primera.count(";") >= primera.count(",") else ","
    return _hoja(nombre, list(csv.reader(io.StringIO(texto, newline=""), delimiter=separador)))


def _xlsx(contenido: bytes) -> list[dict]:
    from openpyxl import load_workbook

    libro = load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    try:
        hojas = []
        for ws in libro.worksheets:
            filas = []
            for fila in ws.iter_rows(values_only=True):
                filas.append(list(fila))
                if len(filas) > MAX_FILAS + 200:  # alcanza para contar «hay más»
                    break
            hojas.append(_hoja(ws.title, filas))
        return hojas
    finally:
        libro.close()


def _xls(contenido: bytes) -> list[dict]:
    import xlrd

    libro = xlrd.open_workbook(file_contents=contenido)
    hojas = []
    for hoja in libro.sheets():
        filas = []
        for i in range(min(hoja.nrows, MAX_FILAS + 1)):
            fila = []
            for celda in hoja.row(i):
                if celda.ctype == xlrd.XL_CELL_DATE:
                    fila.append(xlrd.xldate.xldate_as_datetime(celda.value, libro.datemode))
                else:
                    fila.append(celda.value)
            filas.append(fila)
        vista = _hoja(hoja.name, filas)
        vista["total"], vista["recortada"] = hoja.nrows, hoja.nrows > MAX_FILAS
        hojas.append(vista)
    return hojas


def vista_previa(ruta: Path) -> dict:
    """{nombre, hojas: [{nombre, filas, total, recortada}], adentro?: nombres del .zip}."""
    contenido = ruta.read_bytes()
    ext = ruta.suffix.lower()
    salida: dict = {"nombre": ruta.name, "hojas": []}
    try:
        if contenido[:2] == b"PK" and ext != ".xlsx":
            # El .zip de ARCA (o un control viejo que lo guardó como .csv).
            with zipfile.ZipFile(io.BytesIO(contenido)) as z:
                nombres = z.namelist()
                salida["adentro"] = nombres
                for n in nombres:
                    if n.lower().endswith(".csv"):
                        salida["hojas"].append(_csv(Path(n).name, z.read(n)))
            if not salida["hojas"]:
                raise VistaNoDisponible("El .zip no trae ningún CSV para mostrar.")
        elif ext == ".xlsx":
            salida["hojas"] = _xlsx(contenido)
        elif ext == ".xls":
            salida["hojas"] = _xls(contenido)
        elif ext == ".csv":
            salida["hojas"] = [_csv(ruta.name, contenido)]
        else:
            raise VistaNoDisponible("Este tipo de archivo no tiene vista previa: descargalo para abrirlo.")
    except VistaNoDisponible:
        raise
    except Exception as exc:  # archivo roto o formato raro
        raise VistaNoDisponible(f"No se pudo leer «{ruta.name}» para la vista previa: {exc}") from exc
    return salida
