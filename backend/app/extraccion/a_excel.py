"""«Pasar a Excel»: cualquier archivo (foto, captura, PDF, CSV, XLS) a un .xlsx,
con una verificación de lo leído antes de armarlo.

1. `leer`: saca los datos del archivo.
   - Planillas y CSV: las hojas tal cual (los números y fechas como tales).
   - Fotos y capturas, en cascada (`_leer_imagen`): el QR de ARCA si tiene (exacto)
     y, según OCR_MODE, Gemini (gemini.py) y/o el OCR local: PaddleOCR (paddle.py,
     opcional) o Tesseract con OpenCV (tesseract.py). Tesseract
     lee varias pasadas si la primera no cierra: lo que cada una lee distinto queda
     marcado y se elige la que cierra con las cuentas (neto + IVA + otros = total).
   - PDF escaneados: Tesseract, igual que las fotos.
   - PDF digitales: el texto del PDF (sin OCR).
   Si el texto parece un comprobante, se arman encabezado, detalle y totales; si
   no, el texto queda como tabla (columnas separadas por espacios).
2. `verificar`: lo que conviene mirar antes de armar el Excel: CUIT con dígito
   verificador mal (y la corrección 0↔8 de las ticketeadoras), totales que no
   cierran, IVA de cada alícuota que no da con su base o con los ítems, detalle
   que no suma el neto, fecha
   imposible, datos leídos distinto en cada pasada. Con sugerencia cuando la
   cuenta la da (p. ej. el neto que falta para llegar al total).
3. `generar`: el .xlsx (encabezado, detalle con fórmulas, verificación y el
   texto leído; o las hojas de la planilla).
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

from .modelo import TIPOS, cuit_valido, fecha as fecha_iso, importe, nombre_tipo, norm
from .texto_factura import _tipo as tipo_del_texto
from .texto_factura import cuits as cuits_del_texto

logger = logging.getLogger(__name__)

PLANILLAS = {".xlsx", ".xlsm", ".xls", ".csv", ".txt"}
IMAGENES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
FORMATOS = PLANILLAS | IMAGENES | {".pdf"}

MAX_FILAS = 20000
MAX_PAGINAS = 20
TOLERANCIA = 0.05
ALICUOTAS = (21.0, 10.5, 27.0, 5.0, 2.5)

TOTALES = ("neto_gravado", "no_gravado", "exento", "iva", "impuestos_internos", "percepcion_iibb", "otros_tributos", "total")
ETIQUETAS_TOTALES = {
    "neto_gravado": "Neto gravado",
    "no_gravado": "Neto no gravado",
    "exento": "Exento",
    "iva": "IVA",
    "impuestos_internos": "Impuestos internos",
    "percepcion_iibb": "Percepción IIBB Bs. As.",
    "otros_tributos": "Otros tributos",
    "total": "Total",
}
ETIQUETAS_ENCABEZADO = {
    "emisor": "Emisor",
    "emisor_cuit": "CUIT del emisor",
    "receptor": "Receptor",
    "receptor_cuit": "CUIT del receptor",
    "tipo": "Tipo de comprobante",
    "numero": "Número",
    "fecha": "Fecha",
}

Nivel = Literal["ok", "aviso", "error"]


def _cortar(valor, campo):
    """El texto, sin pasar del max_length del campo."""
    if not isinstance(valor, str):
        return valor
    largo = next((m.max_length for m in campo.metadata if getattr(m, "max_length", None)), None)
    return valor[:largo].rstrip() if largo and len(valor) > largo else valor


class Item(BaseModel):
    codigo: str = Field(default="", max_length=40)
    descripcion: str = Field(default="", max_length=300)
    cantidad: float | None = None
    precio: float | None = None
    alicuota: float | None = None
    importe: float | None = None
    impuestos_internos: float | None = None
    # Producto o servicio sugerido para Xubio (lo propone la IA o las reglas de abajo).
    producto: str = Field(default="", max_length=60)
    # Lo que decía antes de autocorregirlo (vacío si no se tocó).
    leido: str = Field(default="", max_length=300)
    # El importe que se leyó antes de autocorregirlo (None si no se tocó).
    importe_leido: float | None = None

    @field_validator("codigo", "descripcion", "producto", "leido", mode="before")
    @classmethod
    def _recortar(cls, v, info):
        # Un OCR que leyó basura de corrido no hace fallar toda la lectura: se corta.
        return _cortar(v, cls.model_fields[info.field_name])


class AlicuotaIva(BaseModel):
    """Una fila del desglose de IVA: la alícuota (21, 10.5…), su base imponible y el IVA."""

    alicuota: float | None = None
    base: float | None = None
    importe: float | None = None


class Tributo(BaseModel):
    """Una fila de «otros tributos»: percepción, impuesto interno, tasa…"""

    codigo: str = Field(default="", max_length=10)
    descripcion: str = Field(default="", max_length=120)
    base: float | None = None
    importe: float | None = None

    @field_validator("codigo", "descripcion", mode="before")
    @classmethod
    def _recortar(cls, v, info):
        return _cortar(v, cls.model_fields[info.field_name])


class Hoja(BaseModel):
    nombre: str = Field(default="Hoja1", max_length=31)
    filas: list[list[str | float | int | None]] = Field(default_factory=list, max_length=MAX_FILAS)


class Verificacion(BaseModel):
    campo: str
    nivel: Nivel
    mensaje: str
    sugerencia: str | float | None = None
    # Para mostrarlo corto: un título, los números que importan y, si hay dudas, los valores para elegir.
    titulo: str = ""
    datos: list[str] = Field(default_factory=list)
    opciones: list[str] = Field(default_factory=list)


class Lectura(BaseModel):
    """Lo leído de un archivo. `tipo` comprobante: encabezado, items y totales;
    tabla: las hojas."""

    archivo: str = Field(default="", max_length=260)
    tipo: Literal["comprobante", "tabla"] = "tabla"
    # «ia»: imagen leída con Gemini (ver gemini.py).
    metodo: Literal["planilla", "pdf", "ocr", "ia"] = "planilla"
    # Qué leyó la imagen: «gemini», «paddleocr_local», «tesseract_local»,
    # con «qr+» adelante si se usó el QR de ARCA.
    motor: str = Field(default="", max_length=40)
    # Cuánto tardó la lectura de la imagen (para comparar motores).
    duracion_ms: int | None = None
    encabezado: dict[str, str] = Field(default_factory=dict)
    items: list[Item] = Field(default_factory=list, max_length=2000)
    totales: dict[str, float | None] = Field(default_factory=dict)
    # El IVA por alícuota; totales["iva"] es siempre la suma de los importes.
    iva_alicuotas: list[AlicuotaIva] = Field(default_factory=list, max_length=20)
    # Otros tributos uno por uno; con filas, totales["otros_tributos"] es su suma.
    tributos: list[Tributo] = Field(default_factory=list, max_length=30)
    hojas: list[Hoja] = Field(default_factory=list, max_length=60)
    texto: str = Field(default="", max_length=400_000)
    # Lo que cada pasada de OCR leyó distinto: campo -> valores leídos.
    dudas: dict[str, list[str]] = Field(default_factory=dict)
    avisos: list[str] = Field(default_factory=list)
    verificaciones: list[Verificacion] = Field(default_factory=list)


# --- Números y palabras con errores de OCR ---------------------------------------

_NUMERO = re.compile(r"-?\d[\d.,]*\d|\d")


def numeros(linea: str) -> list[float]:
    salida = []
    for m in _NUMERO.finditer(linea):
        v = importe(m.group(0))
        if v is not None:
            salida.append(v)
    return salida


def _ultimo_importe(linea: str) -> float | None:
    """El último número con decimales de la línea (el importe de la derecha)."""
    for m in reversed(list(_NUMERO.finditer(linea))):
        t = m.group(0)
        if re.search(r"[.,]\d{2}$", t):
            return importe(t)
    return None


def _palabras(linea: str) -> list[str]:
    return re.findall(r"[a-z]+", norm(linea))


def tiene(linea: str, frase: str, umbral: float = 0.75) -> bool:
    """`frase` está en la línea con las palabras en orden, tolerando letras mal
    leídas («IWPORTE TOTAL IVA», «WETO GRAVADO»)."""
    buscadas = frase.split()
    palabras = _palabras(linea)
    for i in range(len(palabras) - len(buscadas) + 1):
        if all(
            difflib.SequenceMatcher(None, palabras[i + j], b).ratio() >= umbral
            for j, b in enumerate(buscadas)
        ):
            return True
    return False


# --- Comprobante a partir del texto ------------------------------------------------


def _totales(lineas: list[str]) -> dict[str, float | None]:
    t: dict[str, float | None] = {}
    iva_por_alicuota = 0.0
    neto_sin_rotulo = None
    # Varios importes en una línea («Bruto: 100 Impuestos: 21 Total: 121»): cada uno con su rótulo.
    segmentos = [s for linea in lineas for s in re.split(r"(?<=\d)\s+(?=[A-Za-zÁÉÍÓÚáéíóú])", linea)]
    for linea in segmentos:
        v = _ultimo_importe(linea)
        if v is None:
            continue
        if tiene(linea, "no gravado"):
            t.setdefault("no_gravado", v)
        elif tiene(linea, "neto gravado") or tiene(linea, "subtotal gravado"):
            t.setdefault("neto_gravado", v)
        elif re.match(r"^\s*(?:importe\s+neto|bruto|subtotal)\b", norm(linea)):
            neto_sin_rotulo = neto_sin_rotulo if neto_sin_rotulo is not None else v
        elif re.match(r"^\s*importe\s+iva\b", norm(linea)):
            t.setdefault("iva", v)
        elif tiene(linea, "exento") or tiene(linea, "exentas"):
            t.setdefault("exento", v)
        elif re.search(r"\bimp(?:uestos?|\.)?\s*int(?:ernos?|\.)?\b", norm(linea)):
            t["impuestos_internos"] = round((t.get("impuestos_internos") or 0) + v, 2)
        elif re.search(r"\bperc\w*\.?\s*(?:de\s+)?(?:iibb|ii\.?\s*bb|ing(?:resos)?\.?\s*brutos)", norm(linea)):
            # Bs. As. (ARBA) aparte; las de otras jurisdicciones (CABA…) van a otros tributos.
            clave = "otros_tributos_det" if re.search(r"\b(caba|ciudad|agip|capital)\b", norm(linea)) else "percepcion_iibb"
            t[clave] = round((t.get(clave) or 0) + v, 2)
        elif tiene(linea, "otros tributos"):
            t.setdefault("otros_tributos", v)
        elif tiene(linea, "total iva"):
            t.setdefault("iva", v)
        elif re.search(r"\biva\s*\d{1,2}(?:[.,]\d{1,2})?\s*(?:%|:)", norm(linea)):
            iva_por_alicuota += v
        elif re.match(r"^\s*(?:importe\s+)?total\b(?!\s+(?:iva|otros|neto))", norm(linea)) or tiene(linea, "total a pagar"):
            t.setdefault("total", v)
    if "iva" not in t and iva_por_alicuota:
        t["iva"] = round(iva_por_alicuota, 2)
    # «Importe neto» / «Bruto» / «Subtotal»: el neto gravado si no hay otro rótulo de neto.
    if neto_sin_rotulo is not None and not any(k in t for k in ("neto_gravado", "no_gravado", "exento")):
        t["neto_gravado"] = neto_sin_rotulo
    return _separar_tributos(t)


def _separar_tributos(t: dict[str, float | None]) -> dict[str, float | None]:
    """Muchas facturas listan las percepciones y además las suman en «Otros tributos»:
    si es así, «Otros tributos» queda solo con lo que no se separó."""
    otras = t.pop("otros_tributos_det", None)
    separadas = round((t.get("impuestos_internos") or 0) + (t.get("percepcion_iibb") or 0), 2)
    if t.get("otros_tributos") is None:
        if otras:
            t["otros_tributos"] = otras
        return t
    if separadas and t["otros_tributos"] >= separadas - TOLERANCIA:
        restado = dict(t, otros_tributos=round(t["otros_tributos"] - separadas, 2))
        if _cierra(restado) or not _cierra(t):
            return restado
    return t


def _nombre_cerca(lineas: list[str], indice: int) -> str:
    """La razón social: la línea con letras más cercana arriba del CUIT."""
    for j in range(indice - 1, max(-1, indice - 4), -1):
        linea = lineas[j].strip()
        letras = sum(ch.isalpha() for ch in linea)
        if letras >= 4 and letras >= 0.6 * len(linea.replace(" ", "")) and ":" not in linea:
            # «$A» / «$RL» al final es «SA» / «SRL» mal leído.
            return re.sub(r"\$(?=(?:A|RL|AS)\b)", "S", re.sub(r"\s{2,}", " ", linea))[:200]
    return ""


def _encabezado(texto: str, lineas: list[str]) -> dict[str, str]:
    e: dict[str, str] = {}
    encontrados = cuits_del_texto(texto)
    # Dónde aparece cada CUIT (también los mal leídos, para ubicar al receptor).
    posiciones: list[tuple[int, str]] = []
    for i, linea in enumerate(lineas):
        n = norm(linea)
        # «C.U.I.T.», «CUIT» o mal leído («C.U.T.T.»). Ingresos Brutos suele
        # repetir el CUIT: no cuenta como otra parte.
        if re.search(r"\bc\W{0,3}u\W{0,3}[il1t]\W{0,3}[til1]", n) and not re.search(r"brut|iibb|\bing\b", n):
            m = re.search(r"(\d{2})[-\s.]?(\d{8})[-\s.]?(\d)\b", linea)
            if m:
                posiciones.append((i, "".join(m.groups())))
        # Rótulos en una columna y valores en otra (Xubio): la CUIT sola en su línea.
        elif m := re.fullmatch(r"\s*(\d{2})-(\d{8})-(\d)\s*", linea):
            if cuit_valido("".join(m.groups())):
                posiciones.append((i, "".join(m.groups())))
    vistos: list[str] = []
    for i, c in posiciones:
        if c in vistos:
            continue
        vistos.append(c)
        rol = "emisor" if len(vistos) == 1 else "receptor" if len(vistos) == 2 else None
        if rol:
            e[f"{rol}_cuit"] = c
            if nombre := _nombre_cerca(lineas, i):
                e[rol] = nombre
    if not posiciones and encontrados:
        e["emisor_cuit"] = encontrados[0]
        if len(encontrados) > 1:
            e["receptor_cuit"] = encontrados[1]
    # Con rótulo, la razón social manda: «Sr.(es):» / «Apellido y Nombre» / «Cliente:» es
    # el receptor; la primera otra «Razón Social:», el emisor.
    con_rotulo: dict[str, str] = {}
    for m in re.finditer(r"^(.*?)(?:raz[oó]n\s+social|se[ñn]or(?:\s*\(?es\)?)?|sr\.?\s*\(?es\)?)\s*:\s*(.+)$", texto, re.IGNORECASE | re.MULTILINE):
        nombre = re.sub(r"\s{2,}", " ", m.group(2)).strip()[:200]
        if sum(ch.isalpha() for ch in nombre) < 3:
            continue
        receptor = re.search(r"apellido|cliente|se[ñn]or|\bsr\b", (m.group(0)[: m.start(2) - m.start(0)]).lower())
        con_rotulo.setdefault("receptor" if receptor or "emisor" in con_rotulo else "emisor", nombre)
    e.update(con_rotulo)

    codigo = tipo_del_texto(texto)
    if codigo is None and (m := re.search(r"\b0?(\d{2,3})\s*[-~—]\s*\S*\s*(?:tique\s+)?factura", norm(texto))):
        codigo = int(m.group(1))
    if codigo is None and (m := re.search(r"t\w?que\s+factura\W+([abc])\b", norm(texto))):
        codigo = {"a": 81, "b": 82, "c": 111}[m.group(1)]
    if codigo in (1, 6, 11) and re.search(r"t\w?que\s+factura", norm(texto)):
        codigo = {1: 81, 6: 82, 11: 111}[codigo]
    if codigo in TIPOS:
        e["tipo"] = nombre_tipo(codigo)
    if m := re.search(r"\b(\d{4,5})\s*-\s*(\d{8})\b", texto) or re.search(
        r"punto\s+de\s+venta\W{0,3}(\d{1,5})\b.{0,25}?n(?:ro|ú?mero|[°º])\.?\s*:?\s*(\d{1,8})\b", texto, re.IGNORECASE
    ):
        e["numero"] = f"{int(m.group(1)):05d}-{int(m.group(2)):08d}"
    if m := re.search(r"Fecha[^\d\n]{0,25}(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})", texto, re.IGNORECASE) or re.search(r"\b(\d{2}/\d{2}/\d{4})\b", texto):
        iso = fecha_iso(m.group(1))
        e["fecha"] = f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso else m.group(1)
    return e


_ALICUOTA = re.compile(r"\(\s*(\d{1,2}[.,]\d{1,2})\s*\)|\b(\d{1,2}(?:[.,]\d{1,2})?)\s*%")
_CANT_PRECIO = re.compile(r"^\s*\*?\s*(\d+(?:[.,]\d+)?)\s*[-x×*]\s*(\d[\d.,]*)")


def _es_fin_del_detalle(linea: str) -> bool:
    return any(tiene(linea, f) for f in ("subtotal", "neto gravado", "no gravado", "total iva", "otros tributos", "ajuste")) or bool(
        re.match(r"^\s*(?:importe\s+(?:total|neto|iva)|total|bruto|iva\s*\d|observaciones\s*:)", norm(linea))
    )


def _inicio_del_detalle(lineas: list[str]) -> int:
    """La línea de títulos del detalle: «Descripción», «Producto», «Servicio»…, mejor
    si en la misma línea dice cantidad, precio o importe (así no se toma una leyenda
    del pie como «…los datos ingresados en el detalle…»)."""
    titulos = ("descripcion", "producto", "detalle", "servicio", "articulo", "concepto")
    columnas = ("cantidad", "precio", "importe", "total", "subtotal")
    for i, l in enumerate(lineas):
        if any(tiene(l, t) for t in titulos) and any(tiene(l, c) for c in columnas):
            return i
    return next((i for i, l in enumerate(lineas) if tiene(l, "descripcion") or tiene(l, "producto") or tiene(l, "detalle")), -1)


def _items(lineas: list[str]) -> list[Item]:
    """El detalle. Dos formas comunes:
    - tique: «(700027)ENTRANA» y abajo «1.0000-31000.0000 (21.00) 25619.83»;
    - factura: «Descripción  1,00  1.000,00  21%  1.000,00» en una sola línea.
    """
    items: list[Item] = []
    descripcion_previa = ""
    # La descripción partida en varias líneas (facturas en PDF): se junta.
    partida: list[str] = []
    inicio = _inicio_del_detalle(lineas)
    for linea in lineas[inicio + 1 :] if inicio >= 0 else lineas:
        if _es_fin_del_detalle(linea):
            if items:
                break
            continue
        limpia = linea.strip(" =~-—*'‘")
        letras = re.sub(r"\d|[.,()%$/\-\s\[\]:]", "", limpia)
        valor = _ultimo_importe(limpia)
        ali = _ALICUOTA.search(limpia)
        if valor is not None and ali and len(letras) <= 3:
            # Línea solo de números: es la descripción de arriba.
            alicuota = importe(ali.group(1) or ali.group(2))
            cant = precio = None
            if m := _CANT_PRECIO.match(limpia):
                # Tique: «1.0000-31000.0000 (21.00) 25619.83».
                cant, precio = importe(m.group(1)), importe(m.group(2))
                codigo, desc = _separar_codigo(descripcion_previa)
            else:
                # Factura: «1.00 2,479,338.84 0.00 2,479,338.84 21.00% 3,000,000.00»:
                # cantidad, precio, (descuento), importe sin IVA, alícuota, con IVA.
                antes = [n for n in (importe(x.group(0)) for x in _NUMERO.finditer(limpia[: ali.start()])) if n is not None]
                if len(antes) >= 2:
                    valor = antes[-1]
                    cant, precio = (antes[0], antes[1]) if len(antes) >= 3 else (None, None)
                codigo, desc = _separar_codigo(" ".join(partida) or descripcion_previa)
            items.append(Item(codigo=codigo, descripcion=desc, cantidad=cant, precio=precio, alicuota=alicuota, importe=valor))
            descripcion_previa, partida = "", []
        elif valor is not None and len(letras) >= 3 and re.match(r"^\W*[A-Za-zÁÉÍÓÚÑáéíóúñ(]", limpia) and len(numeros(limpia)) >= 2:
            # Descripción y números en la misma línea.
            texto = re.split(r"\s+\d", limpia, maxsplit=1)[0]
            nums = [importe(m.group(0)) for m in _NUMERO.finditer(limpia[len(texto):])]
            nums = [n for n in nums if n is not None]
            alicuota = importe(ali.group(1) or ali.group(2)) if ali else None
            if ali:
                antes = [importe(m.group(0)) for m in _NUMERO.finditer(limpia[len(texto):ali.start()])]
                antes = [n for n in antes if n is not None]
                valor = antes[-1] if antes else valor
            codigo, desc = _separar_codigo(" ".join([*partida, texto]))
            items.append(Item(codigo=codigo, descripcion=desc, cantidad=nums[0] if len(nums) >= 3 else None,
                              precio=nums[1] if len(nums) >= 3 else None, alicuota=alicuota, importe=valor))
            descripcion_previa, partida = "", []
        elif len(letras) >= 4 and not re.match(r"^\s*itc\b", norm(limpia)):
            descripcion_previa = limpia
            partida = [*partida, limpia][-8:]
    return items


def _separar_codigo(texto: str) -> tuple[str, str]:
    texto = re.sub(r"\s{2,}", " ", texto.strip())
    if m := re.match(r"^\(?\s*(\w{2,12})\s*\)\s*(.+)$", texto):
        return m.group(1), m.group(2).strip()
    # «0003 Servicios profesionales…»: el código numérico adelante.
    if m := re.match(r"^(\d{2,12})\s+([A-Za-zÁÉÍÓÚÑáéíóúñ].+)$", texto):
        return m.group(1), m.group(2).strip()
    return "", texto


def comprobante_de_texto(texto: str) -> tuple[dict[str, str], list[Item], dict[str, float | None]]:
    lineas = [l for l in texto.splitlines() if l.strip()]
    return _encabezado(texto, lineas), _items(lineas), _totales(lineas)


def parece_comprobante(encabezado: dict, totales: dict) -> bool:
    return bool(totales.get("total") is not None or encabezado.get("numero") or encabezado.get("emisor_cuit"))


# --- Varias pasadas: elegir y marcar dudas ------------------------------------------


def _cierra(t: dict[str, float | None]) -> bool:
    if t.get("total") is None:
        return False
    suma = sum(t.get(k) or 0 for k in TOTALES if k != "total")
    return abs(suma - t["total"]) <= TOLERANCIA


def _combinar_totales(lecturas: list[dict[str, float | None]]) -> dict[str, float | None]:
    """Por cada importe, el más votado; si así no cierra, la combinación de
    valores leídos que sí cierra (la que más votos junta)."""
    votos = {k: Counter(l[k] for l in lecturas if l.get(k) is not None) for k in TOTALES}
    elegido = {k: (votos[k].most_common(1)[0][0] if votos[k] else None) for k in TOTALES}
    if _cierra(elegido):
        return elegido
    opciones = [list(votos[k]) or [None] for k in TOTALES]
    mejor, mejor_votos = None, -1
    for combo in itertools.islice(itertools.product(*opciones), 5000):
        t = dict(zip(TOTALES, combo))
        if _cierra(t):
            n = sum(votos[k][v] for k, v in t.items() if v is not None)
            if n > mejor_votos:
                mejor, mejor_votos = t, n
    return mejor or elegido


def _combinar(pasadas: list[str]) -> tuple[str, dict[str, str], list[Item], dict[str, float | None], dict[str, list[str]]]:
    leidas = [comprobante_de_texto(t) for t in pasadas]
    totales = _combinar_totales([l[2] for l in leidas])
    netos = {l[2]["neto_gravado"] for l in leidas if l[2].get("neto_gravado") is not None}
    sumas = [sum(x.importe or 0 for x in l[1]) for l in leidas if l[1]]
    if not _cierra(totales) and len(netos) > 1 and sumas:
        # Sin cuentas que cierren, el neto leído más parecido a lo que suma el detalle
        # («3.974,75» / «83.974,75» con ítems que suman 83.954,68: el segundo).
        totales["neto_gravado"] = min(netos, key=lambda n: min(abs(n - s) for s in sumas))
    encabezado: dict[str, str] = {}
    dudas: dict[str, list[str]] = {}
    for campo in ETIQUETAS_ENCABEZADO:
        valores = [l[0][campo] for l in leidas if l[0].get(campo)]
        if not valores:
            continue
        if campo.endswith("_cuit"):
            # Primero el que tiene el dígito verificador bien.
            validos = [v for v in valores if cuit_valido(v)]
            encabezado[campo] = Counter(validos or valores).most_common(1)[0][0]
        else:
            encabezado[campo] = Counter(valores).most_common(1)[0][0]
        if len(set(valores)) > 1:
            dudas[campo] = sorted(set(valores))
    for campo in TOTALES:
        valores = {l[2][campo] for l in leidas if l[2].get(campo) is not None}
        if len(valores) > 1:
            dudas[campo] = [_fmt(v) for v in sorted(valores)]
    # El detalle de la pasada que mejor suma el neto (o la que más ítems leyó).
    neto = totales.get("neto_gravado")

    def puntaje(i: int) -> tuple:
        items = leidas[i][1]
        suma = sum(x.importe or 0 for x in items)
        return (abs(suma - neto) if neto is not None and items else float("inf"), -len(items))

    mejor = min(range(len(leidas)), key=puntaje)
    return pasadas[mejor], encabezado, leidas[mejor][1], totales, dudas


# --- Leer cada formato -------------------------------------------------------------


def _desde_texto(lectura: Lectura, pasadas: list[str]) -> Lectura:
    texto, encabezado, items, totales, dudas = _combinar(pasadas)
    lectura.texto = texto
    if parece_comprobante(encabezado, totales):
        lectura.tipo = "comprobante"
        lectura.encabezado, lectura.items, lectura.dudas = encabezado, items, dudas
        lectura.totales = {k: totales.get(k) for k in TOTALES}
    else:
        lectura.tipo = "tabla"
        lectura.hojas = [hoja_de_texto(texto)]
    return lectura


def hoja_de_texto(texto: str, nombre: str = "Texto") -> Hoja:
    """Cada línea una fila; las columnas, lo separado por 2+ espacios o tabs."""
    filas = []
    for linea in texto.splitlines():
        if not linea.strip():
            continue
        filas.append([_celda(c) for c in re.split(r"\t|\s{2,}", linea.strip())])
    return Hoja(nombre=nombre, filas=filas[:MAX_FILAS])


def _imagen_a_lectura(nombre: str, imagen, lectura: Lectura, pasadas_extra: bool = True) -> Lectura:
    """La imagen leída con Tesseract (tesseract.extract_with_tesseract)."""
    from .ocr import OCRNoDisponible
    from .tesseract import extract_with_tesseract

    lectura.metodo = "ocr"
    try:
        datos = extract_with_tesseract(imagen, pasadas_extra=pasadas_extra)
    except OCRNoDisponible as exc:
        lectura.avisos.append(str(exc))
        return lectura
    except Exception as exc:
        logger.warning("OCR falló en %s: %s", nombre, exc)
        lectura.avisos.append(f"Tesseract no pudo leer el texto de la imagen ({str(exc)[:160] or type(exc).__name__}).")
        return lectura
    return _desde_json(lectura, datos, "ocr")


def _numero(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return importe(v)


def _entero(v) -> int | None:
    digitos = re.sub(r"\D", "", str(v if v is not None else ""))
    return int(digitos) if digitos else None


def _codigo_de_tipo(valor) -> int | None:
    """«81 - Tique Factura A», 81 o «Tique Factura A» → 81."""
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return int(valor)
    texto = str(valor or "").strip()
    if m := re.match(r"\D{0,3}(\d{1,3})\b", texto):
        return int(m.group(1))
    if codigo := tipo_del_texto(texto):
        return codigo
    if m := re.search(r"t\w?que\s+factura\W+([abc])\b", norm(texto)):
        return {"a": 81, "b": 82, "c": 111}[m.group(1)]
    return None


def _primero(d: dict, *claves):
    """El primer valor presente entre las claves (el esquema nuevo y el viejo)."""
    for k in claves:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def _desde_json(lectura: Lectura, datos: dict, metodo: Literal["ia", "ocr"]) -> Lectura:
    """El JSON fiscal (gemini.PROMPT; PaddleOCR y Tesseract arman el mismo, ver
    tesseract.json_de_texto) a una Lectura: así todas pasan por las mismas verificaciones."""
    lectura.metodo = metodo
    validaciones = datos.get("validaciones") if isinstance(datos.get("validaciones"), dict) else {}
    lectura.motor = str(validaciones.get("motor_utilizado") or ("gemini" if metodo == "ia" else "tesseract_local"))[:40]
    lectura.texto = str(datos.get("texto") or "")[:400_000]
    if isinstance(datos.get("dudas"), dict):
        lectura.dudas = {str(k): [str(x) for x in v][:5] for k, v in datos["dudas"].items() if isinstance(v, list)}
    if str(datos.get("tipo") or "").lower() == "tabla" or not isinstance(datos.get("encabezado"), dict):
        filas = [[_celda(c) for c in f] for f in (datos.get("filas") or []) if isinstance(f, list)]
        lectura.tipo = "tabla"
        lectura.hojas = [Hoja(nombre="Imagen", filas=filas[:MAX_FILAS])] if filas else [hoja_de_texto(lectura.texto)]
        return lectura

    g = datos.get("encabezado") or {}
    e: dict[str, str] = {}
    for rol in ("emisor", "receptor"):
        if nombre := str(_primero(g, f"{rol}_razon_social", rol) or "").strip():
            e[rol] = nombre[:200]
        if cuit := re.sub(r"\D", "", str(g.get(f"{rol}_cuit") or "")):
            # El 0 tachado de las ticketeadoras se lee como 8 (IA y OCR).
            if len(cuit) == 11 and not cuit_valido(cuit) and len(arreglos := cuits_cambiando_0_y_8(cuit)) == 1:
                lectura.avisos.append(
                    f"{ETIQUETAS_ENCABEZADO[rol + '_cuit']}: se leyó {cuit}, que no pasa el dígito verificador; "
                    f"cambiando 0 y 8 da {arreglos[0]}, que sí. Compará con la imagen."
                )
                cuit = arreglos[0]
            e[f"{rol}_cuit"] = cuit
    if (codigo := _codigo_de_tipo(_primero(g, "tipo_comprobante", "tipo_codigo"))) in TIPOS:
        e["tipo"] = nombre_tipo(codigo)
    pv = _entero(_primero(g, "punto_venta", "punto_de_venta"))
    numero = str(_primero(g, "numero_comprobante", "numero") or "")
    if m := re.fullmatch(r"\s*(\d{1,5})\s*-\s*(\d{1,8})\s*", numero):  # «00018-00076897» en un solo campo
        pv, numero = pv or int(m.group(1)), m.group(2)
    if (nro := _entero(numero)) is not None:
        e["numero"] = f"{pv or 0:05d}-{nro:08d}"
    if f := str(g.get("fecha") or "").strip():
        iso = fecha_iso(f)
        e["fecha"] = f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso else f
    lectura.tipo = "comprobante"
    lectura.encabezado = e
    lectura.items = [
        Item(
            codigo=str(x.get("codigo") or "")[:40],
            descripcion=str(x.get("descripcion") or "")[:300],
            cantidad=_numero(x.get("cantidad")),
            precio=_numero(_primero(x, "precio_unitario", "precio")),
            alicuota=_numero(_primero(x, "alicuota_iva", "alicuota")),
            importe=_numero(x.get("importe")),
            impuestos_internos=_numero(x.get("impuestos_internos")),
            producto=str(x.get("producto") or "")[:60],
        )
        for x in (datos.get("items") or [])[:2000]
        if isinstance(x, dict)
    ]
    t = datos.get("importes") if isinstance(datos.get("importes"), dict) else datos.get("totales") or {}
    lectura.totales = {k: _numero(t.get(k)) for k in TOTALES}
    lectura.iva_alicuotas = [
        AlicuotaIva(
            alicuota=_numero(x.get("alicuota")),
            base=_numero(_primero(x, "base_imponible", "base")),
            importe=_numero(_primero(x, "importe_iva", "importe")),
        )
        for x in (t.get("desglose_iva") or [])[:20]
        if isinstance(x, dict) and any(x.get(k) not in (None, "", 0) for k in ("base_imponible", "importe_iva", "base", "importe"))
    ]
    lectura.tributos = [_tributo(x) for x in (t.get("desglose_tributos") or [])[:30] if isinstance(x, dict)]
    lectura.tributos = [x for x in lectura.tributos if x.descripcion or x.importe]
    for advertencia in (validaciones.get("advertencias") or [])[:5]:
        if not isinstance(advertencia, str) or not advertencia.strip():
            continue
        if metodo == "ia":
            lectura.avisos.append(f"Gemini: {advertencia.strip()[:300]}")
        elif "cambiando 0 y 8" in advertencia:
            # Lo demás que no cierra ya lo dice la verificación.
            lectura.avisos.append(f"{advertencia.strip()[:300]} Compará con la imagen.")
    return lectura


def _tributo(x: dict) -> Tributo:
    codigo = str(x.get("codigo") or "").strip()
    descripcion = str(x.get("descripcion") or "").strip()
    # «07 - PERCEPCION IIBB BS AS» todo junto en la descripción.
    if not codigo and (m := re.match(r"^(\d{1,3})\s*[-–]\s*(.+)$", descripcion)):
        codigo, descripcion = m.group(1), m.group(2).strip()
    return Tributo(
        codigo=codigo[:10],
        descripcion=descripcion[:120],
        base=_numero(_primero(x, "base_imponible", "base")),
        importe=_numero(x.get("importe")),
    )


_ES_IMP_INTERNO = re.compile(r"\bimp(?:uestos?|\.)?\s*int(?:ernos?|\.)?\b")
_ES_PERC_IIBB = re.compile(r"\bperc\w*\.?.*\b(?:i[il1t]bb|ii\.?\s*bb|ing(?:resos)?\.?\s*brutos|arba)\b")
_OTRA_JURISDICCION = re.compile(r"\b(?:caba|ciudad|agip|capital)\b")


def grupo_tributo(x: Tributo) -> str:
    """A qué total va una fila de otros tributos: «impuestos_internos», «percepcion_iibb»
    (Bs. As.) u «otros_tributos» (el resto)."""
    d = norm(f"{x.descripcion}")
    if _ES_IMP_INTERNO.search(d):
        return "impuestos_internos"
    if _ES_PERC_IIBB.search(d) and not _OTRA_JURISDICCION.search(d):
        return "percepcion_iibb"
    return "otros_tributos"


def totales_efectivos(lectura: Lectura) -> dict[str, float | None]:
    """Los totales con el IVA igual a la suma de sus filas y, si hay filas de otros
    tributos, cada fila sumada una sola vez en su total (impuestos internos,
    percepción IIBB Bs. As. u otros tributos): así no se cuentan dos veces."""
    t = dict(lectura.totales)
    if lectura.iva_alicuotas:
        t["iva"] = round(sum(x.importe or 0 for x in lectura.iva_alicuotas), 2)
    if lectura.tributos:
        grupos: dict[str, float] = {}
        for x in lectura.tributos:
            g = grupo_tributo(x)
            grupos[g] = round(grupos.get(g, 0) + (x.importe or 0), 2)
        for g in ("impuestos_internos", "percepcion_iibb"):
            if g in grupos:
                t[g] = grupos[g]
        t["otros_tributos"] = grupos.get("otros_tributos", 0.0) or None
    return t


def completar_desglose(lectura: Lectura) -> Lectura:
    """Sin desglose (OCR, PDF): una fila con el IVA leído, en la alícuota que da
    con el neto si da alguna. Con desglose, el IVA total es la suma de las filas."""
    if lectura.tipo != "comprobante":
        return lectura
    lectura.totales = t = totales_efectivos(lectura)
    if not lectura.iva_alicuotas and t.get("iva"):
        neto = t.get("neto_gravado")
        lectura.iva_alicuotas = [AlicuotaIva(alicuota=_alicuota_de(neto or 0, t["iva"]), base=neto, importe=t["iva"])]
    return lectura


Modo = Literal["auto", "gemini", "paddle", "tesseract"]
MOTORES: dict[str, Modo] = {
    "auto": "auto",
    "gemini": "gemini", "gemini_only": "gemini", "ia": "gemini",
    "paddle": "paddle", "paddleocr": "paddle",
    "tesseract": "tesseract", "tesseract_only": "tesseract",
}


def modo_ocr(pedido: str | None = None) -> Modo:
    """Quién lee las imágenes: lo pedido en la petición; si no, FORCE_TESSERACT=true,
    u OCR_MODE (variable de entorno o .env): auto, gemini_only, paddle o tesseract_only
    (también sin el «_only»)."""
    from .entorno import valor

    if pedido and pedido.lower() in MOTORES:
        return MOTORES[pedido.lower()]
    if valor("FORCE_TESSERACT").lower() in ("1", "true", "si", "sí", "yes"):
        return "tesseract"
    return MOTORES.get(valor("OCR_MODE").lower(), "auto")


def es_captura_limpia(imagen, contenido: bytes = b"") -> bool:
    """Una captura de pantalla (fondo blanco puro, pocos grises), no una foto: ahí
    el OCR local lee bien en menos de un segundo y no hace falta el modelo de visión."""
    exif = getattr(imagen, "getexif", lambda: {})()
    if exif and (exif.get(0x010F) or exif.get(0x0110)):  # marca / modelo de cámara
        return False
    chica = imagen.convert("L")
    chica.thumbnail((500, 500))
    histograma = chica.histogram()
    total = sum(histograma) or 1
    blanco = sum(histograma[248:]) / total
    grises = sum(histograma[70:200]) / total
    return blanco >= 0.55 and grises <= 0.10


def _cierra_bien(lectura: Lectura) -> bool:
    """La lectura no necesita otra: el total cierra y el CUIT del emisor es válido."""
    if lectura.tipo != "comprobante":
        return False
    return _cierra(totales_efectivos(lectura)) and cuit_valido(lectura.encabezado.get("emisor_cuit", ""))


def _aplicar_qr(lectura: Lectura, qr: dict) -> Lectura:
    """Los datos del QR de ARCA son exactos: mandan sobre lo leído (sin el CAE)."""
    from .modelo import fecha as fecha_iso_qr

    del_qr: dict[str, str] = {}
    if c := re.sub(r"\D", "", str(qr.get("cuit") or "")):
        del_qr["emisor_cuit"] = c
    if iso := fecha_iso_qr(qr.get("fecha")):
        del_qr["fecha"] = f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}"
    if (codigo := _entero(qr.get("tipoCmp"))) in TIPOS:
        del_qr["tipo"] = nombre_tipo(codigo)
    if (nro := _entero(qr.get("nroCmp"))) is not None:
        del_qr["numero"] = f"{_entero(qr.get('ptoVta')) or 0:05d}-{nro:08d}"
    if _entero(qr.get("tipoDocRec")) == 80 and (rec := re.sub(r"\D", "", str(qr.get("nroDocRec") or ""))):
        del_qr["receptor_cuit"] = rec
    total = _numero(qr.get("importe"))
    if lectura.tipo != "comprobante":
        lectura.tipo, lectura.hojas = "comprobante", []
        lectura.totales = {k: None for k in TOTALES}
    cambios = []
    for campo, valor in del_qr.items():
        leido = lectura.encabezado.get(campo)
        if leido and leido != valor:
            cambios.append(f"{ETIQUETAS_ENCABEZADO[campo]} {leido} → {valor}")
        lectura.encabezado[campo] = valor
        lectura.dudas.pop(campo, None)
    if total is not None:
        leido = lectura.totales.get("total")
        if leido is not None and abs(leido - total) > TOLERANCIA:
            cambios.append(f"Total {_fmt(leido)} → {_fmt(total)}")
        lectura.totales["total"] = total
        lectura.dudas.pop("total", None)
    lectura.motor = f"qr+{lectura.motor}" if lectura.motor else "qr"
    aviso = "Se leyó el QR de ARCA: CUIT del emisor, fecha, tipo, número y total salen de ahí (son exactos)."
    if cambios:
        aviso += " Corrigió lo leído: " + "; ".join(cambios) + "."
    lectura.avisos.append(aviso)
    return lectura


# --- Producto sugerido por ítem (cuando no lo dio la IA) ------------------------------

# Los productos/servicios cargados en Xubio: lo que va en PRODUCTOSERVICIO del Excel.
PRODUCTOS_XUBIO = (
    "Campañas Publicitarias", "Campañas Publicitarias 10.5%", "COMBUSTIBLE", "Combustible al 27%", "Combustible exento",
    "Equipos de Computación", "Equipos de Computacion 10,5", "GASTOS BANCARIOS", "GASTOS BANCARIOS 10.5",
    "Gastos de asesoramiento", "Gastos de capacitación", "Gastos de certificaciones y legalizaciones",
    "Gastos de certificaciones y legalizaciones 21%", "Gastos de Libreria", "Gastos de limpieza, libreria y refrigerio",
    "Gastos de Mantenimiento", "Gastos de movilidad y viáticos", "Gastos de Organización", "Gastos de Seguridad",
    "GASTOS DE SEGUROS", "Gastos de sistemas", "Gastos de sistemas (Exento)", "GASTOS VARIOS", "Gastos Varios al 10,5%",
    "Gastos varios de admnistracion exento", "Herramientas", "HONORARIOS PROFESIONALES", "Honorarios profesionales (exento)",
    "IMPUESTO A LOS CR BANCARIOS", "IMPUESTO A LOS DB BANCARIOS", "Impuesto país", "Instalaciones", "INSTALACIONES 10,50%",
    "Intereses financieros", "Luz, Gas, Telefono e internet 21%", "Luz, Gas, Telefono e internet 27%",
    "Luz, Gas, Telefono e internet exento", "Mejoras en inmuebles de terceros", "Muebles y Utiles", "Muebles y Utiles 10,5%",
    "Muebles y útiles exento", "Pagina web, redes, facebook, Instagram, etc", "Préstamo BBVA", "Producto al 10.5%",
    "Producto al 21% (PROD21)", "Producto Exento (Compra de Servicios)", "Publicidad", "ROPA DE TRABAJO",
    "Servicio de consultoria y procesamiento de datos", "Servidores - Emails", "SOFTWARE", "Software exento",
)

# Categorías para reconocer el ítem (también las que propone la IA); después se pasan
# al producto de Xubio que corresponde según la alícuota (`producto_xubio`).
PRODUCTOS = ("Mercaderías", "Servicios", "Honorarios", "Combustible", "Refrigerio", "Gastos de limpieza", "Alquiler")
_REGLAS_PRODUCTO = (
    ("Combustible", r"nafta|gas\s?oil|diesel|combustible|infinia|v.?power|\bgnc\b|lubric|\bquantium\b"),
    ("Gastos de limpieza", r"lavandina|detergente|limpi|desinfect|papel\s+higien|rollo\s+cocina|trapo|escoba|jabon|lysoform|cif\b|libreria|resma|birome|lapicera|cuaderno"),
    ("Bancarios", r"comision\s+banc|mantenimiento\s+de\s+cuenta|gastos?\s+banc"),
    ("Intereses", r"interes"),
    ("Seguros", r"seguro|poliza"),
    ("Ropa", r"ropa\s+de\s+trabajo|uniforme|botin|guante|mameluco"),
    ("Herramientas", r"herramienta|taladro|destornillador|amoladora|martillo|pinza|llave\s+(?:de|inglesa)"),
    ("Computacion", r"computadora|notebook|\bpc\b|monitor|impresora|teclado|mouse|disco\s+(?:rigido|ssd)|memoria\s+ram"),
    ("Muebles", r"mueble|silla|escritorio|estanteria|armario|mesa\s+de\s+trabajo"),
    ("Movilidad", r"\btaxi\b|\buber\b|\bcabify\b|remis|peaje|estacionamiento|viatico|pasaje"),
    ("Servidores", r"hosting|servidor|dominio|\bemails?\b|correo\s+electronico"),
    ("Software", r"software|licencia"),
    ("Web", r"pagina\s+web|redes\s+sociales|facebook|instagram|community"),
    ("Publicidad", r"publicidad|campa[nñ]a|google\s+ads|\bmeta\s+ads"),
    ("Capacitacion", r"capacitacion|curso|seminario|taller\s+de"),
    ("Certificaciones", r"certificacion|legalizacion|apostilla"),
    ("Servicios publicos", r"\bluz\b|energia\s+electrica|edenor|edesur|\bgas\s+natural|metrogas|naturgy|telefon|internet|fibertel|movistar|telecom"),
    ("Mantenimiento", r"mantenimiento|reparacion|arreglo"),
    ("Alquiler", r"alquiler|locacion|expensas"),
    ("Honorarios", r"honorario"),
    ("Asesoramiento", r"asesor|consultor|auditoria|\bfee\b"),
    ("Servicios", r"servicio|abono|suscrip|flete|envio"),
    ("Refrigerio", r"\bagua\b|gaseosa|\bcafe\b|cortado|medialuna|sandwich|\bmenu\b|almuerzo|cena|empanada|pizza|cerveza|\bvino\b|"
                   r"\bpapas?\b|pure|carne|entra[nñ]a|salm|salh|pollo|ensalada|postre|helado|hamburg|coleslaw|bife|porcion|"
                   r"bebida|jugo|cubierto|guarnicion|milanesa|tostado|factura[s]?\s+dulce|desayuno|merienda|\bmesa\b"),
)


def producto_xubio(categoria: str, alicuota: float | None) -> str:
    """El producto/servicio de Xubio para la categoría, con la variante de la alícuota
    del ítem (10,5 %, 27 % o exento) cuando Xubio la tiene."""
    a = alicuota
    exento, diez, veintisiete = a == 0, a == 10.5, a == 27

    def segun(general: str, con_10=None, con_27=None, sin_iva=None) -> str:
        return (diez and con_10) or (veintisiete and con_27) or (exento and sin_iva) or general

    return {
        "Combustible": segun("COMBUSTIBLE", con_27="Combustible al 27%", sin_iva="Combustible exento"),
        "Gastos de limpieza": "Gastos de limpieza, libreria y refrigerio",
        "Refrigerio": "Gastos de limpieza, libreria y refrigerio",
        "Bancarios": segun("GASTOS BANCARIOS", con_10="GASTOS BANCARIOS 10.5"),
        "Intereses": "Intereses financieros",
        "Seguros": "GASTOS DE SEGUROS",
        "Ropa": "ROPA DE TRABAJO",
        "Herramientas": "Herramientas",
        "Computacion": segun("Equipos de Computación", con_10="Equipos de Computacion 10,5"),
        "Muebles": segun("Muebles y Utiles", con_10="Muebles y Utiles 10,5%", sin_iva="Muebles y útiles exento"),
        "Movilidad": "Gastos de movilidad y viáticos",
        "Servidores": "Servidores - Emails",
        "Software": segun("SOFTWARE", sin_iva="Software exento"),
        "Web": "Pagina web, redes, facebook, Instagram, etc",
        "Publicidad": segun("Publicidad", con_10="Campañas Publicitarias 10.5%"),
        "Capacitacion": "Gastos de capacitación",
        "Certificaciones": "Gastos de certificaciones y legalizaciones 21%" if a == 21 else "Gastos de certificaciones y legalizaciones",
        "Servicios publicos": segun("Luz, Gas, Telefono e internet 21%", con_27="Luz, Gas, Telefono e internet 27%", sin_iva="Luz, Gas, Telefono e internet exento"),
        "Mantenimiento": "Gastos de Mantenimiento",
        "Honorarios": segun("HONORARIOS PROFESIONALES", sin_iva="Honorarios profesionales (exento)"),
        "Asesoramiento": "Gastos de asesoramiento",
        "Servicios": segun("GASTOS VARIOS", con_10="Gastos Varios al 10,5%", sin_iva="Gastos varios de admnistracion exento"),
        "Alquiler": segun("GASTOS VARIOS", con_10="Gastos Varios al 10,5%", sin_iva="Gastos varios de admnistracion exento"),
        "Mercaderías": segun("Producto al 21% (PROD21)", con_10="Producto al 10.5%", sin_iva="Producto Exento (Compra de Servicios)"),
    }.get(categoria, "")

# --- Autocorrección de descripciones mal leídas -------------------------------------

# Palabras de comercios (comida, bebidas, combustible, limpieza, servicios) para corregir
# lo que el OCR lee mal («NWINERAL» -> «MINERAL», «VATER» -> «WATER»).
_VOCABULARIO = set("""
agua mineral gasificada saborizada water smart sprite fanta pepsi coca cola schweppes tonica soda gaseosa levite villavicencio
cerveza quilmes stella artois corona heineken patagonia brahma vino tinto blanco rosado malbec cabernet syrah chardonnay espumante
jugo naranja limon pomelo manzana durazno licuado limonada cafe cortado lagrima capuchino americano expreso te leche chocolate
medialuna medialunas tostado tostados sandwich sandwiches jamon queso crudo cocido pollo carne vacio entrana bife chorizo morcilla
asado matambre milanesa napolitana suprema salmon merluza pescado langostinos rabas papas papa fritas pure boniato batata calabaza
zapallo ensalada mixta verde rucula tomate lechuga cebolla huevo huevos hamburguesa hamburguesas doble triple simple cheddar bacon
panceta pan porcion porciones guarnicion postre helado flan tiramisu brownie torta empanada empanadas pizza muzzarella fugazzeta
coleslaw salsa criolla chimichurri provoleta parrillada menu ejecutivo almuerzo cena desayuno merienda cubierto propina
nafta super premium infinia gasoil diesel euro lubricante aceite filtro lavado
lavandina detergente jabon liquido polvo papel higienico rollo cocina servilletas trapo rejilla escoba esponja desinfectante limpiador
servicio servicios honorarios abono mensual alquiler expensas mantenimiento asesoramiento contable consultoria estudio acceso
internet telefonia hosting licencia flete envio
""".split())
_CONFUSIONES_LETRAS = (("NW", "M"), ("RN", "M"), ("VV", "W"), ("V", "W"), ("W", "V"), ("0", "O"), ("1", "I"), ("5", "S"), ("8", "B"))


def _corregir_palabra(palabra: str) -> str:
    base = norm(palabra)
    if len(base) < 4 or base in _VOCABULARIO or not re.search(r"[a-z]", base):
        return palabra
    candidatas = {base}
    for mal, bien in _CONFUSIONES_LETRAS:
        candidatas.add(norm(palabra.upper().replace(mal, bien)))
    mejor, puntaje = "", 0.0
    for c in candidatas:
        if c in _VOCABULARIO:
            mejor, puntaje = c, 1.0
            break
        # Parecida y con la misma primera letra (si no, «ACONT» terminaba en «BACON»).
        for m in difflib.get_close_matches(c, [v for v in _VOCABULARIO if v[0] == c[0]], n=1, cutoff=0.8):
            # Una es la otra con algo más al final («contables» / «contable», «servicio» /
            # «servicios»): es la misma palabra en plural o singular, no algo mal leído.
            if c.startswith(m) or m.startswith(c):
                continue
            p = difflib.SequenceMatcher(None, c, m).ratio()
            if p > puntaje:
                mejor, puntaje = m, p
    if not mejor or mejor == base:
        return palabra
    if palabra.isupper():
        return mejor.upper()
    return mejor.capitalize() if palabra[:1].isupper() else mejor


def corregir_descripcion(texto: str) -> str:
    """La descripción con las palabras mal leídas corregidas y las unidades arregladas
    («591NL» -> «591ML», «500 6R» -> «500 GR»)."""
    t = re.sub(r"(\d)\s*[NnHh][Ll]\b", lambda m: m.group(1) + ("ML" if m.group(0)[-1].isupper() else "ml"), texto)
    t = re.sub(r"(\d)\s*6[Rr]\b", r"\1GR", t)
    t = re.sub(r"(\d)\s*[Kk]6\b", r"\1KG", t)
    return re.sub(r"[A-Za-zÁÉÍÓÚÑáéíóúñ0-9]+", lambda m: _corregir_palabra(m.group(0)), t)


def corregir_descripciones(items: list[Item]) -> int:
    """Autocorrige cada ítem y guarda lo que decía en «leido». Devuelve cuántos cambió."""
    cambiados = 0
    for item in items:
        if item.leido:
            continue
        nueva = corregir_descripcion(item.descripcion)
        if nueva != item.descripcion:
            item.leido, item.descripcion = item.descripcion, nueva
            cambiados += 1
    return cambiados


def _tolerancia_items(n: int) -> float:
    return max(TOLERANCIA, 0.01 * n / 2)


def corregir_importes_items(items: list[Item], t: dict[str, float | None]) -> int:
    """Si el detalle no suma el neto (o el total), busca UN importe mal leído que lo
    arregle: el de un ítem igual a otro (misma cantidad, precio y alícuota) o un dígito
    que el lector confunde (3 y 9, 6 y 8…). Si hay una sola explicación, lo corrige y
    guarda lo leído en «importe_leido». Devuelve cuántos cambió (0 o 1)."""
    if len(items) < 2 or any(i.importe is None for i in items):
        return 0
    suma = round(sum(i.importe for i in items), 2)
    tol = _tolerancia_items(len(items))
    objetivos = [t.get(k) for k in ("neto_gravado", "total") if t.get(k) is not None]
    if not objetivos or any(abs(o - suma) <= tol for o in objetivos):
        return 0
    candidatos: dict[tuple[int, float], tuple[int, float]] = {}  # (ítem, valor) -> (prioridad, diferencia)
    for n, a in enumerate(items):
        gemelos = [b.importe for m, b in enumerate(items) if m != n and a.precio
                   and (a.cantidad, a.precio, a.alicuota) == (b.cantidad, b.precio, b.alicuota)
                   and b.importe != a.importe and abs(b.importe - a.importe) < 0.02 * max(a.importe, b.importe)]
        opciones = [(0, g) for g in gemelos] + [(1, v) for v in _variantes_importe(a.importe) if v > 0 and v != a.importe]
        for prioridad, valor in opciones:
            nueva = round(suma - a.importe + valor, 2)
            dif = min(abs(o - nueva) for o in objetivos)
            if dif <= tol:
                previo = candidatos.get((n, valor))
                if previo is None or (prioridad, dif) < previo:
                    candidatos[(n, valor)] = (prioridad, dif)
    if not candidatos:
        return 0
    orden = sorted(candidatos.items(), key=lambda x: x[1])
    mejor = orden[0]
    # Dos arreglos igual de buenos en ítems distintos: no se adivina.
    if len(orden) > 1 and orden[1][1] == mejor[1] and orden[1][0][0] != mejor[0][0]:
        return 0
    (n, valor), _ = mejor
    items[n].importe_leido, items[n].importe = items[n].importe, valor
    return 1


def sugerir_producto(descripcion: str) -> str:
    d = norm(descripcion)
    for producto, patron in _REGLAS_PRODUCTO:
        if re.search(patron, d):
            return producto
    return ""


def sugerir_productos(items: list[Item]) -> None:
    """Completa «producto» donde falta, con un producto/servicio de Xubio. Si casi todo
    el detalle es comida (un restaurante), lo que no se reconoce también va como
    refrigerio; si no, como producto (al 21 %, 10,5 % o exento según el ítem)."""
    sugeridos = [sugerir_producto(i.descripcion) or (i.producto if i.producto in PRODUCTOS else "") for i in items]
    conocidos = [x for x in sugeridos if x]
    resto = "Refrigerio" if conocidos and conocidos.count("Refrigerio") >= len(conocidos) * 0.6 else "Mercaderías"
    for item, sugerido in zip(items, sugeridos):
        if item.producto in PRODUCTOS_XUBIO:
            continue  # ya es un producto de Xubio (lo eligió la persona)
        categoria = sugerido or (resto if item.descripcion.strip() else "")
        item.producto = producto_xubio(categoria, item.alicuota) if categoria else ""


def _con_gemini(nombre: str, imagen, lectura: Lectura, plazo_s: float | None) -> Lectura | None:
    """Gemini, o None (con el aviso) si no estuvo disponible a tiempo."""
    from . import gemini

    try:
        return _desde_json(lectura, gemini.leer_imagen(imagen, plazo_s=plazo_s), "ia")
    except gemini.GeminiNoDisponible as exc:
        lectura.avisos.append(f"{exc}")
        return None


def hay_ocr_local() -> bool:
    from . import ocr, paddle

    return paddle.disponible() or ocr.disponible()


def _leer_local(nombre: str, imagen, lectura: Lectura, modo: Modo = "auto", pasadas_extra: bool = True) -> Lectura:
    """El OCR local: PaddleOCR si está instalado (opcional, más preciso); si no, o si
    falla, Tesseract."""
    from . import paddle

    if modo in ("auto", "paddle") and paddle.disponible():
        try:
            return _desde_json(lectura, paddle.extract_with_paddleocr(imagen), "ocr")
        except Exception as exc:
            logger.warning("PaddleOCR falló en %s: %s", nombre, exc)
            lectura.avisos.append("PaddleOCR no pudo leer la imagen: se leyó con Tesseract.")
    elif modo == "paddle":
        lectura.avisos.append("PaddleOCR no está instalado (pip install paddleocr paddlepaddle): se leyó con Tesseract.")
    return _imagen_a_lectura(nombre, imagen, lectura, pasadas_extra=pasadas_extra)


def _leer_imagen(nombre: str, contenido: bytes, motor: str | None = None) -> Lectura:
    """En cascada: el QR de ARCA (exacto) y después, según el modo:
    - gemini: solo Gemini, sin plazo ni respaldo;
    - paddle / tesseract: solo ese OCR local;
    - auto: las capturas limpias, primero el OCR local en una pasada: si cierra,
      listo. Si no, Gemini con plazo (GEMINI_PLAZO_S, 15 s); si no hay clave, falla,
      da 429/503 o tarda, el OCR local (PaddleOCR o Tesseract)."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    from . import gemini
    from .qr_afip import leer_qrs

    try:
        imagen = Image.open(io.BytesIO(contenido))
        imagen.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"«{nombre}» no es una imagen válida.") from exc
    captura = es_captura_limpia(imagen, contenido)
    imagen = ImageOps.exif_transpose(imagen).convert("RGB")
    inicio = time.perf_counter()
    modo = modo_ocr(motor)
    qrs = leer_qrs(imagen)
    lectura = Lectura(archivo=nombre)

    if modo == "tesseract":
        lectura = _imagen_a_lectura(nombre, imagen, lectura)
    elif modo == "paddle":
        lectura = _leer_local(nombre, imagen, lectura, "paddle")
    elif modo == "gemini":
        if gemini.disponible():
            lectura = _con_gemini(nombre, imagen, lectura, None) or lectura
        else:
            lectura.avisos.append("OCR_MODE=gemini_only pero no hay clave de Gemini (GEMINI_API_KEY).")
    else:
        local = hay_ocr_local()
        if captura and local:
            rapida = _leer_local(nombre, imagen, Lectura(archivo=nombre), pasadas_extra=False)
            if _cierra_bien(rapida):
                lectura = rapida
            elif gemini.disponible():
                lectura.avisos.append("La captura leída con OCR local no cerraba: se leyó con Gemini.")
        if not lectura.motor and gemini.disponible():
            # El plazo corto sólo tiene sentido si hay un OCR local para leer después:
            # sin él, se espera a Gemini lo que haga falta.
            if (con_ia := _con_gemini(nombre, imagen, lectura, gemini.plazo_auto() if local else None)) is not None:
                lectura = con_ia
            elif local:
                lectura.avisos[-1] += " Se leyó con el OCR local."
                lectura = _leer_local(nombre, imagen, lectura)
            else:
                lectura.avisos[-1] += " Tampoco hay OCR local (Tesseract) instalado para leerla: probá de nuevo en un rato."
        elif not lectura.motor:
            lectura = _leer_local(nombre, imagen, lectura)
            if not lectura.texto:
                lectura.avisos.append(
                    "Para leer imágenes con IA, poné la clave de Gemini (GEMINI_API_KEY) en el archivo .env "
                    "de la carpeta de datos de la app y volvé a subir la imagen."
                )
    if qrs:
        lectura = _aplicar_qr(lectura, qrs[0])
    lectura.duracion_ms = round((time.perf_counter() - inicio) * 1000)
    return lectura


