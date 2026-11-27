"""Paso 4, exportar: la `Lectura` ya revisada a Excel. Con compra o venta, el .xls del
modelo de importación de Xubio; si no, un .xlsx con lo leído.
"""

from __future__ import annotations
import difflib
import io
import itertools
import logging
import re
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, field_validator
from ..modelo import TIPOS, cuit_valido, fecha as fecha_iso, importe, nombre_tipo, norm
from ..texto_factura import _tipo as tipo_del_texto
from ..texto_factura import cuits as cuits_del_texto
from .datos import ETIQUETAS_ENCABEZADO, ETIQUETAS_TOTALES, Hoja, Item, Lectura, TOTALES
from .normalizar import _alicuota_de, _cantidad, _fmt_alicuota, agrupar_items, totales_efectivos
from .validar import verificar

logger = logging.getLogger(__name__)


def nombre_excel(lectura: Lectura, operacion: str | None = None, empresa: str = "") -> str:
    """Como el AUX de un control: «Conversor de facturas Compras con AFIP - 10-26 - Cliente.xlsx»."""
    from ...core.controles import mes_anio
    from ...core.paths import limpiar_nombre

    base = re.sub(r'[\\/*?:"<>|]', "", Path(lectura.archivo).stem).strip() or "documento"
    if lectura.tipo != "comprobante" or operacion not in ("Compras", "Ventas"):
        return f"{base}.xlsx"
    cliente = limpiar_nombre(empresa).rstrip(". ") or base
    fecha = lectura.encabezado.get("fecha", "")
    periodo = f" - {mes_anio(fecha)}" if re.fullmatch(r"\d{2}/\d{2}/\d{4}", fecha) else ""
    # El modelo de Xubio es .xls (Excel 97-2003).
    return f"Conversor de facturas {operacion} con AFIP{periodo} - {cliente}.xls"


class Extras(BaseModel):
    """Lo que se completa en la factura «como en Xubio» y no sale del archivo."""

    condicion: str = Field(default="", max_length=60)
    vencimiento: str = Field(default="", max_length=10)
    productos: list[str] = Field(default_factory=list, max_length=2000)
    centros: list[str] = Field(default_factory=list, max_length=2000)
    dto: list[float | None] = Field(default_factory=list, max_length=2000)
    # Un solo producto para todo el detalle: una fila por alícuota y los artículos en
    # «PRODUCTOOBSERVACION». Vacío: una fila por ítem.
    agrupar: str = Field(default="", max_length=60)


# El modelo de Xubio «Importar comprobantes» (modelo-importacion-ventas): una fila con
# la cabecera y una por ítem, unidas por NUMERODECONTROL. En compras va el proveedor.
COLUMNAS_IMPORTACION = [
    "NUMERODECONTROL", "CLIENTE", "TIPO", "NUMERO", "FECHA", "VENCIMIENTODELCOBRO",
    "COMPROBANTEASOCIADO", "MONEDA", "COTIZACION", "OBSERVACIONES", "PRODUCTOSERVICIO",
    "CENTRODECOSTO", "PRODUCTOOBSERVACION", "CANTIDAD", "PRECIO", "DESCUENTO", "IMPORTE", "IVA",
]


# El modelo de Xubio para compras (ej-importacion-compras.xls, hoja «Hoja1»): sin
# descuento ni comprobante asociado, con fecha fiscal y exento/no gravado; los impuestos
# internos y las percepciones van como filas más, con su nombre en PRODUCTOSERVICIOPERCEPCION.
COLUMNAS_COMPRAS = [
    "NUMERODECONTROL", "PROVEEDOR", "TIPO", "NUMERO", "FECHA", "VENCIMIENTODELPAGO", "MONEDA",
    "COTIZACION", "FECHAFISCAL", "OBSERVACIONES", "PRODUCTOSERVICIOPERCEPCION", "CENTRODECOSTO",
    "PRODUCTOOBSERVACION", "CANTIDAD", "PRECIO", "IMPORTE", "IVA", "EXENOGRAV",
]
# Nombre de cada impuesto en Xubio. «Impuestos Internos» es el del modelo; los otros
# tienen que coincidir con los que tiene el estudio en Xubio.
TRIBUTOS_COMPRAS = (
    ("impuestos_internos", "Impuestos Internos"),
    ("percepcion_iibb", "Percepción IIBB Buenos Aires"),
    ("otros_tributos", "Otros Tributos"),
)
# Tique Factura A / B / C (081, 082, 111): en compras, Xubio los importa como tipo 5.
TIQUES_FACTURA = {81, 82, 111}


