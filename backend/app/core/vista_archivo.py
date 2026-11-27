"""Vista previa de un archivo del control (Excel o CSV) sin descargarlo.

Devuelve las hojas como filas de texto, ya formateadas como se ven en Excel
(fechas dd/mm/aaaa, importes con coma decimal), cortadas en ``MAX_FILAS``. De
los .xlsx también calcula las fórmulas (openpyxl las graba sin resultado) y
trae el color de fondo y la negrita de cada celda.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from pathlib import Path

MAX_FILAS = 500
MAX_COLUMNAS = 60
MAX_HOJAS = 20
EXTENSIONES = (".xlsx", ".xlsm", ".xls", ".csv", ".txt")
# Hasta este tamaño el Excel se abre entero (fórmulas y formato); más grande,
# solo los valores guardados, para no tardar.
MAX_BYTES_CON_FORMULAS = 8_000_000


class SinVista(Exception):
    """El archivo no se puede mostrar (formato no soportado o ilegible)."""


def _texto(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "VERDADERO" if v else "FALSO"
    if isinstance(v, datetime):
        return v.strftime("%d/%m/%Y") if (v.hour, v.minute, v.second) == (0, 0, 0) else v.strftime("%d/%m/%Y %H:%M")
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, float):
        if v != v:  # NaN (celda vacía en pandas)
            return ""
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return f"{v:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    if isinstance(v, str) and v.startswith("="):
        return ""  # fórmula que no se pudo calcular
    return str(v).strip()


def _hoja(nombre: str, filas_crudas, formatos=None) -> dict:
    """``formatos``: por fila de entrada, {columna: formato} (colores y negrita)."""
    filas: list[list[str]] = []
    marcas: dict[str, dict] = {}
    total = 0
    for i, fila in enumerate(filas_crudas):
        textos = [_texto(v) for v in list(fila)[:MAX_COLUMNAS]]
        if not any(textos):
            # Las vacías del medio se muestran; las del final se recortan abajo.
            if filas:
                total += 1
                if len(filas) < MAX_FILAS:
                    filas.append([])
            continue
        total += 1
        if len(filas) < MAX_FILAS:
            if formatos is not None and i < len(formatos):
                for c, formato in formatos[i].items():
                    if c < MAX_COLUMNAS:
                        marcas[f"{len(filas)},{c}"] = formato
            filas.append(textos)
    while filas and not any(filas[-1]):
        filas.pop()
        total -= 1
    ancho = max((max((i + 1 for i, c in enumerate(f) if c), default=0) for f in filas), default=0)
    filas = [(f + [""] * ancho)[:ancho] for f in filas]
    hoja = {"nombre": nombre, "filas": filas, "total_filas": max(total, len(filas)), "cortado": total > len(filas)}
    visibles = {
        k: v for k, v in marcas.items() if int(k.split(",")[0]) < len(filas) and int(k.split(",")[1]) < ancho
    }
    if visibles:
        hoja["formatos"] = visibles
    return hoja


def _color(color) -> str | None:
    """«FFFFFF00» → «#FFFF00»; los colores de tema e indexados no se resuelven."""
    if color is None or getattr(color, "type", None) != "rgb" or not isinstance(color.rgb, str):
        return None
    if color.rgb == "00000000" or len(color.rgb) < 6:
        return None
    return "#" + color.rgb[-6:].upper()


def _formato(celda) -> dict | None:
    formato = {}
    relleno = celda.fill
    if relleno is not None and relleno.fill_type == "solid":
        fondo = _color(relleno.fgColor)
        if fondo and fondo != "#FFFFFF":
            formato["fondo"] = fondo
    letra = celda.font
    if letra is not None:
        if letra.b:
            formato["negrita"] = True
        tinta = _color(letra.color)
        if tinta and tinta != "#000000":
            formato["color"] = tinta
    return formato or None