def _leer_pdf(nombre: str, contenido: bytes) -> Lectura:
    import pypdfium2 as pdfium

    lectura = Lectura(archivo=nombre, metodo="pdf")
    try:
        pdf = pdfium.PdfDocument(contenido)
    except Exception as exc:
        raise ValueError(f"«{nombre}» no es un PDF válido o tiene contraseña.") from exc
    try:
        total = len(pdf)
        if total > MAX_PAGINAS:
            lectura.avisos.append(f"Tiene {total} páginas: se leyeron las primeras {MAX_PAGINAS}.")
        textos = []
        for i in range(min(total, MAX_PAGINAS)):
            pagina = pdf[i]
            try:
                tp = pagina.get_textpage()
                textos.append(tp.get_text_range())
                tp.close()
            finally:
                pagina.close()
        texto = "\n".join(textos)
        if len(texto.strip()) >= 40:
            if total > 1:
                lectura.avisos.append(f"Tiene {min(total, MAX_PAGINAS)} páginas: se leyeron como un solo documento.")
            return _desde_texto(lectura, [texto])
        # Escaneado: OCR de la primera página (las demás suelen ser copias).
        pagina = pdf[0]
        try:
            imagen = pagina.render(scale=300 / 72).to_pil()
        finally:
            pagina.close()
        if total > 1:
            lectura.avisos.append(f"PDF escaneado de {total} páginas: se leyó la primera.")
        return _leer_local(nombre, imagen, lectura)
    finally:
        pdf.close()


