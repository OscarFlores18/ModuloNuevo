"""Partidas de ARCA no encontradas en el Libro IVA, en el formato CSV de
«Mis Comprobantes» (el que baja ARCA con «Exportar como CSV», Emitidos).

Se usa ese esqueleto para Ventas y para Compras, tal cual (es el que reconoce
Xubio): en Compras, los datos del emisor (el proveedor) van en las columnas
«Receptor» del esqueleto.

Formato, igual al que baja ARCA:
- UTF-8 sin BOM, separador «;», fin de línea LF.
- Encabezado entre comillas; los datos, sin comillas.
- Fecha AAAA-MM-DD; tipo de comprobante y de documento como código.
- Importes con coma decimal y dos decimales; vacío si ARCA no informa el campo.
"""

from __future__ import annotations

import io
import math
import re
import unicodedata
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd

COLUMNAS_CSV = [
    "Fecha de Emisión",
    "Tipo de Comprobante",
    "Punto de Venta",
    "Número Desde",
    "Número Hasta",
    "Cód. Autorización",
    "Tipo Doc. Receptor",
    "Nro. Doc. Receptor",
    "Denominación Receptor",
    "Tipo Cambio",
    "Moneda",
    "Imp. Neto Gravado IVA 0%",
    "IVA 2,5%",
    "Imp. Neto Gravado IVA 2,5%",
    "IVA 5%",
    "Imp. Neto Gravado IVA 5%",
    "IVA 10,5%",
    "Imp. Neto Gravado IVA 10,5%",
    "IVA 21%",
    "Imp. Neto Gravado IVA 21%",
    "IVA 27%",
    "Imp. Neto Gravado IVA 27%",
    "Imp. Neto Gravado Total",
    "Imp. Neto No Gravado",
    "Imp. Op. Exentas",
    "Otros Tributos",
    "Total IVA",
    "Imp. Total",
]

# Columna del CSV -> columna del Excel de ARCA (sin la parte «Receptor/Emisor»).
_IMPORTES = {
    "Imp. Neto Gravado IVA 0%": "Neto Grav. IVA 0%",
    "IVA 2,5%": "IVA 2,5%",
    "Imp. Neto Gravado IVA 2,5%": "Neto Grav. IVA 2,5%",
    "IVA 5%": "IVA 5%",
    "Imp. Neto Gravado IVA 5%": "Neto Grav. IVA 5%",
    "IVA 10,5%": "IVA 10,5%",
    "Imp. Neto Gravado IVA 10,5%": "Neto Grav. IVA 10,5%",
    "IVA 21%": "IVA 21%",
    "Imp. Neto Gravado IVA 21%": "Neto Grav. IVA 21%",
    "IVA 27%": "IVA 27%",
    "Imp. Neto Gravado IVA 27%": "Neto Grav. IVA 27%",
    "Imp. Neto Gravado Total": "Neto Gravado Total",
    "Imp. Neto No Gravado": "Neto No Gravado",
    "Imp. Op. Exentas": "Op. Exentas",
    "Otros Tributos": "Otros Tributos",
    "Total IVA": "Total IVA",
    "Imp. Total": "Imp. Total",
}

# Desglose por alícuota: vacío cuando no aplica.
_ALICUOTAS = set(COLUMNAS_CSV[11:22])

# Tipo de documento: el Excel trae el nombre y el CSV el código de ARCA.
CODIGOS_DOC = {
    "CUIT": 80,
    "CUIL": 86,
    "CDI": 87,
    "LE": 89,
    "LC": 90,
    "CI EXTRANJERA": 91,
    "PASAPORTE": 94,
    "DNI": 96,
    "SIN IDENTIFICAR": 99,
    "CONSUMIDOR FINAL": 99,
}