def _xlsx(contenido: bytes) -> list[dict]:
    from openpyxl import load_workbook

    guardados = load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    try:
        if len(contenido) > MAX_BYTES_CON_FORMULAS:
            return [_hoja(h.title, h.iter_rows(values_only=True)) for h in guardados.worksheets[:MAX_HOJAS]]
        cache = {
            h.title: {
                (f, c): v
                for f, fila in enumerate(h.iter_rows(values_only=True), start=1)
                for c, v in enumerate(fila, start=1)
                if v is not None
            }
            for h in guardados.worksheets
        }
    finally:
        guardados.close()

    from .formulas_excel import Libro

    libro = load_workbook(io.BytesIO(contenido), data_only=False)
    calculo = Libro(
        {
            ws.title: {(c.row, c.column): c.value for fila in ws.iter_rows() for c in fila if c.value is not None}
            for ws in libro.worksheets
        }
    )
    hojas = []
    for ws in libro.worksheets[:MAX_HOJAS]:
        valores_guardados = cache.get(ws.title, {})
        filas, formatos = [], []
        for f, fila in enumerate(ws.iter_rows(max_col=min(ws.max_column, MAX_COLUMNAS)), start=1):
            valores, marcas = [], {}
            for c, celda in enumerate(fila):
                v = celda.value
                if isinstance(v, str) and v.startswith("="):
                    # Excel guarda el resultado al grabar; openpyxl no: se calcula acá.
                    # Pasadas las filas que se muestran solo importa que no esté vacía.
                    guardado = valores_guardados.get((f, c + 1))
                    if guardado is not None:
                        v = guardado
                    elif len(filas) <= MAX_FILAS + 50:
                        v = calculo.valor(ws.title, f, c + 1)
                    else:
                        v = "·"
                valores.append(v)
                formato = _formato(celda)
                if formato:
                    marcas[c] = formato
            filas.append(valores)
            formatos.append(marcas)
        hojas.append(_hoja(ws.title, filas, formatos))
    return hojas


def _csv(contenido: bytes) -> list[dict]:
    if contenido.startswith(b"PK"):
        # ARCA baja el «.csv» comprimido: un .zip con el CSV adentro.
        import zipfile

        with zipfile.ZipFile(io.BytesIO(contenido)) as z:
            adentro = [n for n in z.namelist() if n.lower().endswith((".csv", ".txt"))]
            if not adentro:
                raise SinVista("El archivo comprimido no tiene un CSV adentro.")
            contenido = z.read(adentro[0])
    texto = ""
    for codificacion in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            texto = contenido.decode(codificacion)
            break
        except UnicodeDecodeError:
            continue
    muestra = texto[:4000]
    try:
        dialecto = csv.Sniffer().sniff(muestra, delimiters=";,\t|")
        separador = dialecto.delimiter
    except csv.Error:
        separador = ";" if muestra.count(";") >= muestra.count(",") else ","
    # splitlines: ARCA corta las líneas con \r, \r\n o \n según el archivo.
    return [_hoja("CSV", csv.reader(texto.splitlines(), delimiter=separador))]


def vista(ruta: Path) -> dict:
    sufijo = ruta.suffix.lower()
    if sufijo not in EXTENSIONES:
        raise SinVista(f"«{ruta.name}» no se puede ver acá: descargalo para abrirlo.")
    contenido = ruta.read_bytes()
    try:
        if sufijo in (".csv", ".txt"):
            hojas = _csv(contenido)
        elif sufijo == ".xls":
            import pandas as pd

            with pd.ExcelFile(io.BytesIO(contenido), engine="xlrd") as libro:
                hojas = [
                    _hoja(str(h), pd.read_excel(libro, sheet_name=h, header=None, dtype=object).itertuples(index=False, name=None))
                    for h in libro.sheet_names[:MAX_HOJAS]
                ]
        else:
            hojas = _xlsx(contenido)
    except SinVista:
        raise
    except Exception as exc:
        raise SinVista(f"No se pudo leer «{ruta.name}» para mostrarlo: descargalo para abrirlo.") from exc
    return {"nombre": ruta.name, "hojas": hojas}