def _tipo_xubio(tipo: str) -> int | str:
    """Código de ARCA -> tipo de Xubio: 1 factura, 2 nota de débito, 3 nota de crédito."""
    m = re.match(r"\s*(\d+)", tipo or "")
    if not m:
        return ""
    codigo = int(m.group(1))
    for xubio, codigos in ((1, {1, 6, 11, 19, 51, 81, 82, 111}), (2, {2, 7, 12, 20, 52}), (3, {3, 8, 13, 21, 53})):
        if codigo in codigos:
            return xubio
    return codigo


def _numero_xubio(lectura: Lectura) -> str:
    """«00001-00000001» con la letra del tipo -> «A-00001-00000001»."""
    numero = lectura.encabezado.get("numero", "")
    m = re.search(r"\b([ABCEM])\s*$", lectura.encabezado.get("tipo", ""))
    pv, _, nro = numero.partition("-")
    if not (pv.isdigit() and nro.isdigit()):
        return numero
    return f"{m.group(1) + '-' if m else ''}{pv.zfill(5)}-{nro.zfill(8)}"


def _fecha_xubio(texto: str):
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%Y")
    except ValueError:
        return texto or None


def _tipo_compra(tipo: str) -> int | str:
    m = re.match(r"\s*(\d+)", tipo or "")
    return 5 if m and int(m.group(1)) in TIQUES_FACTURA else _tipo_xubio(tipo)


def _precio_neto(cantidad: float | None, importe: float | None, observacion: str | None) -> tuple:
    """PRECIO x CANTIDAD tiene que dar el IMPORTE (neto), no el precio con IVA de la foto.
    Si el unitario no da justo con dos decimales, va como 1 con la cantidad en la observación."""
    cant = cantidad or 1
    if importe is None:
        return cant, None, observacion
    unitario = round(importe / cant, 2)
    if round(unitario * cant, 2) == round(importe, 2):
        return cant, unitario, observacion
    return 1, importe, f"{_cantidad(cant)}x {observacion or ''}".strip()


def filas_compras(lectura: Lectura, extras: Extras | None = None) -> list[list]:
    """Cabecera, detalle (o el detalle agrupado) y una fila por cada impuesto o percepción."""
    extras = extras or Extras()
    e, t = lectura.encabezado, totales_efectivos(lectura)
    fecha = _fecha_xubio(e.get("fecha", ""))
    vence = _fecha_xubio(extras.vencimiento or e.get("vencimiento", "")) or fecha
    filas = [[1, e.get("emisor", ""), _tipo_compra(e.get("tipo", "")), _numero_xubio(lectura), fecha, vence,
              "pesos argentinos", "", fecha, Path(lectura.archivo).stem[:200]] + [None] * 8]
    detalle = []  # (producto, centro, observación, cantidad, precio, importe, alícuota)
    if extras.agrupar.strip() and lectura.items:
        centro = next((c for c in extras.centros if c), None)
        detalle = [(extras.agrupar.strip(), centro, obs, 1, suma, suma, a) for a, suma, obs in agrupar_items(lectura.items)]
    elif lectura.items:
        for n, it in enumerate(lectura.items):
            def de(lista, defecto=None):
                return lista[n] if n < len(lista) and lista[n] not in ("", None) else defecto
            cant, precio, obs = _precio_neto(it.cantidad, it.importe, it.descripcion or None)
            detalle.append((de(extras.productos, it.producto or it.codigo or it.descripcion[:60]), de(extras.centros),
                            obs, cant, precio, it.importe, it.alicuota))
    elif t.get("neto_gravado"):
        neto = t["neto_gravado"]
        detalle = [(extras.agrupar.strip() or "Según factura", None, None, 1, neto, neto, _alicuota_de(neto, t.get("iva") or 0.0))]
    # Lo exento y no gravado va en la primera fila del detalle.
    exento = round((t.get("no_gravado") or 0) + (t.get("exento") or 0), 2) or None
    for n, (prod, centro, obs, cant, precio, importe, alicuota) in enumerate(detalle):
        filas.append([1] + [None] * 9 + [prod, centro, obs, cant, precio, importe, alicuota, exento if n == 0 else None])
    internos_items = round(sum(i.impuestos_internos or 0 for i in lectura.items), 2)
    for campo, nombre in TRIBUTOS_COMPRAS:
        valor = t.get(campo) or (internos_items if campo == "impuestos_internos" else None)
        if valor:
            filas.append([1] + [None] * 9 + [nombre, None, None, 1, None, valor, None, None])
    return filas