def _vacio(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return str(v).strip() in ("", "nan", "NaN", "None", "NaT")


def _numero(v) -> float | None:
    if _vacio(v):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    texto = str(v).replace("$", "").replace(" ", "").strip()
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def _importe(v) -> str:
    n = _numero(v)
    return "" if n is None else f"{n:.2f}".replace(".", ",")


def _tipo_cambio(v) -> str:
    n = _numero(v)
    if n is None:
        return ""
    texto = f"{n:.6f}".rstrip("0")
    entero, _, dec = texto.partition(".")
    return f"{entero},{dec.ljust(2, '0')}"


def _entero(v) -> str:
    n = _numero(v)
    return "" if n is None else str(int(n))


def _fecha(v) -> str:
    if _vacio(v):
        return ""
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip().split(" ")[0]
    if m := re.match(r"^(\d{2})[-/](\d{2})[-/](\d{4})$", s):
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    if m := re.match(r"^(\d{4})[-/](\d{2})[-/](\d{2})$", s):
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return s


def _codigo_tipo(v) -> str:
    """«1 - Factura A» -> «1»."""
    if _vacio(v):
        return ""
    m = re.match(r"^\s*(\d+)", str(v))
    return m.group(1) if m else str(v).strip()


def _codigo_doc(v) -> str:
    if _vacio(v):
        return ""
    texto = str(v).strip()
    if re.fullmatch(r"\d+(\.0)?", texto):
        return texto.removesuffix(".0")
    return str(CODIGOS_DOC.get(texto.upper(), texto))


def _texto(v) -> str:
    if _vacio(v):
        return ""
    texto = str(v).strip()
    # ARCA no usa comillas en los datos; solo si el texto rompería la fila.
    if ";" in texto or '"' in texto or "\n" in texto:
        return '"' + texto.replace('"', '""') + '"'
    return texto


def _norm(texto) -> str:
    """Para comparar nombres: sin tildes, en minúscula y con un solo espacio."""
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode("ascii")
    return " ".join(t.lower().split())


def _columna(fila: pd.Series, *nombres: str):
    """El valor de la primera columna que exista. Se compara sin tildes ni
    mayúsculas, así no depende de cómo esté escrito exactamente el encabezado."""
    indice = {_norm(c): c for c in fila.index}
    for nombre in nombres:
        c = indice.get(_norm(nombre))
        if c is not None:
            return fila[c]
    return None


def fila_csv(fila: pd.Series, operacion: str) -> list[str]:
    """Una fila del Excel de ARCA (Emitidos o Recibidos) como fila del CSV."""
    # En Compras la contraparte es el emisor; en Ventas, el receptor.
    parte = "Emisor" if operacion == "Compras" else "Receptor"
    valores = {
        "Fecha de Emisión": _fecha(_columna(fila, "Fecha", "Fecha de Emisión")),
        "Tipo de Comprobante": _codigo_tipo(_columna(fila, "Tipo", "Tipo de Comprobante")),
        "Punto de Venta": _entero(_columna(fila, "Punto de Venta")),
        "Número Desde": _entero(_columna(fila, "Número Desde")),
        "Número Hasta": _entero(_columna(fila, "Número Hasta")),
        "Cód. Autorización": _entero(_columna(fila, "Cód. Autorización")),
        "Tipo Doc. Receptor": _codigo_doc(_columna(fila, f"Tipo Doc. {parte}")),
        "Nro. Doc. Receptor": _entero(_columna(fila, f"Nro. Doc. {parte}")),
        "Denominación Receptor": _texto(_columna(fila, f"Denominación {parte}")),
        "Tipo Cambio": _tipo_cambio(_columna(fila, "Tipo Cambio")),
        "Moneda": _texto(_columna(fila, "Moneda")),
    }
    for csv, excel in _IMPORTES.items():
        valor = _importe(_columna(fila, excel, csv))
        # ARCA deja vacías las alícuotas que no usa el comprobante; el Excel del
        # control las trae en 0.
        if csv in _ALICUOTAS and valor in ("", "0,00", "-0,00"):
            valor = ""
        valores[csv] = valor
    return [valores[c] for c in COLUMNAS_CSV]


def columnas_csv(operacion: str) -> list[str]:
    """El encabezado del esqueleto, igual para Ventas y Compras."""
    return list(COLUMNAS_CSV)


def texto_csv(df: pd.DataFrame, operacion: str) -> str:
    """Las filas en el formato CSV de Mis Comprobantes. Sin filas, solo el encabezado."""
    lineas = [";".join(f'"{c}"' for c in columnas_csv(operacion))]
    lineas += [";".join(fila_csv(fila, operacion)) for _, fila in df.iterrows()]
    return "\n".join(lineas) + "\n"


def escribir_csv(df: pd.DataFrame, operacion: str, ruta: str | Path) -> Path:
    ruta = Path(ruta)
    with open(ruta, "w", encoding="utf-8", newline="\n") as f:
        f.write(texto_csv(df, operacion))
    return ruta


def _fila_de_encabezado(crudo: pd.DataFrame) -> int | None:
    """La fila del encabezado: la primera (de las primeras 15) que tiene «Fecha»
    y alguna columna de número de comprobante."""
    for i in range(min(15, len(crudo))):
        celdas = {_norm(v) for v in crudo.iloc[i] if not _vacio(v)}
        if "fecha" in celdas and any(c.startswith(("numero desde", "punto de venta", "comprobante")) for c in celdas):
            return i
    return None


def _tabla(crudo: pd.DataFrame) -> pd.DataFrame | None:
    """La primera tabla de la hoja: desde el encabezado hasta la primera fila
    vacía (debajo puede venir otro bloque, como «Facturas Originales en USD»)."""
    inicio = _fila_de_encabezado(crudo)
    if inicio is None:
        return None
    encabezado = [str(c).strip() for c in crudo.iloc[inicio]]
    filas = []
    for _, fila in crudo.iloc[inicio + 1:].iterrows():
        if all(_vacio(v) for v in fila):
            break
        filas.append(list(fila))
    return pd.DataFrame(filas, columns=encabezado)


def _operacion_por_texto(texto: str) -> str | None:
    t = _norm(texto)
    ventas = "venta" in t or "emitid" in t
    compras = "compra" in t or "recibid" in t
    return "Ventas" if ventas and not compras else "Compras" if compras and not ventas else None


def _operacion_por_columnas(tabla: pd.DataFrame) -> str:
    """Compras trae al emisor (el proveedor); Ventas solo al receptor (el cliente)."""
    return "Compras" if any("emisor" in _norm(c) for c in tabla.columns) else "Ventas"


def detectar_faltantes(fuente) -> list[tuple[str, pd.DataFrame]]:
    """Las tablas de partidas de ARCA no encontradas en el Libro IVA que haya en el
    Excel, con su operación: [(«Ventas»|«Compras», tabla)].

    No depende de nombres fijos: busca las hojas con «faltante» en el nombre, el
    encabezado dentro de la hoja y la operación por el nombre de la hoja o, si no
    lo dice, por las columnas (Emisor = Compras)."""
    libro = pd.ExcelFile(fuente)
    encontradas: list[tuple[str, pd.DataFrame]] = []
    for hoja in libro.sheet_names:
        if "faltante" not in _norm(hoja):
            continue
        tabla = _tabla(pd.read_excel(libro, sheet_name=hoja, header=None))
        if tabla is None:
            continue
        encontradas.append((_operacion_por_texto(hoja) or _operacion_por_columnas(tabla), tabla))
    return encontradas


def detectar_periodo(fuente) -> str:
    """«MM-AA» del mes que más se repite en las fechas del Excel ("" si no hay)."""
    libro = pd.ExcelFile(fuente)
    meses: Counter[str] = Counter()
    for hoja in libro.sheet_names:
        tabla = _tabla(pd.read_excel(libro, sheet_name=hoja, header=None))
        if tabla is None:
            continue
        columna = next(c for c in tabla.columns if _norm(c) == "fecha")
        for v in tabla[columna]:
            f = _fecha(v)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", f):
                meses[f"{f[5:7]}-{f[2:4]}"] += 1
    return meses.most_common(1)[0][0] if meses else ""


def _comprobante(fila: pd.Series) -> str:
    """«A-00014-01481153»: letra del tipo («1 - Factura A»), punto de venta y número."""
    tipo = str(_columna(fila, "Tipo", "Tipo de Comprobante") or "").strip()
    letra = tipo.split()[-1] if tipo and re.fullmatch(r"[A-EM]", tipo.split()[-1]) else ""
    pv, nro = _entero(_columna(fila, "Punto de Venta")), _entero(_columna(fila, "Número Desde"))
    if not pv and not nro:
        return ""
    cuerpo = f"{int(pv or 0):05d}-{int(nro or 0):08d}"
    return f"{letra}-{cuerpo}" if letra else cuerpo


def clave_fila(fila: pd.Series, operacion: str) -> str:
    """«CUIT|comprobante» de una partida («30500000000|A-00014-01481153»): la misma
    clave con la que la pantalla elige qué filas subir."""
    parte = "Emisor" if operacion == "Compras" else "Receptor"
    return f"{_entero(_columna(fila, f'Nro. Doc. {parte}'))}|{_comprobante(fila)}"


def sin_excluidas(tabla: pd.DataFrame, operacion: str, excluir: set[str]) -> pd.DataFrame:
    """La tabla sin las filas que se destildaron en la pantalla."""
    if not excluir:
        return tabla
    quedan = [clave_fila(fila, operacion) not in excluir for _, fila in tabla.iterrows()]
    return tabla[quedan]


def filas_para_mostrar(fuente) -> dict[str, list[dict]]:
    """Las partidas de ARCA no encontradas en el Libro IVA tal como están en la hoja
    «Faltantes en Libro IVA …» del Excel, listas para la pantalla:
    {«Compras»: [{fecha, tipo, comprobante, cuit, razon, total}, …]}."""
    salida: dict[str, list[dict]] = {}
    for op, tabla in detectar_faltantes(fuente):
        parte = "Emisor" if op == "Compras" else "Receptor"
        filas = []
        for _, fila in tabla.iterrows():
            fecha = _fecha(_columna(fila, "Fecha", "Fecha de Emisión"))
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha):
                fecha = f"{fecha[8:10]}/{fecha[5:7]}/{fecha[0:4]}"
            razon = _columna(fila, f"Denominación {parte}")
            filas.append({
                "fecha": fecha,
                "tipo": "" if _vacio(_columna(fila, "Tipo")) else str(_columna(fila, "Tipo")).strip(),
                "comprobante": _comprobante(fila),
                "cuit": _entero(_columna(fila, f"Nro. Doc. {parte}")),
                "razon": "" if _vacio(razon) else str(razon).strip(),
                "total": _numero(_columna(fila, "Imp. Total", "Importe Total")),
            })
        salida[op] = filas
    return salida