def _celda(v):
    """Números y fechas como tales; lo demás, texto."""
    if v is None:
        return None
    if isinstance(v, float) and v != v:
        return None
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, (datetime, date)):
        return v.strftime("%d/%m/%Y")
    t = str(v).strip()
    if not t:
        return None
    # Ceros a la izquierda (CUIT, punto de venta) y números largos quedan como texto.
    if re.fullmatch(r"-?\$?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|-?\d+(?:[.,]\d+)?", t) and not re.fullmatch(r"0\d+", t) and len(re.sub(r"\D", "", t)) <= 15:
        n = importe(t)
        if n is not None:
            return int(n) if n.is_integer() and not re.search(r"[.,]\d{1,2}$", t) else n
    return t


def _leer_planilla(nombre: str, contenido: bytes) -> Lectura:
    import pandas as pd

    from .planillas import _leer_csv

    lectura = Lectura(archivo=nombre, metodo="planilla")
    sufijo = Path(nombre).suffix.lower()
    if sufijo in (".csv", ".txt"):
        tablas = {"Hoja1": _leer_csv(contenido)}
    else:
        motor = "xlrd" if sufijo == ".xls" else "openpyxl"
        try:
            with pd.ExcelFile(io.BytesIO(contenido), engine=motor) as libro:
                tablas = {h: pd.read_excel(libro, sheet_name=h, header=None, dtype=object) for h in libro.sheet_names[:60]}
        except Exception as exc:
            raise ValueError(f"«{nombre}» no se pudo abrir como planilla.") from exc
    for hoja, df in tablas.items():
        filas = [[_celda(v) for v in fila] for fila in df.itertuples(index=False, name=None)]
        filas = [f for f in filas if any(c is not None for c in f)]
        # Sin columnas vacías al final.
        ancho = max((max((i + 1 for i, c in enumerate(f) if c is not None), default=0) for f in filas), default=0)
        if len(filas) > MAX_FILAS:
            lectura.avisos.append(f"La hoja «{hoja}» tiene {len(filas)} filas: se pasan las primeras {MAX_FILAS}.")
        lectura.hojas.append(Hoja(nombre=str(hoja)[:31] or "Hoja", filas=[f[:ancho] for f in filas[:MAX_FILAS]]))
    lectura.tipo = "tabla"
    return lectura