def filas_importacion(lectura: Lectura, operacion: str, extras: Extras | None = None) -> list[list]:
    extras = extras or Extras()
    e, t = lectura.encabezado, lectura.totales
    lado = "emisor" if operacion == "Compras" else "receptor"
    fecha = _fecha_xubio(e.get("fecha", ""))
    cabecera = [
        1, e.get(lado, ""), _tipo_xubio(e.get("tipo", "")), _numero_xubio(lectura), fecha,
        _fecha_xubio(extras.vencimiento or e.get("vencimiento", "")) or fecha,
        "", "Pesos Argentinos", "", Path(lectura.archivo).stem[:200],
    ] + [None] * 8
    items = lectura.items or [Item(
        descripcion="Según factura", cantidad=1, precio=t.get("neto_gravado"), importe=t.get("neto_gravado"),
        alicuota=_alicuota_de(t.get("neto_gravado") or 0.0, t.get("iva") or 0.0),
    )]
    filas = [cabecera]
    if extras.agrupar.strip() and lectura.items:
        centro = next((c for c in extras.centros if c), None)
        for alicuota, suma, detalle in agrupar_items(lectura.items):
            filas.append([1] + [None] * 9 + [extras.agrupar.strip(), centro, detalle, 1, suma, None, suma, alicuota])
        return filas
    for n, it in enumerate(items):
        def de(lista, defecto=None):
            return lista[n] if n < len(lista) and lista[n] not in ("", None) else defecto
        filas.append([1] + [None] * 9 + [
            de(extras.productos, it.producto or it.codigo or it.descripcion[:60]), de(extras.centros), it.descripcion or None,
            it.cantidad, it.precio, de(extras.dto) or None, it.importe, it.alicuota,
        ])
    return filas


def _hoja_impuestos(lectura: Lectura) -> list[list]:
    """Ventas: el modelo no trae columnas para impuestos internos ni percepciones; van en
    otra hoja (la importación lee la primera) para cargarlos en «Percepciones e Impuestos».
    Con los totales efectivos: si los tributos vinieron renglón por renglón, salen de ahí."""
    t = totales_efectivos(lectura)
    internos = [(n + 1, i.descripcion, i.impuestos_internos) for n, i in enumerate(lectura.items) if i.impuestos_internos]
    filas: list[list] = [["Impuestos internos", n, desc, v] for n, desc, v in internos]
    if t.get("impuestos_internos") and not internos:
        filas.append(["Impuestos internos", None, "Total de la factura", t["impuestos_internos"]])
    if t.get("percepcion_iibb"):
        filas.append(["Percepción IIBB Bs. As.", None, None, t["percepcion_iibb"]])
    if t.get("otros_tributos"):
        filas.append(["Otros tributos", None, None, t["otros_tributos"]])
    return [["CONCEPTO", "ITEM", "DETALLE", "IMPORTE"], *filas] if filas else []