_SUMA = re.compile(r"^=SUM\(([A-Z]+)(\d+):([A-Z]+)(\d+)\)$", re.IGNORECASE)


def libro_sin_arca(fuente, operacion: str) -> list[dict]:
    """Las partidas del Libro IVA no encontradas en ARCA tal como están en el Excel:
    el bloque «Partidas del Libro IVA … no encontradas en ARCA» al pie de la primera
    hoja (columnas de ARCA, el comprobante en la columna A). El total es una fórmula
    =SUM(…) que openpyxl no calcula: se suma acá."""
    from openpyxl import load_workbook
    from openpyxl.utils import column_index_from_string

    ws = load_workbook(fuente).worksheets[0]
    indice = {_norm(c.value): i for i, c in enumerate(ws[1]) if not _vacio(c.value)}

    def col(*nombres: str) -> int | None:
        return next((indice[_norm(n)] for n in nombres if _norm(n) in indice), None)

    inicio = next(
        (r for r in range(2, ws.max_row + 1) if _norm(ws.cell(r, 1).value or "").startswith("partidas del libro iva")),
        None,
    )
    if inicio is None:
        return []
    parte = "Emisor" if operacion == "Compras" else "Receptor"
    c_fecha, c_tipo, c_total = col("Fecha"), col("Tipo"), col("Imp. Total")
    c_cuit, c_razon = col(f"Nro. Doc. {parte}"), col(f"Denominación {parte}")
    filas = []
    # Debajo del título viene una fila de encabezados (=A1, =B1…) y después los datos.
    for r in range(inicio + 2, ws.max_row + 1):
        valores = [c.value for c in ws[r]]
        if all(_vacio(v) for v in valores):
            break

        def v(i: int | None):
            return valores[i] if i is not None and i < len(valores) else None

        total = v(c_total)
        if isinstance(total, str) and (m := _SUMA.match(total.strip())):
            desde, hasta = column_index_from_string(m.group(1)), column_index_from_string(m.group(3))
            total = sum(_numero(ws.cell(r, c).value) or 0 for c in range(desde, hasta + 1))
        fecha = _fecha(v(c_fecha))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha):
            fecha = f"{fecha[8:10]}/{fecha[5:7]}/{fecha[0:4]}"
        filas.append({
            "fecha": fecha,
            "tipo": "" if _vacio(v(c_tipo)) else str(v(c_tipo)).strip(),
            "comprobante": "" if _vacio(valores[0]) else str(valores[0]).strip(),
            "cuit": re.sub(r"\D", "", str(v(c_cuit) or "")),
            "razon": "" if _vacio(v(c_razon)) else str(v(c_razon)).strip(),
            "total": _numero(total),
        })
    return filas