def leer(nombre: str, contenido: bytes, motor: str | None = None) -> Lectura:
    """`motor` (opcional, para probar): auto, gemini, paddle o tesseract; si no,
    OCR_MODE."""
    nombre = Path(nombre).name
    sufijo = Path(nombre).suffix.lower()
    if sufijo in PLANILLAS:
        lectura = _leer_planilla(nombre, contenido)
    elif sufijo == ".pdf":
        lectura = _leer_pdf(nombre, contenido)
    elif sufijo in IMAGENES:
        lectura = _leer_imagen(nombre, contenido, motor)
    else:
        raise ValueError(f"«{nombre}»: formato no soportado (foto, captura, PDF, CSV o Excel).")
    if lectura.tipo == "comprobante":
        corregir_descripciones(lectura.items)
        corregir_importes_items(lectura.items, lectura.totales)
        sugerir_productos(lectura.items)
    completar_desglose(lectura)
    lectura.verificaciones = verificar(lectura)
    return lectura


# --- Verificación --------------------------------------------------------------------

# Dígitos que el OCR confunde entre sí.
_CONFUSIONES = {"0": "689", "1": "74", "2": "7", "3": "985", "4": "1", "5": "63", "6": "058", "7": "12", "8": "3069", "9": "38"}


def _fmt(v: float | None) -> str:
    if v is None:
        return ""
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _variantes(texto: str) -> list[tuple[int, str]]:
    """(posición, texto) con un dígito cambiado por otro que el OCR confunde."""
    salida = []
    for i, d in enumerate(texto):
        for r in _CONFUSIONES.get(d, ""):
            salida.append((i, texto[:i] + r + texto[i + 1 :]))
    return salida