def _xls(hojas: list[tuple[str, list[list], set[int], set[int]]]) -> bytes:
    """Excel 97-2003 (.xls), como los modelos de Xubio: (nombre, filas, columnas de
    fecha, columnas de importe)."""
    import xlwt

    fecha = xlwt.easyxf(num_format_str="dd/mm/yyyy")
    numero = xlwt.easyxf(num_format_str="#,##0.00")
    negrita = xlwt.easyxf("font: bold on")
    wb = xlwt.Workbook(encoding="utf-8")
    for nombre, filas, fechas, numeros in hojas:
        ws = wb.add_sheet(nombre)
        for i, fila in enumerate(filas):
            for j, valor in enumerate(fila):
                if valor is None or valor == "":
                    continue
                if i == 0:
                    ws.write(i, j, valor, negrita)
                elif j in fechas and isinstance(valor, datetime):
                    ws.write(i, j, valor, fecha)
                elif j in numeros and isinstance(valor, (int, float)):
                    ws.write(i, j, valor, numero)
                else:
                    ws.write(i, j, valor)
        for j, titulo in enumerate(filas[0] if filas else []):
            ws.col(j).width = 256 * max(12, len(str(titulo)) + 2)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def excel_importacion(lectura: Lectura, operacion: str, extras: Extras | None = None) -> bytes:
    """El .xls con el modelo de importación de Xubio (Compras: «Hoja1»; Ventas: «Sheet0»)."""
    if operacion == "Compras":
        # En compras los impuestos internos y las percepciones van como filas del comprobante.
        return _xls([("Hoja1", [COLUMNAS_COMPRAS, *filas_compras(lectura, extras)], {4, 5, 8}, {14, 15, 17})])
    hojas = [("Sheet0", [COLUMNAS_IMPORTACION, *filas_importacion(lectura, operacion, extras)], {4, 5}, {14, 15, 16})]
    if impuestos := _hoja_impuestos(lectura):
        hojas.append(("Impuestos y percepciones", impuestos, set(), {3}))
    return _xls(hojas)


