"""Comprobantes desde planillas: XLSX, XLS y CSV.

Sirve para el Excel/CSV de «Mis Comprobantes» de ARCA (Emitidos y Recibidos) y
para planillas propias: el encabezado se busca en las primeras filas y cada
columna se reconoce por sinónimos («Pto. Vta.», «Punto de Venta», «PV»…).
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import pandas as pd

from .modelo import (
    EXENTO, IVAS, NETO_TOTAL, NETOS, NO_GRAVADO, OTROS, TOTAL, TOTAL_IVA,
    Comprobante, Parte, codigo_documento, codigo_tipo, completar, digitos, entero, fecha,
    importe, moneda, norm, vacio,
)

_ALICUOTA = re.compile(r"(?<![\d,])(10,5|2,5|27|21|5|0)(?![\d,])")


def campo(encabezado) -> str | None:
    """El campo del comprobante al que corresponde un encabezado (o None)."""
    if vacio(encabezado):
        return None
    t = re.sub(r"(\d)\.(\d)", r"\1,\2", str(encabezado))
    n = norm(t).replace(".", " ").replace(":", " ").replace("°", " ").replace("º", " ")
    n = " ".join(n.split())
    if n.startswith("fecha") and not re.search(r"vto|venc|pago|cae", n):
        return "fecha"
    if re.fullmatch(r"tipo|tipo (de )?(comprobante|cbte|cpte|comp)|comprobante tipo|tipo factura", n):
        return "tipo"
    if re.search(r"punto de venta|pto vta|punto vta|^p ?v$|^pto venta|^sucursal$", n):
        return "punto_venta"
    if re.search(r"(numero|nro|num) hasta", n):
        return "numero_hasta"
    if re.search(r"(numero|nro|num) desde|^(numero|nro|num)$|(numero|nro|num) (de )?(comprobante|cbte|cpte|factura)", n):
        return "numero"
    if re.fullmatch(r"comprobante|factura|nro factura|(numero|nro) completo", n):
        return "comprobante"
    if re.search(r"autorizacion|^cae$|^c a e$", n):
        return "cae"
    for rol in ("emisor", "receptor"):
        if rol in n:
            if "tipo doc" in n:
                return f"{rol}_doc_tipo"
            if re.search(r"(nro|numero) doc|cuit|documento", n):
                return f"{rol}_doc"
            if re.search(r"denominacion|razon social|nombre", n):
                return f"{rol}_nombre"
    if re.fullmatch(r"proveedor|razon social proveedor|nombre proveedor", n):
        return "emisor_nombre"
    if re.fullmatch(r"cuit proveedor|cuit del proveedor", n):
        return "emisor_doc"
    if re.fullmatch(r"cliente|razon social cliente|nombre cliente", n):
        return "receptor_nombre"
    if re.fullmatch(r"cuit cliente|cuit del cliente", n):
        return "receptor_doc"
    if re.fullmatch(r"tipo doc(umento)?", n):
        return "doc_tipo"
    if re.fullmatch(r"cuit|cuil|cuit/cuil|nro doc(umento)?|documento|cuit contraparte", n):
        return "doc"
    if re.fullmatch(r"razon social|denominacion|nombre|apellido y nombre", n):
        return "nombre"
    if re.search(r"tipo (de )?cambio|cotizacion", n):
        return "tipo_cambio"
    if n == "moneda":
        return "moneda"
    if "no gravado" in n:
        return NO_GRAVADO
    if "exent" in n:
        return EXENTO
    if re.search(r"otros tributos|percepcion|otros impuestos|impuestos internos", n):
        return OTROS
    if "total iva" in n or n in ("iva", "importe iva"):
        return TOTAL_IVA
    if "iva" in n and (m := _ALICUOTA.search(n)):
        a = m.group(1)
        if re.search(r"neto|grav", n):
            return NETOS[a]
        return IVAS.get(a)
    if re.search(r"neto gravado total|^neto gravado$|^(importe |imp )?neto$|^subtotal gravado$|^gravado$", n):
        return NETO_TOTAL
    if re.search(r"imp total|importe total|^total$|total comprobante|total factura", n):
        return TOTAL
    return None


def _fila_encabezado(crudo: pd.DataFrame) -> tuple[int, dict[int, str]] | None:
    """La primera fila (de las primeras 20) con fecha, algo del número y algún importe."""
    for i in range(min(20, len(crudo))):
        campos = {j: campo(v) for j, v in enumerate(crudo.iloc[i])}
        campos = {j: c for j, c in campos.items() if c}
        valores = set(campos.values())
        numero = valores & {"numero", "comprobante", "punto_venta"}
        if "fecha" in valores and numero and len(valores) >= 3:
            return i, campos
    return None


def _operacion_por_texto(texto: str) -> str | None:
    t = norm(texto)
    ventas = bool(re.search(r"venta|emitid", t))
    compras = bool(re.search(r"compra|recibid|proveedor", t))
    return "Ventas" if ventas and not compras else "Compras" if compras and not ventas else None


def _comprobante_completo(texto) -> tuple[str, int | None, int | None]:
    """«A-0001-00001234», «FC A 0001-00001234» o «00001-00001234» -> (letra, pv, nro)."""
    t = str(texto or "").upper()
    m = re.search(r"(?:\b([ABCEM])\b)?\D*?(\d{1,5})\s*-\s*(\d{1,8})\b", t)
    if not m:
        return "", None, None
    return m.group(1) or "", int(m.group(2)), int(m.group(3))


def _comprobante_de_fila(valores: dict[str, object], operacion: str | None, archivo: str, n: int) -> Comprobante | None:
    def v(nombre):
        return valores.get(nombre)

    c = Comprobante(id=f"{archivo}#{n}", archivo=archivo, pagina=None, metodo="planilla", operacion=operacion)
    c.fecha = fecha(v("fecha"))
    c.tipo = codigo_tipo(v("tipo"))
    c.punto_venta = entero(v("punto_venta"))
    c.numero = entero(v("numero"))
    if not vacio(v("comprobante")) and (c.punto_venta is None or c.numero is None):
        letra, pv, nro = _comprobante_completo(v("comprobante"))
        c.punto_venta = c.punto_venta or pv
        c.numero = c.numero or nro
        if c.tipo is None and letra:
            c.tipo = codigo_tipo(f"factura {letra}")
    c.cae = digitos(v("cae"))
    c.emisor = Parte(doc_tipo=codigo_documento(v("emisor_doc_tipo")), doc_nro=digitos(v("emisor_doc")), nombre=_texto(v("emisor_nombre")))
    c.receptor = Parte(doc_tipo=codigo_documento(v("receptor_doc_tipo")), doc_nro=digitos(v("receptor_doc")), nombre=_texto(v("receptor_nombre")))
    # «CUIT» / «Razón social» a secas: la contraparte de la operación.
    if not vacio(v("doc")) or not vacio(v("nombre")):
        parte = c.emisor if operacion == "Compras" else c.receptor
        parte.doc_tipo = parte.doc_tipo or codigo_documento(v("doc_tipo"))
        parte.doc_nro = parte.doc_nro or digitos(v("doc"))
        parte.nombre = parte.nombre or _texto(v("nombre"))
    tc = importe(v("tipo_cambio"))
    c.tipo_cambio = tc if tc else 1.0
    c.moneda = moneda(v("moneda"))
    c.importes = {}
    for col, valor in valores.items():
        if col[0].isupper() or col.startswith("IVA"):
            n_imp = importe(valor)
            if n_imp is not None:
                c.importes[col] = n_imp
    # Las filas de totales o vacías no son comprobantes.
    if not c.fecha and not c.numero:
        return None
    if not c.numero and not c.importes:
        return None
    return completar(c)


def _texto(v) -> str:
    return "" if vacio(v) else str(v).strip()


def comprobantes_de_tabla(crudo: pd.DataFrame, archivo: str, operacion: str | None, desde: int = 0) -> list[Comprobante]:
    """Los comprobantes de una hoja sin encabezado (header=None)."""
    encontrado = _fila_encabezado(crudo)
    if encontrado is None:
        return []
    inicio, campos = encontrado
    # Sin pista por el nombre: Recibidos trae al emisor, Emitidos solo al receptor.
    if operacion is None:
        valores = set(campos.values())
        if valores & {"emisor_doc", "emisor_nombre"} and not valores & {"receptor_nombre"}:
            operacion = "Compras"
        elif valores & {"receptor_doc", "receptor_nombre"} and not valores & {"emisor_doc", "emisor_nombre"}:
            operacion = "Ventas"
    salida: list[Comprobante] = []
    vacias = 0
    for i in range(inicio + 1, len(crudo)):
        fila = crudo.iloc[i]
        if all(vacio(x) for x in fila):
            vacias += 1
            # Debajo puede venir otro bloque (por ejemplo «Facturas en USD»).
            if vacias >= 1 and salida:
                break
            continue
        vacias = 0
        valores = {}
        for j, c in campos.items():
            if c not in valores or vacio(valores[c]):
                valores[c] = fila.iloc[j] if j < len(fila) else None
        comp = _comprobante_de_fila(valores, operacion, archivo, desde + len(salida) + 1)
        if comp is not None:
            salida.append(comp)
    return salida


def _leer_csv(contenido: bytes) -> pd.DataFrame:
    for codificacion in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            texto = contenido.decode(codificacion)
            break
        except UnicodeDecodeError:
            continue
    muestra = texto[:4096]
    try:
        separador = csv.Sniffer().sniff(muestra, delimiters=";,\t|").delimiter
    except csv.Error:
        separador = ";" if muestra.count(";") >= muestra.count(",") else ","
    filas = list(csv.reader(io.StringIO(texto), delimiter=separador))
    ancho = max((len(f) for f in filas), default=0)
    return pd.DataFrame([f + [None] * (ancho - len(f)) for f in filas], dtype=object)


def extraer_planilla(nombre: str, contenido: bytes, operacion: str | None = None) -> list[Comprobante]:
    """Todos los comprobantes del archivo. La operación sale del nombre del
    archivo o de la hoja; si no, de las columnas."""
    nombre = Path(nombre).name
    sufijo = Path(nombre).suffix.lower()
    pista = operacion or _operacion_por_texto(Path(nombre).stem)
    if sufijo in (".csv", ".txt"):
        return comprobantes_de_tabla(_leer_csv(contenido), nombre, pista)
    motor = "xlrd" if sufijo == ".xls" else "openpyxl"
    salida: list[Comprobante] = []
    with pd.ExcelFile(io.BytesIO(contenido), engine=motor) as libro:
        for hoja in libro.sheet_names:
            crudo = pd.read_excel(libro, sheet_name=hoja, header=None, dtype=object)
            salida += comprobantes_de_tabla(crudo, nombre, pista or _operacion_por_texto(hoja), len(salida))
    return salida