def leer_faltantes_de_control(ruta_control, operacion: str) -> pd.DataFrame:
    """La tabla de partidas de ARCA no encontradas de esa operación (vacía si no hay)."""
    for op, tabla in detectar_faltantes(ruta_control):
        if op == operacion:
            return tabla
    return pd.DataFrame()


def csv_desde_control(ruta_control: str | Path, operacion: str, ruta_csv: str | Path) -> tuple[Path, int]:
    """Del Excel del control al CSV con el esqueleto de Mis Comprobantes."""
    df = leer_faltantes_de_control(ruta_control, operacion)
    return escribir_csv(df, operacion, ruta_csv), len(df)


PREFIJO_CSV = "Partidas de ARCA no encontradas en Libro IVA"


def nombre_csv(operacion: str, cliente: str, mes_anio: str) -> str:
    return f"{PREFIJO_CSV} {operacion} - {cliente}" + (f" - {mes_anio}" if mes_anio else "") + ".csv"


# --- Copia en Excel, para mirar -------------------------------------------------
#
# El CSV es el que se importa. Excel, al abrirlo, muestra el CAE y el CUIT como
# 8,64E+13 (convierte las cifras largas a número). La copia .xlsx trae los
# mismos datos con CAE y CUIT como texto, la fecha como fecha y los importes
# como número.