def generar(lectura: Lectura, operacion: str | None = None, extras: Extras | None = None) -> bytes:
    """Una factura con el estudio elegido sale con el modelo de importación de Xubio; lo
    demás (planillas, o sin compra/venta) se transcribe como antes."""
    if lectura.tipo == "comprobante" and operacion in ("Compras", "Ventas"):
        return excel_importacion(lectura, operacion, extras)
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    negrita = Font(bold=True)
    titulo = Font(bold=True, size=13)
    cabecera = PatternFill("solid", fgColor="E2E8F0")
    moneda = '#,##0.00'
    colores = {"error": "FEE2E2", "aviso": "FEF3C7", "ok": "DCFCE7"}

    wb = Workbook()
    wb.remove(wb.active)

    def anchos(ws, minimos: dict[int, int] | None = None):
        for col in ws.columns:
            largo = max((len(str(c.value)) for c in col if c.value is not None), default=8)
            letra = get_column_letter(col[0].column)
            ws.column_dimensions[letra].width = min(max(largo + 2, (minimos or {}).get(col[0].column, 10)), 60)

    if lectura.tipo == "tabla":
        for h in lectura.hojas or [Hoja()]:
            nombre = re.sub(r"[\\/*?:\[\]]", "", h.nombre)[:31] or "Hoja"
            ws = wb.create_sheet(nombre if nombre not in wb.sheetnames else f"{nombre[:28]}_{len(wb.sheetnames)}")
            for fila in h.filas:
                ws.append(list(fila))
            for fila in ws.iter_rows():
                for c in fila:
                    if isinstance(c.value, float):
                        c.number_format = moneda
            anchos(ws)
    else:
        e, t = lectura.encabezado, totales_efectivos(lectura)
        ws = wb.create_sheet("Comprobante")
        ws.append([e.get("tipo") or "Comprobante"])
        ws["A1"].font = titulo
        ws.append([])
        for campo, etiqueta in ETIQUETAS_ENCABEZADO.items():
            valor = e.get(campo, "")
            if campo == "fecha" and valor:
                try:
                    valor = datetime.strptime(valor, "%d/%m/%Y")
                except ValueError:
                    pass
            ws.append([etiqueta, valor])
            ws.cell(ws.max_row, 1).font = negrita
            if isinstance(valor, datetime):
                ws.cell(ws.max_row, 2).number_format = "DD/MM/YYYY"
        ws.append([])
        ws.append(["Importes"])
        ws.cell(ws.max_row, 1).font = titulo
        filas_importes = {}
        for campo in TOTALES:
            ws.append([ETIQUETAS_TOTALES[campo], t.get(campo)])
            filas_importes[campo] = ws.max_row
            ws.cell(ws.max_row, 1).font = negrita
            ws.cell(ws.max_row, 2).number_format = moneda
        partes = ",".join(f"B{filas_importes[k]}" for k in TOTALES if k != "total")
        ws.append(["Suma de los importes", f"=SUM({partes})"])
        ws.cell(ws.max_row, 2).number_format = moneda
        ws.append(["Diferencia con el total", f"=B{filas_importes['total']}-B{ws.max_row}"])
        ws.cell(ws.max_row, 2).number_format = moneda
        if lectura.iva_alicuotas:
            ws.append([])
            ws.append(["IVA por alícuota"])
            ws.cell(ws.max_row, 1).font = titulo
            ws.append(["Alícuota %", "Base imponible", "IVA"])
            for c in ws[ws.max_row]:
                c.font, c.fill = negrita, cabecera
            for x in lectura.iva_alicuotas:
                ws.append([x.alicuota, x.base, x.importe])
                for col in (2, 3):
                    ws.cell(ws.max_row, col).number_format = moneda
        if lectura.tributos:
            ws.append([])
            ws.append(["Otros tributos"])
            ws.cell(ws.max_row, 1).font = titulo
            ws.append(["Tributo", "Base imponible", "Importe"])
            for c in ws[ws.max_row]:
                c.font, c.fill = negrita, cabecera
            for x in lectura.tributos:
                ws.append([f"{x.codigo} - {x.descripcion}" if x.codigo else x.descripcion, x.base, x.importe])
                for col in (2, 3):
                    ws.cell(ws.max_row, col).number_format = moneda
        ws.append([])
        ws.append(["Archivo", lectura.archivo])
        ws.append(["Leído con", {"ocr": "OCR (foto o escaneo)", "ia": "IA (Gemini) de la imagen", "pdf": "texto del PDF", "planilla": "planilla"}[lectura.metodo]])
        anchos(ws, {1: 26, 2: 30})

        wd = wb.create_sheet("Detalle")
        wd.append(["Código", "Descripción", "Cantidad", "Precio unitario", "Alícuota IVA %", "Importe"])
        for c in wd[1]:
            c.font, c.fill = negrita, cabecera
        for x in lectura.items:
            wd.append([x.codigo or None, x.descripcion, x.cantidad, x.precio, x.alicuota, x.importe])
            for col in (4, 6):
                wd.cell(wd.max_row, col).number_format = moneda
        ultima = wd.max_row
        wd.append([None, "Total del detalle", None, None, None, f"=SUM(F2:F{max(ultima, 2)})" if lectura.items else 0])
        wd.cell(wd.max_row, 2).font = negrita
        wd.cell(wd.max_row, 6).number_format = moneda
        wd.cell(wd.max_row, 6).font = negrita
        fila_suma = wd.max_row
        wd.append([None, "Neto gravado del comprobante", None, None, None, f"=Comprobante!B{filas_importes['neto_gravado']}"])
        wd.cell(wd.max_row, 6).number_format = moneda
        wd.append([None, "Diferencia", None, None, None, f"=F{wd.max_row}-F{fila_suma}"])
        wd.cell(wd.max_row, 6).number_format = moneda
        anchos(wd, {2: 34})
        wd.freeze_panes = "A2"

    wv = wb.create_sheet("Verificación")
    wv.append(["Estado", "Dato", "Detalle"])
    for c in wv[1]:
        c.font, c.fill = negrita, cabecera
    nombres = {**ETIQUETAS_ENCABEZADO, **ETIQUETAS_TOTALES, "items": "Detalle", "hojas": "Hojas"}
    nombres.update({f"tributo_{i}": x.descripcion or "Otros tributos" for i, x in enumerate(lectura.tributos)})
    nombres.update({f"iva_{i}": f"IVA {_fmt_alicuota(x.alicuota)}".strip() for i, x in enumerate(lectura.iva_alicuotas)})
    for x in lectura.verificaciones or verificar(lectura):
        wv.append([{"ok": "Bien", "aviso": "Revisar", "error": "Error"}[x.nivel], nombres.get(x.campo, x.campo), x.mensaje])
        wv.cell(wv.max_row, 1).fill = PatternFill("solid", fgColor=colores[x.nivel])
        wv.cell(wv.max_row, 3).alignment = Alignment(wrap_text=True, vertical="top")
    for aviso in lectura.avisos:
        wv.append(["Nota", "", aviso])
    wv.column_dimensions["A"].width = 10
    wv.column_dimensions["B"].width = 22
    wv.column_dimensions["C"].width = 100

    if lectura.texto:
        wt = wb.create_sheet("Texto leído")
        wt.append(["Lo que se leyó del archivo, línea por línea"])
        wt["A1"].font = negrita
        for linea in lectura.texto.splitlines():
            if linea.strip():
                wt.append([linea])
        wt.column_dimensions["A"].width = 90

    salida = io.BytesIO()
    wb.save(salida)
    return salida.getvalue()