def cuits_cambiando_0_y_8(cuit: str) -> list[str]:
    """CUIT válidos que salen de cambiar 0 por 8 y 8 por 0 (en las ticketeadoras el
    cero va tachado y en una foto borrosa parece un ocho). Los de menos cambios."""
    posiciones = [i for i, d in enumerate(cuit) if d in "08"]
    for cuantos in range(1, len(posiciones) + 1):
        validos = []
        for cambiar in itertools.combinations(posiciones, cuantos):
            c = "".join(("8" if d == "0" else "0") if i in cambiar else d for i, d in enumerate(cuit))
            if cuit_valido(c):
                validos.append(c)
        if validos:
            return validos
    return []


def cuits_parecidos(cuit: str, texto: str = "") -> list[str]:
    """CUIT válidos que difieren en un dígito que el OCR suele confundir. Primero
    los que se parecen a otros números del texto (Ingresos Brutos suele ser el
    mismo CUIT, y cada lectura se equivoca en un dígito distinto)."""
    candidatos = []
    for _, c in _variantes(cuit):
        if cuit_valido(c) and c not in candidatos:
            candidatos.append(c)
    # Más de un dígito mal: en tiques térmicos casi siempre son 0 leídos como 8 (o al revés).
    candidatos += [c for c in cuits_cambiando_0_y_8(cuit) if c not in candidatos]
    otros = [n for n in re.findall(r"(?<!\d)\d{11}(?!\d)", re.sub(r"(?<=\d)[-.](?=\d)", "", texto)) if n != cuit]

    def apoyo(c: str) -> int:
        return sum(1 for n in otros if sum(a != b for a, b in zip(c, n)) <= 1)

    return sorted(candidatos, key=lambda c: -apoyo(c))


