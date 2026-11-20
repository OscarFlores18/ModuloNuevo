"""El comprobante normalizado: lo que se extrae de cualquier archivo, lo que se
revisa en la pantalla y lo que se pasa al CSV de «Mis Comprobantes»."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field

from ..auditor.csv_arca import COLUMNAS_CSV

# Importes del CSV de Mis Comprobantes, en orden (las claves de `importes`).
IMPORTES = COLUMNAS_CSV[11:]
NETOS = {
    "0": "Imp. Neto Gravado IVA 0%",
    "2,5": "Imp. Neto Gravado IVA 2,5%",
    "5": "Imp. Neto Gravado IVA 5%",
    "10,5": "Imp. Neto Gravado IVA 10,5%",
    "21": "Imp. Neto Gravado IVA 21%",
    "27": "Imp. Neto Gravado IVA 27%",
}
IVAS = {"2,5": "IVA 2,5%", "5": "IVA 5%", "10,5": "IVA 10,5%", "21": "IVA 21%", "27": "IVA 27%"}
NETO_TOTAL = "Imp. Neto Gravado Total"
NO_GRAVADO = "Imp. Neto No Gravado"
EXENTO = "Imp. Op. Exentas"
OTROS = "Otros Tributos"
TOTAL_IVA = "Total IVA"
TOTAL = "Imp. Total"

# Códigos de comprobante de ARCA (los más comunes).
TIPOS: dict[int, str] = {
    1: "Factura A", 2: "Nota de Débito A", 3: "Nota de Crédito A", 4: "Recibo A",
    6: "Factura B", 7: "Nota de Débito B", 8: "Nota de Crédito B", 9: "Recibo B",
    11: "Factura C", 12: "Nota de Débito C", 13: "Nota de Crédito C", 15: "Recibo C",
    19: "Factura E", 20: "Nota de Débito E", 21: "Nota de Crédito E",
    51: "Factura M", 52: "Nota de Débito M", 53: "Nota de Crédito M", 54: "Recibo M",
    81: "Tique Factura A", 82: "Tique Factura B", 83: "Tique", 111: "Tique Factura C",
    201: "Factura de Crédito Electrónica MiPyMEs (FCE) A",
    202: "Nota de Débito Electrónica MiPyMEs (FCE) A",
    203: "Nota de Crédito Electrónica MiPyMEs (FCE) A",
    206: "Factura de Crédito Electrónica MiPyMEs (FCE) B",
    207: "Nota de Débito Electrónica MiPyMEs (FCE) B",
    208: "Nota de Crédito Electrónica MiPyMEs (FCE) B",
    211: "Factura de Crédito Electrónica MiPyMEs (FCE) C",
    212: "Nota de Débito Electrónica MiPyMEs (FCE) C",
    213: "Nota de Crédito Electrónica MiPyMEs (FCE) C",
}
_POR_CLASE = {
    "factura": {"A": 1, "B": 6, "C": 11, "E": 19, "M": 51},
    "debito": {"A": 2, "B": 7, "C": 12, "E": 20, "M": 52},
    "credito": {"A": 3, "B": 8, "C": 13, "E": 21, "M": 53},
    "recibo": {"A": 4, "B": 9, "C": 15, "M": 54},
}
DOCUMENTOS = {"CUIT": 80, "CUIL": 86, "CDI": 87, "PASAPORTE": 94, "DNI": 96, "CONSUMIDOR FINAL": 99, "SIN IDENTIFICAR": 99}

Metodo = Literal["planilla", "qr", "pdf", "ocr", "manual"]


class Parte(BaseModel):
    doc_tipo: int | None = None  # 80 CUIT, 96 DNI, 99 consumidor final
    doc_nro: str = Field(default="", max_length=20)
    nombre: str = Field(default="", max_length=200)


class Comprobante(BaseModel):
    """Un comprobante. `fecha` en AAAA-MM-DD; `importes` con las columnas del CSV."""

    id: str = Field(default="", max_length=80)
    archivo: str = Field(default="", max_length=260)
    pagina: int | None = None
    metodo: Metodo = "manual"
    incluir: bool = True
    operacion: Literal["Ventas", "Compras"] | None = None
    cliente: str = Field(default="", max_length=200)
    fecha: str = Field(default="", max_length=10)
    tipo: int | None = None
    punto_venta: int | None = None
    numero: int | None = None
    cae: str = Field(default="", max_length=20)
    emisor: Parte = Field(default_factory=Parte)
    receptor: Parte = Field(default_factory=Parte)
    moneda: str = Field(default="$", max_length=5)
    tipo_cambio: float = 1.0
    importes: dict[str, float | None] = Field(default_factory=dict)
    avisos: list[str] = Field(default_factory=list)

    def contraparte(self) -> Parte:
        """En Ventas, el receptor (el cliente del cliente); en Compras, el emisor."""
        return self.emisor if self.operacion == "Compras" else self.receptor


# --- Normalizar valores sueltos ----------------------------------------------------


def norm(texto) -> str:
    """Sin tildes, en minúscula y con un solo espacio."""
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode("ascii")
    return " ".join(t.lower().split())


def vacio(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and v != v:
        return True
    return str(v).strip() in ("", "nan", "NaN", "None", "NaT", "-")


def importe(v) -> float | None:
    """«1.210,50», «1,210.50», «$ 1210.5», «(12,00)» o un número -> float."""
    if vacio(v):
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    t = str(v).strip().replace("$", "").replace("ARS", "").replace(" ", "").replace(" ", "")
    negativo = t.startswith("(") and t.endswith(")") or t.startswith("-")
    t = t.strip("()-+")
    if not t or not re.fullmatch(r"[\d.,]+", t):
        return None
    if "," in t and "." in t:
        # El último separador es el decimal.
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        # Coma decimal (como se escribe acá); varias comas son de miles.
        t = t.replace(",", ".") if t.count(",") == 1 else t.replace(",", "")
    elif t.count(".") > 1:
        t = t.replace(".", "")
    elif "." in t and len(t.split(".")[1]) == 3 and len(t.split(".")[0]) <= 3 and t.split(".")[0] != "0":
        # «1.210» en una factura argentina es mil doscientos diez.
        t = t.replace(".", "")
    try:
        n = float(t)
    except ValueError:
        return None
    return round(-n if negativo else n, 2)


def entero(v) -> int | None:
    if vacio(v):
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v)
    t = re.sub(r"\D", "", str(v).split(".")[0] if re.fullmatch(r"\d+\.0+", str(v).strip()) else str(v))
    return int(t) if t else None


def digitos(v) -> str:
    if vacio(v):
        return ""
    if isinstance(v, float):
        return str(int(v))
    t = str(v).strip()
    if re.fullmatch(r"\d+\.0+", t):
        t = t.split(".")[0]
    return re.sub(r"\D", "", t)


def fecha(v) -> str:
    """A AAAA-MM-DD. Acepta datetime, DD/MM/AAAA, DD-MM-AA, AAAA-MM-DD y AAAAMMDD."""
    if vacio(v):
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip().split(" ")[0].split("T")[0]
    if m := re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})", s):
        d, mes, a = int(m.group(1)), int(m.group(2)), m.group(3)
        a = int(a) + 2000 if len(a) == 2 else int(a)
        return f"{a:04d}-{mes:02d}-{d:02d}" if 1 <= mes <= 12 and 1 <= d <= 31 else ""
    if m := re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s):
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    if m := re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s):
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""


def codigo_tipo(v) -> int | None:
    """«1 - Factura A», «Factura A», «FC A», «NC B», «001» o 1 -> código de ARCA."""
    if vacio(v):
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v)
    t = norm(v)
    if m := re.match(r"^\s*(\d{1,3})\b", t):
        return int(m.group(1))
    letra = None
    if m := re.search(r"(?:^|[\s\"'(])([abcem])(?:$|[\s\"')])", t):
        letra = m.group(1).upper()
    if "credito electronica" in t or "fce" in t or "mipyme" in t:
        base = 203 if "nota de credito" in t else 202 if "nota de debito" in t else 201
        return base + {"A": 0, "B": 5, "C": 10}.get(letra or "A", 0)
    if "credito" in t or re.search(r"\bnc\b", t):
        clase = "credito"
    elif "debito" in t or re.search(r"\bnd\b", t):
        clase = "debito"
    elif "recibo" in t:
        clase = "recibo"
    elif re.search(r"factura|\bfc\b|\bfa\b|\bfac\b", t):
        clase = "factura"
    else:
        return None
    return _POR_CLASE[clase].get(letra or "", None)


def nombre_tipo(codigo: int | None) -> str:
    """«1 - Factura A» (lo que muestran las tablas de ARCA)."""
    if codigo is None:
        return ""
    return f"{codigo} - {TIPOS[codigo]}" if codigo in TIPOS else str(codigo)


def letra_tipo(codigo: int | None) -> str:
    nombre = TIPOS.get(codigo or -1, "")
    return nombre.split()[-1] if nombre and nombre.split()[-1] in ("A", "B", "C", "E", "M") else ""


def codigo_documento(v) -> int | None:
    if vacio(v):
        return None
    t = str(v).strip()
    if re.fullmatch(r"\d+(\.0)?", t):
        return int(float(t))
    return DOCUMENTOS.get(t.upper())


def cuit_valido(cuit: str) -> bool:
    """Dígito verificador del CUIT/CUIL (módulo 11)."""
    c = re.sub(r"\D", "", cuit or "")
    if len(c) != 11:
        return False
    pesos = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
    resto = 11 - sum(int(a) * b for a, b in zip(c[:10], pesos)) % 11
    dv = 0 if resto == 11 else 9 if resto == 10 else resto
    return dv == int(c[10])


def moneda(v) -> str:
    """Como la escribe ARCA en Mis Comprobantes: «$» para pesos."""
    if vacio(v):
        return "$"
    t = str(v).strip().upper()
    if t in ("$", "PES", "ARS", "PESOS", "PESO"):
        return "$"
    if t in ("DOL", "USD", "U$S", "US$", "DOLAR", "DOLARES", "DÓLAR"):
        return "DOL"
    return t[:5]


# --- Completar y validar -------------------------------------------------------------


def completar(c: Comprobante) -> Comprobante:
    """Lo que se deduce de lo que ya está: totales de neto e IVA, documento CUIT,
    el neto de la única alícuota y el total si falta."""
    imp = {k: v for k, v in c.importes.items() if k in IMPORTES and v is not None}
    alicuotas_con_iva = [a for a, col in IVAS.items() if imp.get(col)]
    netos = [col for col in NETOS.values() if imp.get(col) is not None]
    # Un solo IVA y el neto gravado total: ese neto es de esa alícuota.
    if len(alicuotas_con_iva) == 1 and not netos and imp.get(NETO_TOTAL):
        imp[NETOS[alicuotas_con_iva[0]]] = imp[NETO_TOTAL]
    # Un solo IVA sin ningún neto: el neto sale de la alícuota.
    if len(alicuotas_con_iva) == 1 and not netos and not imp.get(NETO_TOTAL):
        a = alicuotas_con_iva[0]
        imp[NETOS[a]] = round(imp[IVAS[a]] / (float(a.replace(",", ".")) / 100), 2)
    if NETO_TOTAL not in imp and any(imp.get(col) is not None for col in NETOS.values()):
        imp[NETO_TOTAL] = round(sum(imp.get(col) or 0 for col in NETOS.values()), 2)
    if TOTAL_IVA not in imp and any(imp.get(col) is not None for col in IVAS.values()):
        imp[TOTAL_IVA] = round(sum(imp.get(col) or 0 for col in IVAS.values()), 2)
    if TOTAL not in imp and imp:
        imp[TOTAL] = round(sum(imp.get(k) or 0 for k in (NETO_TOTAL, NO_GRAVADO, EXENTO, OTROS, TOTAL_IVA)), 2)
    c.importes = {k: imp.get(k) for k in IMPORTES if imp.get(k) is not None}
    for parte in (c.emisor, c.receptor):
        parte.doc_nro = digitos(parte.doc_nro)
        if parte.doc_tipo is None and len(parte.doc_nro) == 11:
            parte.doc_tipo = 80
        parte.nombre = " ".join(parte.nombre.split())
    c.cae = digitos(c.cae)
    c.moneda = moneda(c.moneda)
    if not c.tipo_cambio or c.tipo_cambio <= 0:
        c.tipo_cambio = 1.0
    return c


def validar(c: Comprobante) -> list[str]:
    """Lo que hay que mirar antes de subirlo. Vacío = listo."""
    avisos: list[str] = []
    if not c.operacion:
        avisos.append("Falta elegir si es venta o compra.")
    if not c.cliente:
        avisos.append("Falta elegir el cliente (la empresa en Xubio).")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", c.fecha or ""):
        avisos.append("Falta la fecha.")
    if c.tipo is None:
        avisos.append("Falta el tipo de comprobante.")
    elif c.tipo not in TIPOS:
        avisos.append(f"Tipo de comprobante {c.tipo} desconocido.")
    if not c.punto_venta:
        avisos.append("Falta el punto de venta.")
    if not c.numero:
        avisos.append("Falta el número.")
    if c.importes.get(TOTAL) is None:
        avisos.append("Falta el importe total.")
    if c.cae and len(c.cae) != 14:
        avisos.append("El CAE debería tener 14 dígitos.")
    if not c.cae:
        avisos.append("Sin CAE: Xubio puede no aceptarlo en «Importar desde Mis Comprobantes».")
    if c.operacion:
        parte = c.contraparte()
        quien = "del proveedor" if c.operacion == "Compras" else "del cliente"
        if not parte.doc_nro and parte.doc_tipo != 99:
            avisos.append(f"Falta el CUIT {quien}.")
        elif parte.doc_tipo in (80, 86, None) and parte.doc_nro and not cuit_valido(parte.doc_nro):
            avisos.append(f"El CUIT {quien} ({parte.doc_nro}) no es válido.")
    total = c.importes.get(TOTAL)
    partes = [c.importes.get(k) for k in (NETO_TOTAL, NO_GRAVADO, EXENTO, OTROS, TOTAL_IVA)]
    if total is not None and any(p is not None for p in partes):
        suma = round(sum(p or 0 for p in partes), 2)
        if abs(suma - total) > max(1.0, abs(total) * 0.001):
            avisos.append(f"Los importes no suman el total (suman {formato(suma)}, total {formato(total)}).")
    return avisos


def formato(n: float) -> str:
    return f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def clave(c: Comprobante) -> tuple:
    """Para no repetir el mismo comprobante (ORIGINAL / DUPLICADO, o dos fotos)."""
    return (c.emisor.doc_nro or c.receptor.doc_nro, c.tipo, c.punto_venta, c.numero)


def fila_arca(c: Comprobante) -> dict:
    """Con las columnas que entiende `csv_arca.fila_csv` (las del Excel de ARCA)."""
    parte = "Emisor" if c.operacion == "Compras" else "Receptor"
    otra = c.contraparte()
    fila = {
        "Fecha": c.fecha,
        "Tipo": nombre_tipo(c.tipo),
        "Punto de Venta": c.punto_venta,
        "Número Desde": c.numero,
        "Número Hasta": c.numero,
        "Cód. Autorización": c.cae or None,
        f"Tipo Doc. {parte}": otra.doc_tipo if otra.doc_tipo is not None else (80 if len(otra.doc_nro) == 11 else None),
        f"Nro. Doc. {parte}": otra.doc_nro or None,
        f"Denominación {parte}": otra.nombre or None,
        "Tipo Cambio": c.tipo_cambio,
        "Moneda": moneda(c.moneda),
    }
    for col in IMPORTES:
        fila[col] = c.importes.get(col)
    return fila