_COLUMNAS_TEXTO = {"Cód. Autorización", "Nro. Doc. Receptor", "Nro. Doc. Emisor"}
_COLUMNAS_ENTERAS = {"Tipo de Comprobante", "Punto de Venta", "Número Desde", "Número Hasta",
                     "Tipo Doc. Receptor", "Tipo Doc. Emisor"}


def _celda_excel(columna: str, valor: str):
    if valor == "":
        return None
    if columna in _COLUMNAS_TEXTO or columna in ("Denominación Receptor", "Denominación Emisor", "Moneda"):
        return valor.strip('"').replace('""', '"')
    if columna == "Fecha de Emisión":
        try:
            return datetime.strptime(valor, "%Y-%m-%d")
        except ValueError:
            return valor
    if columna in _COLUMNAS_ENTERAS:
        return int(valor) if valor.isdigit() else valor
    try:
        return float(valor.replace(",", "."))
    except ValueError:
        return valor


def excel_de(tablas: dict[str, pd.DataFrame]) -> bytes:
    """Un .xlsx con una hoja por operación, mismas columnas que el CSV."""
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    for op in ("Ventas", "Compras"):
        if op not in tablas:
            continue
        ws = wb.create_sheet(op)
        columnas = columnas_csv(op)
        ws.append(columnas)
        for _, fila in tablas[op].iterrows():
            ws.append([_celda_excel(c, v) for c, v in zip(columnas, fila_csv(fila, op))])
        for i, c in enumerate(columnas, start=1):
            letra = get_column_letter(i)
            ws.column_dimensions[letra].width = max(12, min(40, len(c) + 2))
            formato = ("@" if c in _COLUMNAS_TEXTO else "dd/mm/yyyy" if c == "Fecha de Emisión"
                       else "0" if c in _COLUMNAS_ENTERAS else "#,##0.00")
            for celda in ws[letra][1:]:
                celda.number_format = formato
        ws.freeze_panes = "A2"
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# --- Convertir AUX sueltos (pestaña «Convertir AUX») ------------------------------------
#
# Independiente de los controles: se suben los AUX, se convierten en memoria y se
# devuelve un .zip por cliente y período. No se escribe nada en las carpetas.

_NOMBRE_AUX = re.compile(
    r"^AUX - Control de facturas (Ventas|Compras) con AFIP - (\d{2}-\d{2}) - (.+?)"
    r"(?: - \d+\S* control)?\.xlsx?$",
    re.IGNORECASE,
)


def datos_del_nombre(nombre: str) -> tuple[str | None, str, str]:
    """«AUX - Control de facturas Compras con AFIP - 04-26 - Acme - 1er control.xlsx»
    -> («Compras», «04-26», «Acme»).

    Con otro nombre («testeo compras 1.xlsx»): la operación si el nombre la dice,
    sin período, y como cliente el nombre sin «ventas/compras», así el de Ventas
    y el de Compras del mismo cliente van al mismo .zip («testeo 1»)."""
    nombre = Path(nombre).name
    m = _NOMBRE_AUX.match(nombre)
    if m:
        return m.group(1).capitalize(), m.group(2), m.group(3).strip()
    base = Path(nombre).stem
    sin_op = re.sub(r"\b(ventas?|compras?|emitidos?|recibidos?)\b", " ", base, flags=re.IGNORECASE)
    sin_op = re.sub(r"(\s*-\s*)+", " - ", " ".join(sin_op.split())).strip(" -")
    return _operacion_por_texto(base), "", sin_op or base