def _mejor_parecido(cuit: str, texto: str) -> str | None:
    """El parecido que se puede sugerir: el único, o el único con apoyo en el texto."""
    candidatos = cuits_parecidos(cuit, texto)
    if len(candidatos) == 1:
        return candidatos[0]
    otros = [n for n in re.findall(r"(?<!\d)\d{11}(?!\d)", re.sub(r"(?<=\d)[-.](?=\d)", "", texto)) if n != cuit]
    apoyos = [sum(1 for n in otros if sum(a != b for a, b in zip(c, n)) <= 1) for c in candidatos]
    if apoyos and apoyos[0] > 0 and apoyos.count(apoyos[0]) == 1:
        return candidatos[0]
    # Sin otro desempate, el único que sale cambiando 0 y 8 (el error típico de las ticketeadoras).
    if len(ceros_y_ochos := cuits_cambiando_0_y_8(cuit)) == 1:
        return ceros_y_ochos[0]
    return None


def _verificar_cuit(campo: str, valor: str, obligatorio: bool, texto: str = "") -> Verificacion | None:
    etiqueta = ETIQUETAS_ENCABEZADO[campo]
    digitos = re.sub(r"\D", "", valor or "")
    if not digitos:
        if obligatorio:
            return Verificacion(campo=campo, nivel="aviso", mensaje=f"No se encontró el {etiqueta}.", titulo=f"Falta el {etiqueta}")
        return None
    if len(digitos) != 11:
        return Verificacion(campo=campo, nivel="error", mensaje=f"El {etiqueta} «{valor}» no tiene 11 dígitos.",
                            titulo=f"{etiqueta} incompleto", datos=[valor])
    if not cuit_valido(digitos):
        mejor = _mejor_parecido(digitos, texto)
        mensaje = f"El {etiqueta} {digitos} no es válido (el dígito verificador no da): seguro está mal leído."
        if mejor:
            mensaje += f" Lo más probable es {mejor}: compará con la imagen."
        elif parecidos := cuits_parecidos(digitos, texto):
            mensaje += " Puede ser " + " o ".join(parecidos[:3]) + "."
        return Verificacion(campo=campo, nivel="error", mensaje=mensaje, sugerencia=mejor, titulo=f"{etiqueta} mal leído",
                            datos=[digitos], opciones=[] if mejor else cuits_parecidos(digitos, texto)[:3])
    return Verificacion(campo=campo, nivel="ok", mensaje=f"{etiqueta}: el dígito verificador da bien.", titulo=etiqueta)


def _alicuota_de(neto: float, iva: float) -> float | None:
    if not neto:
        return None
    for a in ALICUOTAS:
        if abs(neto * a / 100 - iva) <= max(0.05, abs(iva) * 0.0005):
            return a
    return None


def _variantes_importe(v: float) -> list[float]:
    texto = f"{v:.2f}"
    return [float(x) for _, x in _variantes(texto)]


def corregir_totales(t: dict[str, float | None], suma_items: float | None = None) -> dict[str, float] | None:
    """Los importes que, cambiando uno o dos dígitos que el OCR confunde (6 y 8,
    0 y 8, 3 y 8...), hacen cerrar neto + IVA + otros = total con el IVA en una
    alícuota del neto. Solo si hay una sola corrección así (o la que coincide con
    lo que suma el detalle). {campo: valor corregido} o None."""
    presentes = [k for k in TOTALES if t.get(k) is not None]
    if "total" not in presentes:
        return None

    def bien(x: dict[str, float | None]) -> bool:
        if not _cierra(x):
            return False
        neto, iva = x.get("neto_gravado"), x.get("iva")
        return not iva or (neto is not None and _alicuota_de(neto, iva) is not None)

    soluciones: list[dict[str, float]] = []
    for cuantos in (1, 2):
        for campos in itertools.combinations(presentes, cuantos):
            # Un importe en 0 no se «corrige» (0,00 → 8,00 no es un error de lectura).
            if any(not t[c] for c in campos):
                continue
            opciones = [_variantes_importe(t[c]) for c in campos]
            for valores in itertools.product(*opciones):
                x = {**t, **dict(zip(campos, valores))}
                if bien(x):
                    soluciones.append(dict(zip(campos, valores)))
        if soluciones:
            break
    if not soluciones:
        return None
    if len(soluciones) == 1:
        return soluciones[0]
    if suma_items:
        # El detalle desempata: la que deja el neto igual a lo que suman los ítems.
        cerca = [s for s in soluciones if abs(s.get("neto_gravado", t.get("neto_gravado") or 0) - suma_items) <= 1]
        if len(cerca) == 1:
            return cerca[0]
    return None


def _verificar_totales(t: dict[str, float | None], suma_items: float | None = None, varias_alicuotas: bool = False) -> list[Verificacion]:
    v: list[Verificacion] = []
    total = t.get("total")
    if total is None:
        return [Verificacion(campo="total", nivel="error", mensaje="No se encontró el total del comprobante.", titulo="Falta el total")]
    partes = {k: t.get(k) for k in TOTALES if k != "total"}
    suma = round(sum(x or 0 for x in partes.values()), 2)
    neto, iva = t.get("neto_gravado"), t.get("iva")
    if abs(suma - total) <= TOLERANCIA:
        v.append(Verificacion(campo="total", nivel="ok", mensaje=f"Neto + IVA + otros tributos = total ({_fmt(total)}).", titulo="El total cierra"))
    else:
        diferencia = round(total - suma, 2)
        sugerida = False
        # Primero, dígitos que el lector confunde (es lo que más pasa en fotos).
        if correccion := corregir_totales(t, suma_items):
            for campo, valor in correccion.items():
                v.append(Verificacion(
                    campo=campo, nivel="error",
                    mensaje=(f"Los importes no suman el total. {ETIQUETAS_TOTALES[campo]}: se leyó {_fmt(t.get(campo))} y "
                             f"seguro es {_fmt(valor)} (el lector confunde dígitos como 6 y 8); con ese valor "
                             "neto + IVA + otros tributos da el total y el IVA da justo una alícuota."),
                    sugerencia=valor, titulo=f"{ETIQUETAS_TOTALES[campo]} mal leído",
                    datos=[f"Leído {_fmt(t.get(campo))}", f"Debería ser {_fmt(valor)}"],
                ))
            sugerida = True
        # ¿Qué importe, corregido, hace cerrar la cuenta? El neto, si con el IVA da una alícuota.
        if iva and not sugerida and not varias_alicuotas:
            neto_calc = round(total - (suma - (neto or 0)), 2)
            if neto_calc > 0 and _alicuota_de(neto_calc, iva):
                v.append(Verificacion(
                    campo="neto_gravado", nivel="error",
                    mensaje=(f"Los importes no suman el total (falta {_fmt(diferencia)}). Con el total, el IVA y los otros "
                             f"tributos, el neto gravado tiene que ser {_fmt(neto_calc)} (y el IVA da justo el "
                             f"{_alicuota_de(neto_calc, iva):g}%). Se leyó {_fmt(neto)}."),
                    sugerencia=neto_calc, titulo="El neto no cierra con el total",
                    datos=[f"Leído {_fmt(neto)}", f"Debería ser {_fmt(neto_calc)}"],
                ))
                sugerida = True
        if not sugerida and neto and not varias_alicuotas:
            iva_calc = round(total - (suma - (iva or 0)), 2)
            if iva_calc > 0 and _alicuota_de(neto, iva_calc):
                v.append(Verificacion(
                    campo="iva", nivel="error",
                    mensaje=(f"Los importes no suman el total. Con el total y el neto, el IVA tiene que ser "
                             f"{_fmt(iva_calc)} ({_alicuota_de(neto, iva_calc):g}% del neto). Se leyó {_fmt(iva)}."),
                    sugerencia=iva_calc, titulo="El IVA no cierra con el total",
                    datos=[f"Leído {_fmt(iva)}", f"Debería ser {_fmt(iva_calc)}"],
                ))
                sugerida = True
        if not sugerida:
            v.append(Verificacion(
                campo="total", nivel="error",
                mensaje=f"Neto + IVA + otros tributos da {_fmt(suma)} y el total leído es {_fmt(total)} (diferencia {_fmt(diferencia)}). Revisá los importes.",
                titulo="Los importes no suman el total",
                datos=[f"Suma {_fmt(suma)}", f"Total {_fmt(total)}", f"Dif. {_fmt(diferencia)}"],
            ))
    if neto and iva and not varias_alicuotas and not any(x.nivel == "error" for x in v):
        if (a := _alicuota_de(neto, iva)) is not None:
            v.append(Verificacion(campo="iva", nivel="ok", mensaje=f"El IVA es el {a:g}% del neto gravado.", titulo=f"IVA {a:g}%"))
        elif iva > neto * 0.27 + TOLERANCIA:
            v.append(Verificacion(campo="iva", nivel="aviso", mensaje=f"El IVA ({_fmt(iva)}) es más del 27% del neto: revisalo.", titulo="IVA demasiado alto", datos=[_fmt(iva)]))
        else:
            v.append(Verificacion(campo="iva", nivel="aviso", mensaje="El IVA no es una sola alícuota del neto (puede tener varias): revisalo.", titulo="IVA con varias alícuotas"))
    return v


def _tolerancia_iva(iva: float) -> float:
    return max(TOLERANCIA, abs(iva) * 0.0005)


def iva_de_items(items: list[Item], t: dict[str, float | None]) -> tuple[dict[float, float], bool]:
    """El IVA que dan los ítems en cada alícuota. Si el detalle suma el neto, los
    importes son sin IVA; si suma el total (tiques, factura B), lo incluyen.
    ({alícuota: IVA}, si los importes incluyen IVA)."""
    suma = sum(x.importe or 0 for x in items)
    neto, total = t.get("neto_gravado"), t.get("total")
    con_iva = total is not None and (neto is None or abs(suma - total) < abs(suma - neto))
    por_alicuota: dict[float, float] = {}
    for x in items:
        if x.alicuota is None or x.importe is None:
            continue
        a = float(x.alicuota)
        iva = x.importe - x.importe / (1 + a / 100) if con_iva else x.importe * a / 100
        por_alicuota[a] = por_alicuota.get(a, 0) + iva
    return {a: round(v, 2) for a, v in por_alicuota.items()}, con_iva


def _fmt_alicuota(a: float | None) -> str:
    return "" if a is None else f"{a:g}".replace(".", ",") + "%"


def _verificar_desglose(lectura: Lectura, t: dict[str, float | None]) -> list[Verificacion]:
    filas = lectura.iva_alicuotas
    if not filas:
        return []
    v: list[Verificacion] = []
    bien = True
    for i, f in enumerate(filas):
        campo = f"iva_{i}"
        nombre = f"IVA {_fmt_alicuota(f.alicuota)}" if f.alicuota is not None else f"IVA (fila {i + 1})"
        if f.alicuota is None:
            v.append(Verificacion(campo=campo, nivel="aviso", mensaje=f"{nombre}: falta la alícuota."))
            bien = False
            continue
        if f.alicuota not in ALICUOTAS and f.alicuota != 0:
            v.append(Verificacion(campo=campo, nivel="aviso", mensaje=f"{nombre}: no es una alícuota de IVA habitual (21, 10,5, 27, 5 o 2,5)."))
            bien = False
        if f.base is not None and f.importe is not None:
            calculado = round(f.base * f.alicuota / 100, 2)
            if abs(calculado - f.importe) > _tolerancia_iva(f.importe):
                v.append(Verificacion(
                    campo=campo, nivel="error",
                    mensaje=f"{nombre}: el {_fmt_alicuota(f.alicuota)} de {_fmt(f.base)} es {_fmt(calculado)} y se leyó {_fmt(f.importe)}.",
                    sugerencia=calculado,
                ))
                bien = False
    if len({f.alicuota for f in filas}) < len(filas):
        v.append(Verificacion(campo="iva", nivel="aviso", mensaje="Hay dos filas de IVA con la misma alícuota: juntalas."))
        bien = False
    neto = t.get("neto_gravado")
    bases = round(sum(f.base or 0 for f in filas), 2)
    if neto is not None and any(f.base is not None for f in filas) and abs(bases - neto) > TOLERANCIA:
        v.append(Verificacion(
            campo="iva", nivel="error",
            mensaje=f"Las bases imponibles del IVA suman {_fmt(bases)} y el neto gravado es {_fmt(neto)} (diferencia {_fmt(round(neto - bases, 2))}).",
        ))
        bien = False
    # Lo que dan los ítems en cada alícuota.
    if lectura.items and all(x.alicuota is not None and x.importe is not None for x in lectura.items):
        por_alicuota, _ = iva_de_items(lectura.items, t)
        tolerancia_items = max(TOLERANCIA, 0.01 * len(lectura.items))
        declaradas = {f.alicuota for f in filas}
        for i, f in enumerate(filas):
            if f.alicuota is None or f.importe is None:
                continue
            de_items = por_alicuota.get(f.alicuota, 0.0)
            if abs(de_items - f.importe) > tolerancia_items:
                v.append(Verificacion(
                    campo=f"iva_{i}", nivel="aviso",
                    mensaje=f"IVA {_fmt_alicuota(f.alicuota)}: los ítems de esa alícuota dan {_fmt(de_items)} de IVA y el comprobante dice {_fmt(f.importe)}.",
                ))
                bien = False
        for a, iva in por_alicuota.items():
            if a not in declaradas and abs(iva) > TOLERANCIA:
                v.append(Verificacion(
                    campo="iva", nivel="aviso",
                    mensaje=f"Hay ítems al {_fmt_alicuota(a)} ({_fmt(iva)} de IVA) y esa alícuota no está en el desglose del IVA.",
                ))
                bien = False
    if bien:
        texto = " · ".join(f"{_fmt_alicuota(f.alicuota)}: {_fmt(f.importe)}" for f in filas)
        v.append(Verificacion(campo="iva", nivel="ok", mensaje=f"El IVA de cada alícuota da con su base ({texto})."))
    return v


def _verificar_tributos(tributos: list[Tributo]) -> list[Verificacion]:
    v: list[Verificacion] = []
    for i, x in enumerate(tributos):
        nombre = x.descripcion or f"fila {i + 1}"
        if x.importe is None:
            v.append(Verificacion(campo=f"tributo_{i}", nivel="aviso", mensaje=f"Otros tributos, {nombre}: falta el importe."))
        elif not x.descripcion:
            v.append(Verificacion(campo=f"tributo_{i}", nivel="aviso", mensaje=f"Otros tributos, fila {i + 1}: falta qué tributo es."))
        elif x.base and abs(x.importe) > abs(x.base):
            v.append(Verificacion(
                campo=f"tributo_{i}", nivel="aviso",
                mensaje=f"Otros tributos, {nombre}: el importe ({_fmt(x.importe)}) es mayor que la base ({_fmt(x.base)}): revisalo.",
            ))
    if tributos and not v:
        texto = " · ".join(f"{x.descripcion}: {_fmt(x.importe)}" for x in tributos)
        v.append(Verificacion(campo="otros_tributos", nivel="ok", mensaje=f"Otros tributos: {texto}."))
    return v


def _verificar_items(items: list[Item], t: dict[str, float | None]) -> list[Verificacion]:
    corregidos = [(n + 1, x) for n, x in enumerate(items) if x.leido and x.leido != x.descripcion]
    aviso = [Verificacion(
        campo="descripcion", nivel="aviso",
        mensaje="Estas descripciones se leyeron mal y las corregí solo (en naranja en el detalle): "
                + "; ".join(f"ítem {n}: «{x.leido}» → «{x.descripcion}»" for n, x in corregidos) + ". Revisalas.",
        titulo=f"Autocorregí {len(corregidos)} {'descripción' if len(corregidos) == 1 else 'descripciones'}",
        datos=[f"#{n} {x.descripcion}" for n, x in corregidos[:4]],
    )] if corregidos else []
    for n, x in enumerate(items):
        if x.importe_leido is not None and x.importe is not None and x.importe_leido != x.importe:
            aviso.append(Verificacion(
                campo="items", nivel="aviso",
                mensaje=(f"El importe del ítem {n + 1} se leyó {_fmt(x.importe_leido)} y lo corregí a {_fmt(x.importe)}: "
                         "con ese valor el detalle suma el neto (en naranja en el detalle). Compará con la imagen."),
                titulo=f"Corregí el importe del ítem {n + 1}",
                datos=[f"Leído {_fmt(x.importe_leido)}", f"Ahora {_fmt(x.importe)}"],
            ))
    return aviso + _verificar_items_sin_correccion(items, t)


def _verificar_items_sin_correccion(items: list[Item], t: dict[str, float | None]) -> list[Verificacion]:
    if not items:
        return [Verificacion(campo="items", nivel="aviso", mensaje="No se pudo leer el detalle: el Excel va a tener solo el encabezado y los totales.", titulo="Sin detalle")]
    v: list[Verificacion] = []
    sin_importe = [i + 1 for i, x in enumerate(items) if x.importe is None]
    if sin_importe:
        v.append(Verificacion(campo="items", nivel="aviso", mensaje=f"Ítems sin importe: {', '.join(map(str, sin_importe))}.", titulo="Ítems sin importe", datos=[f"#{n}" for n in sin_importe[:6]]))
    sin_desc = [i + 1 for i, x in enumerate(items) if not x.descripcion.strip()]
    if sin_desc:
        v.append(Verificacion(campo="items", nivel="aviso", mensaje=f"Ítems sin descripción: {', '.join(map(str, sin_desc))}.", titulo="Ítems sin descripción", datos=[f"#{n}" for n in sin_desc[:6]]))
    # Mismo producto, cantidad y precio pero otro importe: uno de los dos está mal leído.
    for i, a in enumerate(items):
        for j in range(i + 1, len(items)):
            b = items[j]
            if (a.cantidad, a.precio, a.alicuota) == (b.cantidad, b.precio, b.alicuota) and a.precio and a.importe != b.importe                     and a.importe and b.importe and abs(a.importe - b.importe) < 0.02 * max(a.importe, b.importe):
                v.append(Verificacion(
                    campo="items", nivel="aviso",
                    mensaje=(f"Los ítems {i + 1} y {j + 1} tienen la misma cantidad y precio pero distinto importe "
                             f"({_fmt(a.importe)} y {_fmt(b.importe)}): uno puede estar mal leído."),
                    titulo=f"Ítems {i + 1} y {j + 1}: mismo precio, otro importe",
                    datos=[f"#{i + 1} {_fmt(a.importe)}", f"#{j + 1} {_fmt(b.importe)}"],
                ))
    suma = round(sum(x.importe or 0 for x in items), 2)
    # El detalle suma el neto (factura A) o el total (factura B/C, tiques).
    for campo in ("neto_gravado", "total"):
        objetivo = t.get(campo)
        if objetivo is None:
            continue
        dif = round(objetivo - suma, 2)
        if abs(dif) <= max(TOLERANCIA, 0.01 * len(items) / 2):
            extra = f" (diferencia de redondeo {_fmt(dif)})" if dif else ""
            v.append(Verificacion(campo="items", nivel="ok", mensaje=f"Los {len(items)} ítems suman el {ETIQUETAS_TOTALES[campo].lower()}: {_fmt(suma)}{extra}.", titulo=f"Los {len(items)} ítems suman bien"))
            return v
    objetivo = t.get("neto_gravado") if t.get("neto_gravado") is not None else t.get("total")
    if objetivo is not None:
        v.append(Verificacion(
            campo="items", nivel="error",
            mensaje=f"Los {len(items)} ítems suman {_fmt(suma)} y el neto gravado es {_fmt(objetivo)} (diferencia {_fmt(round(objetivo - suma, 2))}): algún importe está mal leído o falta un ítem.",
            titulo="Los ítems no suman el neto",
            datos=[f"Ítems {_fmt(suma)}", f"Neto {_fmt(objetivo)}", f"Dif. {_fmt(round(objetivo - suma, 2))}"],
        ))
    return v