def convertir_auxiliares(archivos: list[tuple[str, bytes]], excluir: dict[str, set[str]] | None = None) -> list[dict]:
    """Por cliente y período: los CSV de Ventas y Compras en un .zip (en memoria).

    Todo se detecta del archivo: qué hojas tienen partidas, de qué operación son,
    dónde empieza la tabla y el período (si el nombre no lo dice, por las fechas).

    Cada resultado: {cliente, periodo, archivos, partidas, zips: {operación: {nombre, contenido}},
    excel, avisos, error}.
    Sin partidas en una operación, no va al .zip y queda un aviso; sin partidas en
    ninguna, no hay .zip (zips vacío, excel en None) y solo los avisos.

    `excluir`: por nombre de archivo, las claves «CUIT|comprobante» que no van
    (las que se destildaron al revisar el control)."""
    grupos: dict[tuple[str, str], dict] = {}
    sueltos: list[dict] = []
    for nombre, contenido in archivos:
        nombre = Path(nombre).name
        _, periodo, cliente = datos_del_nombre(nombre)
        try:
            tablas = detectar_faltantes(io.BytesIO(contenido))
            periodo = periodo or detectar_periodo(io.BytesIO(contenido))
        except Exception:
            tablas = []
        if not tablas:
            sueltos.append({
                "cliente": cliente, "periodo": periodo, "archivos": [nombre], "partidas": {}, "zips": {}, "excel": None, "avisos": [],
                "error": f"«{nombre}» no tiene una hoja de partidas faltantes («Faltantes en Libro IVA …»).",
            })
            continue
        grupo = grupos.setdefault(
            (cliente.casefold(), periodo),
            {"cliente": cliente, "periodo": periodo, "archivos": [], "tablas": {}, "error": None},
        )
        grupo["archivos"].append(nombre)
        fuera = (excluir or {}).get(nombre, set())
        for op, tabla in tablas:
            tabla = sin_excluidas(tabla, op, fuera)
            if op in grupo["tablas"]:
                grupo["error"] = f"Hay dos AUX de {op} para el mismo cliente y período: subí uno solo (el del control que querés)."
            grupo["tablas"][op] = tabla

    resultados: list[dict] = []
    for grupo in grupos.values():
        item = {k: grupo[k] for k in ("cliente", "periodo", "archivos", "error")}
        item.update(partidas={}, zips={}, excel=None, avisos=[])
        resultados.append(item)
        if item["error"]:
            continue
        item["partidas"] = {op: len(grupo["tablas"][op]) for op in ("Ventas", "Compras") if op in grupo["tablas"]}
        # Una operación sin partidas no va al .zip (un CSV vacío no sirve para
        # importar): se informa en su lugar.
        con_partidas = {op: t for op, t in grupo["tablas"].items() if len(t)}
        for op, n in item["partidas"].items():
            if not n:
                item["avisos"].append(
                    f"{op}: no hay partidas de ARCA no encontradas en el Libro IVA "
                    "(todo lo de ARCA está cargado). No se genera archivo."
                )
        if not con_partidas:
            continue
        # Un .zip por operación, cada uno con su único CSV en la raíz: en Xubio la
        # importación de Compras y la de Ventas son pantallas separadas.
        sufijo = f" - {item['periodo']}" if item["periodo"] else ""
        try:
            zips = {
                op: {
                    "nombre": f"{PREFIJO_CSV} {op} - {item['cliente']}{sufijo}.zip",
                    "contenido": zip_de_operacion(con_partidas[op], op, item["cliente"], item["periodo"]),
                }
                for op in ("Ventas", "Compras")
                if op in con_partidas
            }
            excel = excel_de(con_partidas)
        except Exception as exc:
            item["error"] = f"No se pudo convertir: {exc}"
            continue
        item["zips"] = zips
        item["excel"] = {"nombre": f"{PREFIJO_CSV} - {item['cliente']}{sufijo}.xlsx", "contenido": excel}
    return resultados + sueltos


def zip_de_operacion(tabla: pd.DataFrame, operacion: str, cliente: str, periodo: str = "") -> bytes:
    """El .zip para «Importar desde Mis Comprobantes (ARCA)» de Xubio: como el de
    ARCA, un solo CSV (el de esa operación) en la raíz."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(nombre_csv(operacion, cliente, periodo), texto_csv(tabla, operacion).encode("utf-8"))
    return buffer.getvalue()