def _verificar_fecha(valor: str) -> Verificacion | None:
    if not valor:
        return Verificacion(campo="fecha", nivel="aviso", mensaje="No se encontró la fecha.", titulo="Falta la fecha")
    try:
        d = datetime.strptime(valor, "%d/%m/%Y").date()
    except ValueError:
        return Verificacion(campo="fecha", nivel="error", mensaje=f"La fecha «{valor}» no es válida (tiene que ser DD/MM/AAAA).", titulo="Fecha inválida", datos=[valor])
    if d > date.today():
        return Verificacion(campo="fecha", nivel="aviso", mensaje=f"La fecha {valor} es posterior a hoy.", titulo="Fecha futura", datos=[valor])
    if d.year < 2000:
        return Verificacion(campo="fecha", nivel="aviso", mensaje=f"La fecha {valor} es muy vieja: revisala.", titulo="Fecha muy vieja", datos=[valor])
    return Verificacion(campo="fecha", nivel="ok", mensaje=f"Fecha {valor}.", titulo=f"Fecha {valor}")


def _verificar_tabla(lectura: Lectura) -> list[Verificacion]:
    v: list[Verificacion] = []
    if not lectura.hojas or not any(h.filas for h in lectura.hojas):
        return [Verificacion(campo="hojas", nivel="error", mensaje="No se leyó ningún dato.")]
    for h in lectura.hojas:
        if not h.filas:
            v.append(Verificacion(campo="hojas", nivel="aviso", mensaje=f"La hoja «{h.nombre}» está vacía."))
            continue
        ancho = max(len(f) for f in h.filas)
        v.append(Verificacion(campo="hojas", nivel="ok", mensaje=f"«{h.nombre}»: {len(h.filas)} filas y {ancho} columnas."))
        # Columnas con números y texto mezclados (fuera del encabezado): suele ser un número mal escrito.
        for col in range(ancho):
            valores = [f[col] for f in h.filas[1:] if col < len(f) and f[col] is not None]
            nums = [x for x in valores if isinstance(x, (int, float))]
            textos = [x for x in valores if isinstance(x, str)]
            if len(valores) >= 5 and len(nums) >= 0.8 * len(valores) and textos:
                v.append(Verificacion(
                    campo="hojas", nivel="aviso",
                    mensaje=f"«{h.nombre}», columna {col + 1}: casi todos son números pero {len(textos)} no ({', '.join(map(str, textos[:3]))}).",
                ))
    if lectura.metodo in ("ocr", "ia"):
        como = "con OCR" if lectura.metodo == "ocr" else "con IA (Gemini)"
        v.append(Verificacion(campo="hojas", nivel="aviso", mensaje=f"Leído {como}: compará el texto con la imagen antes de armar el Excel."))
    return v


def verificar(lectura: Lectura) -> list[Verificacion]:
    if lectura.tipo == "tabla":
        return _verificar_tabla(lectura)
    e = lectura.encabezado
    t = totales_efectivos(lectura)
    v: list[Verificacion] = []
    for campo, obligatorio in (("emisor_cuit", True), ("receptor_cuit", False)):
        if (x := _verificar_cuit(campo, e.get(campo, ""), obligatorio, lectura.texto)) is not None:
            v.append(x)
    if not e.get("numero"):
        v.append(Verificacion(campo="numero", nivel="aviso", mensaje="No se encontró el número de comprobante.", titulo="Falta el número"))
    elif not re.fullmatch(r"\d{4,5}-\d{8}", e["numero"]):
        v.append(Verificacion(campo="numero", nivel="aviso", mensaje=f"El número «{e['numero']}» no tiene la forma 00001-00000001.", titulo="Número raro", datos=[e["numero"]]))
    if not e.get("tipo"):
        v.append(Verificacion(campo="tipo", nivel="aviso", mensaje="No se reconoció el tipo de comprobante.", titulo="Falta el tipo"))
    if (x := _verificar_fecha(e.get("fecha", ""))) is not None:
        v.append(x)
    v += _verificar_totales(t, round(sum(x.importe or 0 for x in lectura.items), 2) if lectura.items else None, len(lectura.iva_alicuotas) > 1)
    v += _verificar_desglose(lectura, t)
    v += _verificar_tributos(lectura.tributos)
    v += _verificar_items(lectura.items, t)
    # Lo que cada pasada leyó distinto y sigue sin verificarse por otro lado.
    con_error = {x.campo for x in v if x.nivel != "ok"}
    verificados = {x.campo for x in v if x.nivel == "ok"}
    for campo, valores in lectura.dudas.items():
        if campo in con_error or (campo in verificados and campo.endswith("_cuit")) or (campo in TOTALES and _cierra(t)):
            continue
        etiqueta = ETIQUETAS_ENCABEZADO.get(campo) or ETIQUETAS_TOTALES.get(campo, campo)
        v.append(Verificacion(campo=campo, nivel="aviso", mensaje=f"{etiqueta}: cada lectura dio algo distinto ({' / '.join(valores[:3])}). Compará con la imagen.",
                             titulo=f"{etiqueta}: ¿cuál es?", opciones=valores[:3]))
    # Primero lo que hay que arreglar.
    orden = {"error": 0, "aviso": 1, "ok": 2}
    return sorted(v, key=lambda x: orden[x.nivel])


# --- Excel ---------------------------------------------------------------------------


def nombre_excel(lectura: Lectura, operacion: str | None = None, empresa: str = "") -> str:
    """Como el AUX de un control: «Conversor de facturas Compras con AFIP - 10-26 - Cliente.xlsx»."""
    from ..core.controles import mes_anio
    from ..core.paths import limpiar_nombre

    base = re.sub(r'[\\/*?:"<>|]', "", Path(lectura.archivo).stem).strip() or "documento"
    if lectura.tipo != "comprobante" or operacion not in ("Compras", "Ventas"):
        return f"{base}.xlsx"
    cliente = limpiar_nombre(empresa).rstrip(". ") or base
    fecha = lectura.encabezado.get("fecha", "")
    periodo = f" - {mes_anio(fecha)}" if re.fullmatch(r"\d{2}/\d{2}/\d{4}", fecha) else ""
    return f"Conversor de facturas {operacion} con AFIP{periodo} - {cliente}.xlsx"


class Extras(BaseModel):
    """Lo que se completa en la factura «como en Xubio» y no sale del archivo."""

    condicion: str = Field(default="", max_length=60)
    vencimiento: str = Field(default="", max_length=10)
    productos: list[str] = Field(default_factory=list, max_length=2000)
    centros: list[str] = Field(default_factory=list, max_length=2000)
    dto: list[float | None] = Field(default_factory=list, max_length=2000)


# El modelo de Xubio «Importar comprobantes» (modelo-importacion-ventas): una fila con
# la cabecera y una por ítem, unidas por NUMERODECONTROL. En compras va el proveedor.
COLUMNAS_IMPORTACION = [
    "NUMERODECONTROL", "CLIENTE", "TIPO", "NUMERO", "FECHA", "VENCIMIENTODELCOBRO",
    "COMPROBANTEASOCIADO", "MONEDA", "COTIZACION", "OBSERVACIONES", "PRODUCTOSERVICIO",
    "CENTRODECOSTO", "PRODUCTOOBSERVACION", "CANTIDAD", "PRECIO", "DESCUENTO", "IMPORTE", "IVA",
]


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


def filas_importacion(lectura: Lectura, operacion: str, extras: Extras | None = None) -> list[list]:
    extras = extras or Extras()
    e, t = lectura.encabezado, lectura.totales
    lado = "emisor" if operacion == "Compras" else "receptor"
    fecha = _fecha_xubio(e.get("fecha", ""))
    cabecera = [
        1, e.get(lado, ""), _tipo_xubio(e.get("tipo", "")), _numero_xubio(lectura), fecha,
        _fecha_xubio(extras.vencimiento) if extras.vencimiento else fecha,
        "", "Pesos Argentinos", "", Path(lectura.archivo).stem[:200],
    ] + [None] * 8
    items = lectura.items or [Item(
        descripcion="Según factura", cantidad=1, precio=t.get("neto_gravado"), importe=t.get("neto_gravado"),
        alicuota=_alicuota_de(t.get("neto_gravado") or 0.0, t.get("iva") or 0.0),
    )]
    filas = [cabecera]
    for n, it in enumerate(items):
        def de(lista, defecto=None):
            return lista[n] if n < len(lista) and lista[n] not in ("", None) else defecto
        filas.append([1] + [None] * 9 + [
            de(extras.productos, it.producto or it.codigo or it.descripcion[:60]), de(extras.centros), it.descripcion or None,
            it.cantidad, it.precio, de(extras.dto) or None, it.importe, it.alicuota,
        ])
    return filas


def _hoja_impuestos(wb, lectura: Lectura) -> None:
    """El modelo de Xubio no trae columnas para impuestos internos ni percepciones: van
    en otra hoja (la importación lee la primera) para cargarlos en «Percepciones e Impuestos»."""
    t = lectura.totales
    internos = [(n + 1, i.descripcion, i.impuestos_internos) for n, i in enumerate(lectura.items) if i.impuestos_internos]
    if not (internos or t.get("impuestos_internos") or t.get("percepcion_iibb") or t.get("otros_tributos")):
        return
    ws = wb.create_sheet("Impuestos y percepciones")
    ws.append(["CONCEPTO", "ITEM", "DETALLE", "IMPORTE"])
    for n, desc, v in internos:
        ws.append(["Impuestos internos", n, desc, v])
    if t.get("impuestos_internos") and not internos:
        ws.append(["Impuestos internos", None, "Total de la factura", t["impuestos_internos"]])
    if t.get("percepcion_iibb"):
        ws.append(["Percepción IIBB Bs. As.", None, None, t["percepcion_iibb"]])
    if t.get("otros_tributos"):
        ws.append(["Otros tributos", None, None, t["otros_tributos"]])
    for celda in ws["D"][1:]:
        celda.number_format = "#,##0.00"
    for letra, ancho in zip("ABCD", (26, 8, 40, 14)):
        ws.column_dimensions[letra].width = ancho


def excel_importacion(lectura: Lectura, operacion: str, extras: Extras | None = None) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet0"
    encabezado = list(COLUMNAS_IMPORTACION)
    if operacion == "Compras":
        encabezado[1], encabezado[5] = "PROVEEDOR", "VENCIMIENTODELPAGO"
    ws.append(encabezado)
    for fila in filas_importacion(lectura, operacion, extras):
        ws.append(fila)
    for letra in "EF":
        for celda in ws[letra][1:]:
            celda.number_format = "dd/mm/yyyy"
    for letra in "OPQ":
        for celda in ws[letra][1:]:
            celda.number_format = "#,##0.00"
    for i, c in enumerate(encabezado):
        ws.column_dimensions[chr(65 + i)].width = max(12, len(c) + 2)
    _hoja_impuestos(wb, lectura)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


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


def extract_invoice_data(ruta, motor: str | None = None) -> Lectura:
    """Un archivo del disco (PDF, foto, captura, planilla) → la Lectura, ya
    verificada. Lo mismo que hace el endpoint con lo que se sube."""
    from pathlib import Path

    ruta = Path(ruta)
    return leer(ruta.name, ruta.read_bytes(), motor)
