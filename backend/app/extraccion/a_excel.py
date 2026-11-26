"""«Pasar a Excel»: cualquier archivo (foto, captura, PDF, CSV, XLS) a un .xlsx,
con una verificación de lo leído antes de armarlo.

1. `leer`: saca los datos del archivo.
   - Planillas y CSV: las hojas tal cual (los números y fechas como tales).
   - Fotos, capturas y PDF escaneados: OCR en varias pasadas (`PASADAS`). Lo que
     cada pasada lee distinto queda marcado y, entre las lecturas, se elige la que
     cierra con las cuentas (neto + IVA + otros tributos = total).
   - PDF digitales: el texto del PDF (sin OCR).
   Si el texto parece un comprobante, se arman encabezado, detalle y totales; si
   no, el texto queda como tabla (columnas separadas por espacios).
2. `verificar`: lo que conviene mirar antes de armar el Excel: CUIT con dígito
   verificador mal, totales que no cierran, detalle que no suma el neto, fecha
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
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .modelo import TIPOS, cuit_valido, fecha as fecha_iso, importe, nombre_tipo, norm
from .texto_factura import _tipo as tipo_del_texto
from .texto_factura import cuits as cuits_del_texto

logger = logging.getLogger(__name__)

PLANILLAS = {".xlsx", ".xlsm", ".xls", ".csv", ".txt"}
IMAGENES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
FORMATOS = PLANILLAS | IMAGENES | {".pdf"}

# (lado mayor en px, modo de página de Tesseract). Probado con fotos de tiques
# térmicos: 4000 px en modo 6 es la que mejor lee; las otras sirven para comparar.
PASADAS = ((4000, 6), (3200, 6), (2600, 6))
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
    "cae": "CAE",
}

Nivel = Literal["ok", "aviso", "error"]


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
    encabezado: dict[str, str] = Field(default_factory=dict)
    items: list[Item] = Field(default_factory=list, max_length=2000)
    totales: dict[str, float | None] = Field(default_factory=dict)
    hojas: list[Hoja] = Field(default_factory=list, max_length=60)
    texto: str = Field(default="", max_length=400_000)
    # Lo que cada pasada de OCR leyó distinto: campo -> valores leídos.
    dudas: dict[str, list[str]] = Field(default_factory=dict)
    # Lo que se autocorrigió del encabezado: campo -> lo que se había leído.
    corregidos: dict[str, str] = Field(default_factory=dict)
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
    if m := re.search(r"C\.?\s?A\.?\s?E\.?\s*(?:N[°º.*o]?)?\s*:?\s*(\d{14})\b", texto, re.IGNORECASE):
        e["cae"] = m.group(1)
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


def _imagen_a_lectura(nombre: str, imagen, lectura: Lectura) -> Lectura:
    from .ocr import OCRNoDisponible, texto_de_imagen_variante

    lectura.metodo = "ocr"
    pasadas = []
    for lado, psm in PASADAS:
        try:
            pasadas.append(texto_de_imagen_variante(imagen, lado, psm))
        except OCRNoDisponible as exc:
            lectura.avisos.append(str(exc))
            break
        except Exception as exc:
            logger.warning("OCR falló en %s (%s px, psm %s): %s", nombre, lado, psm, exc)
    if not pasadas:
        lectura.avisos.append("No se pudo leer el texto de la imagen.")
        return lectura
    return _desde_texto(lectura, pasadas)


def _numero(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return importe(v)


def _entero(v) -> int | None:
    digitos = re.sub(r"\D", "", str(v if v is not None else ""))
    return int(digitos) if digitos else None


def _desde_gemini(lectura: Lectura, datos: dict) -> Lectura:
    """El JSON de Gemini (ver gemini.PROMPT) a una Lectura, con los mismos formatos
    que la lectura por OCR: así pasa por las mismas verificaciones."""
    lectura.metodo = "ia"
    lectura.texto = str(datos.get("texto") or "")[:400_000]
    if str(datos.get("tipo") or "").lower() == "tabla" or not isinstance(datos.get("encabezado"), dict):
        filas = [[_celda(c) for c in f] for f in (datos.get("filas") or []) if isinstance(f, list)]
        lectura.tipo = "tabla"
        lectura.hojas = [Hoja(nombre="Imagen", filas=filas[:MAX_FILAS])] if filas else [hoja_de_texto(lectura.texto)]
        return lectura

    g = datos.get("encabezado") or {}
    e: dict[str, str] = {}
    for rol in ("emisor", "receptor"):
        if nombre := str(g.get(rol) or "").strip():
            e[rol] = nombre[:200]
        if cuit := re.sub(r"\D", "", str(g.get(f"{rol}_cuit") or "")):
            e[f"{rol}_cuit"] = cuit
    if (codigo := _entero(g.get("tipo_codigo"))) in TIPOS:
        e["tipo"] = nombre_tipo(codigo)
    pv, nro = _entero(g.get("punto_de_venta")), _entero(g.get("numero"))
    if nro is not None:
        e["numero"] = f"{pv or 0:05d}-{nro:08d}"
    if f := str(g.get("fecha") or "").strip():
        iso = fecha_iso(f)
        e["fecha"] = f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso else f
    if cae := re.sub(r"\D", "", str(g.get("cae") or "")):
        e["cae"] = cae
    lectura.tipo = "comprobante"
    lectura.encabezado = e
    lectura.items = [
        Item(
            codigo=str(x.get("codigo") or "")[:40],
            descripcion=str(x.get("descripcion") or "")[:300],
            cantidad=_numero(x.get("cantidad")),
            precio=_numero(x.get("precio")),
            alicuota=_numero(x.get("alicuota")),
            importe=_numero(x.get("importe")),
            impuestos_internos=_numero(x.get("impuestos_internos")),
            producto=str(x.get("producto") or "")[:60],
        )
        for x in (datos.get("items") or [])[:2000]
        if isinstance(x, dict)
    ]
    t = datos.get("totales") or {}
    lectura.totales = {k: _numero(t.get(k)) for k in TOTALES}
    return lectura


# --- Producto sugerido por ítem (cuando no lo dio la IA) ------------------------------

PRODUCTOS = ("Mercaderías", "Servicios", "Honorarios", "Combustible", "Refrigerio", "Gastos de limpieza", "Alquiler")
_REGLAS_PRODUCTO = (
    ("Combustible", r"nafta|gas\s?oil|diesel|combustible|infinia|v.?power|\bgnc\b|lubric|\bquantium\b"),
    ("Gastos de limpieza", r"lavandina|detergente|limpi|desinfect|papel\s+higien|rollo\s+cocina|trapo|escoba|jabon|lysoform|cif\b"),
    ("Alquiler", r"alquiler|locacion|expensas"),
    ("Honorarios", r"honorario"),
    ("Servicios", r"servicio|\bfee\b|abono|suscrip|mantenimiento|asesor|consultor|internet|telefon|hosting|licencia|flete|envio"),
    ("Refrigerio", r"\bagua\b|gaseosa|\bcafe\b|cortado|medialuna|sandwich|\bmenu\b|almuerzo|cena|empanada|pizza|cerveza|\bvino\b|"
                   r"\bpapas?\b|pure|carne|entra[nñ]a|salm|salh|pollo|ensalada|postre|helado|hamburg|coleslaw|bife|porcion|"
                   r"bebida|jugo|cubierto|guarnicion|milanesa|tostado|factura[s]?\s+dulce|desayuno|merienda|\bmesa\b"),
)


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
    """Completa «producto» donde falta. Si casi todo el detalle es comida (un restaurante),
    lo que no se reconoce también va como refrigerio; si no, como mercaderías."""
    sugeridos = [sugerir_producto(i.descripcion) or (i.producto if i.producto in PRODUCTOS else "") for i in items]
    conocidos = [x for x in sugeridos if x]
    resto = "Refrigerio" if conocidos and conocidos.count("Refrigerio") >= len(conocidos) * 0.6 else "Mercaderías"
    for item, sugerido in zip(items, sugeridos):
        item.producto = sugerido or (resto if item.descripcion.strip() else "")


def _leer_imagen(nombre: str, contenido: bytes) -> Lectura:
    from PIL import Image, ImageOps, UnidentifiedImageError

    from . import gemini

    try:
        imagen = Image.open(io.BytesIO(contenido))
        imagen.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"«{nombre}» no es una imagen válida.") from exc
    imagen = ImageOps.exif_transpose(imagen).convert("RGB")
    lectura = Lectura(archivo=nombre)
    # Con clave de Gemini, la lee la IA; si falla, el OCR de siempre.
    if gemini.disponible():
        try:
            return _desde_gemini(lectura, gemini.leer_imagen(imagen))
        except gemini.GeminiNoDisponible as exc:
            lectura.avisos.append(f"{exc} Se leyó con OCR.")
    else:
        lectura = _imagen_a_lectura(nombre, imagen, lectura)
        if not lectura.texto:
            lectura.avisos.append(
                "Para leer imágenes con IA, poné la clave de Gemini (GEMINI_API_KEY) en el archivo .env "
                "de la carpeta de datos de la app y volvé a subir la imagen."
            )
        return lectura
    return _imagen_a_lectura(nombre, imagen, lectura)


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
        return _imagen_a_lectura(nombre, imagen, lectura)
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


_BASURA_NOMBRE = re.compile(r"[$#@*|~^_=+<>{}\[\]\\/%!?¡¿;:]")
_SOCIEDAD = re.compile(r"\b(S\.?\s?A\.?(\s?U\.?)?|S\.?\s?R\.?\s?L\.?|S\.?\s?A\.?\s?S\.?|S\.?\s?C\.?\s?A\.?|S\.?\s?H\.?)\s*$", re.I)


def _esqueleto(nombre: str) -> str:
    """Las letras del nombre sin sociedad ni basura («FOC FUELS MARKETING»)."""
    sin = _SOCIEDAD.sub("", _BASURA_NOMBRE.sub(" ", nombre))
    return re.sub(r"[^A-Z0-9]", "", sin.upper())


def limpiar_nombres(lectura: Lectura) -> None:
    """Emisor y receptor que cada pasada leyó distinto solo por caracteres sueltos
    («FOC FUELS MARKETING $» / «… SA»): se queda con el más limpio (sin símbolos y con
    la sociedad), saca la duda y guarda lo leído en «corregidos»."""
    for campo in ("emisor", "receptor"):
        valores = lectura.dudas.get(campo)
        if not valores or len(valores) < 2:
            continue
        base = _esqueleto(valores[0])
        if not base or any(difflib.SequenceMatcher(None, base, _esqueleto(v)).ratio() < 0.85 for v in valores[1:]):
            continue
        limpio = min(valores, key=lambda v: (len(_BASURA_NOMBRE.findall(v)), not _SOCIEDAD.search(v), -len(v)))
        leido = lectura.encabezado.get(campo, "")
        if leido == limpio:
            leido = next(v for v in valores if v != limpio)
        lectura.encabezado[campo] = limpio
        lectura.corregidos[campo] = leido
        del lectura.dudas[campo]


def leer(nombre: str, contenido: bytes) -> Lectura:
    nombre = Path(nombre).name
    sufijo = Path(nombre).suffix.lower()
    if sufijo in PLANILLAS:
        lectura = _leer_planilla(nombre, contenido)
    elif sufijo == ".pdf":
        lectura = _leer_pdf(nombre, contenido)
    elif sufijo in IMAGENES:
        lectura = _leer_imagen(nombre, contenido)
    else:
        raise ValueError(f"«{nombre}»: formato no soportado (foto, captura, PDF, CSV o Excel).")
    if lectura.tipo == "comprobante":
        corregir_descripciones(lectura.items)
        corregir_importes_items(lectura.items, lectura.totales)
        limpiar_nombres(lectura)
        sugerir_productos(lectura.items)
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


def cuits_parecidos(cuit: str, texto: str = "") -> list[str]:
    """CUIT válidos que difieren en un dígito que el OCR suele confundir. Primero
    los que se parecen a otros números del texto (Ingresos Brutos suele ser el
    mismo CUIT, y cada lectura se equivoca en un dígito distinto)."""
    candidatos = []
    for _, c in _variantes(cuit):
        if cuit_valido(c) and c not in candidatos:
            candidatos.append(c)
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


def _verificar_totales(t: dict[str, float | None], suma_items: float | None = None) -> list[Verificacion]:
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
        if iva and not sugerida:
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
        if not sugerida and neto:
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
    if neto and iva and not any(x.nivel == "error" for x in v):
        if (a := _alicuota_de(neto, iva)) is not None:
            v.append(Verificacion(campo="iva", nivel="ok", mensaje=f"El IVA es el {a:g}% del neto gravado.", titulo=f"IVA {a:g}%"))
        elif iva > neto * 0.27 + TOLERANCIA:
            v.append(Verificacion(campo="iva", nivel="aviso", mensaje=f"El IVA ({_fmt(iva)}) es más del 27% del neto: revisalo.", titulo="IVA demasiado alto", datos=[_fmt(iva)]))
        else:
            v.append(Verificacion(campo="iva", nivel="aviso", mensaje="El IVA no es una sola alícuota del neto (puede tener varias): revisalo.", titulo="IVA con varias alícuotas"))
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
                         "con ese valor el detalle suma el neto (en naranja en el detalle). Si no es así, cambialo a mano."),
                titulo=f"Autocorregí el importe del ítem {n + 1}",
                datos=[f"Leído {_fmt(x.importe_leido)}", f"Ahora {_fmt(x.importe)}"],
            ))
    return aviso + _verificar_items_sin_correccion(items, t)


def _verificar_iva_por_alicuota(items: list[Item], t: dict[str, float | None]) -> Verificacion | None:
    """Xubio calcula el IVA de cada fila (importe x alícuota). Con el detalle agrupado por
    alícuota, ¿da el IVA de la factura? Unos centavos de diferencia se ajustan al cargar."""
    if not items or t.get("iva") is None or any(i.importe is None or i.alicuota is None for i in items):
        return None
    calculado = round(sum(round(suma * (a or 0) / 100, 2) for a, suma, _ in agrupar_items(items)), 2)
    dif = round(t["iva"] - calculado, 2)
    if not dif or abs(dif) > 0.05:
        return None
    return Verificacion(
        campo="iva", nivel="aviso", titulo="Redondeo de IVA",
        mensaje=(f"Xubio va a calcular un IVA de {_fmt(calculado)} y la factura dice {_fmt(t['iva'])} "
                 f"(diferencia {_fmt(dif)}): ajustá esos centavos al importar el comprobante."),
        datos=[f"Xubio {_fmt(calculado)}", f"Factura {_fmt(t['iva'])}", f"Dif. {_fmt(dif)}"],
    )


def _verificar_items_sin_correccion(items: list[Item], t: dict[str, float | None]) -> list[Verificacion]:
    if not items:
        return [Verificacion(campo="items", nivel="aviso", mensaje="No se pudo leer el detalle: el Excel va a tener solo el encabezado y los totales.", titulo="Sin detalle")]
    v: list[Verificacion] = []
    sin_importe = [i + 1 for i, x in enumerate(items) if x.importe is None]
    if sin_importe:
        v.append(Verificacion(campo="items", nivel="aviso", mensaje=f"Ítems sin importe: {', '.join(map(str, sin_importe))}.", titulo="Ítems sin importe", datos=[f"#{n}" for n in sin_importe[:6]]))
    mal_cant = [i + 1 for i, x in enumerate(items) if x.cantidad is not None and x.cantidad <= 0]
    if mal_cant:
        v.append(Verificacion(campo="items", nivel="error", mensaje=f"Ítems con cantidad 0 o negativa: {', '.join(map(str, mal_cant))}.", titulo="Cantidad mal leída", datos=[f"#{n}" for n in mal_cant[:6]]))
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
    e, t = lectura.encabezado, lectura.totales
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
    v += _verificar_totales(t, round(sum(x.importe or 0 for x in lectura.items), 2) if lectura.items else None)
    v += _verificar_items(lectura.items, t)
    if (x := _verificar_iva_por_alicuota(lectura.items, t)) is not None:
        v.append(x)
    for campo, leido in lectura.corregidos.items():
        ahora = e.get(campo, "")
        if leido and ahora and leido != ahora:
            etiqueta = ETIQUETAS_ENCABEZADO.get(campo, campo)
            v.append(Verificacion(campo=campo, nivel="aviso", titulo=f"Autocorregí el {etiqueta.lower()}",
                                  mensaje=f"{etiqueta}: se leyó «{leido}» y lo dejé como «{ahora}». Si no es así, cambialo a mano.",
                                  datos=[f"Leído {leido}", f"Ahora {ahora}"]))
    # Lo que cada pasada leyó distinto y sigue sin verificarse por otro lado.
    con_error = {x.campo for x in v if x.nivel != "ok"}
    verificados = {x.campo for x in v if x.nivel == "ok"}
    for campo, valores in lectura.dudas.items():
        etiqueta = ETIQUETAS_ENCABEZADO.get(campo) or ETIQUETAS_TOTALES.get(campo, campo)
        if campo in con_error:
            # Ya marcado como error: las lecturas distintas pasan a ser los «Usar X».
            for x in v:
                if x.campo == campo and x.nivel == "error" and not x.opciones:
                    x.titulo, x.opciones = f"{etiqueta} mal leído", _ordenar_opciones(lectura, campo, valores)[:3]
                    break
            continue
        if (campo in verificados and campo.endswith("_cuit")) or (campo in TOTALES and _cierra(t)):
            continue
        v.append(Verificacion(campo=campo, nivel="error", mensaje=f"{etiqueta}: cada lectura dio algo distinto ({' / '.join(valores[:3])}). Elegí el que figura en la imagen.",
                             titulo=f"{etiqueta} mal leído", opciones=_ordenar_opciones(lectura, campo, valores)[:3]))
    # Primero lo que hay que arreglar.
    orden = {"error": 0, "aviso": 1, "ok": 2}
    return sorted(v, key=lambda x: orden[x.nivel])


def _ordenar_opciones(lectura: Lectura, campo: str, valores: list[str]) -> list[str]:
    """Primero la recomendada: el importe con el que cierra el total o, si no, el que quedó."""
    def puntaje(v: str) -> tuple:
        if campo in TOTALES:
            n = _numero(v)
            cierra = n is not None and _cierra({**lectura.totales, campo: n})
            return (not cierra, n != lectura.totales.get(campo))
        return (False, v != lectura.encabezado.get(campo))
    return sorted(valores, key=puntaje)


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
    # Un solo producto para todo el detalle («Refrigerio»): una fila por alícuota y los
    # artículos en «PRODUCTOOBSERVACION». Vacío: una fila por ítem.
    agrupar: str = Field(default="", max_length=60)


# El modelo de Xubio «Importar comprobantes» (modelo-importacion-ventas): una fila con
# la cabecera y una por ítem, unidas por NUMERODECONTROL.
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


def filas_compras(lectura: Lectura, extras: Extras | None = None) -> list[list]:
    """Cabecera, detalle (o el detalle agrupado) y una fila por cada impuesto o percepción."""
    extras = extras or Extras()
    e, t = lectura.encabezado, lectura.totales
    fecha = _fecha_xubio(e.get("fecha", ""))
    vence = _fecha_xubio(extras.vencimiento) if extras.vencimiento else fecha
    filas = [[1, e.get("emisor", ""), _tipo_xubio(e.get("tipo", "")), _numero_xubio(lectura), fecha, vence,
              "pesos argentinos", "", fecha, Path(lectura.archivo).stem[:200]] + [None] * 8]
    detalle = []  # (producto, centro, observación, cantidad, precio, importe, alícuota)
    if extras.agrupar.strip() and lectura.items:
        centro = next((c for c in extras.centros if c), None)
        detalle = [(extras.agrupar.strip(), centro, obs, 1, suma, suma, a) for a, suma, obs in agrupar_items(lectura.items)]
    elif lectura.items:
        for n, it in enumerate(lectura.items):
            def de(lista, defecto=None):
                return lista[n] if n < len(lista) and lista[n] not in ("", None) else defecto
            detalle.append((de(extras.productos, it.producto or it.codigo or it.descripcion[:60]), de(extras.centros),
                            it.descripcion or None, it.cantidad, it.precio, it.importe, it.alicuota))
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


def _cantidad(c: float | None) -> str:
    c = 1 if c is None else c
    return str(int(c)) if float(c).is_integer() else f"{c:.3f}".rstrip("0").replace(".", ",")


def agrupar_items(items: list[Item]) -> list[tuple[float | None, float, str]]:
    """Por alícuota: (alícuota, suma de los importes netos, «1x AGUA…; 2x ENTRAÑA»), con
    las cantidades leídas. El IVA de cada alícuota queda igual."""
    grupos: dict[float | None, tuple[float, list[str]]] = {}
    for it in items:
        suma, detalle = grupos.get(it.alicuota, (0.0, []))
        detalle.append(f"{_cantidad(it.cantidad)}x {it.descripcion.strip() or 'Sin descripción'}")
        grupos[it.alicuota] = (round(suma + (it.importe or 0), 2), detalle)
    return [(a, suma, "; ".join(d)) for a, (suma, d) in grupos.items()]


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
    compras = operacion == "Compras"
    ws.title = "Hoja1" if compras else "Sheet0"
    encabezado = COLUMNAS_COMPRAS if compras else COLUMNAS_IMPORTACION
    ws.append(list(encabezado))
    for fila in filas_compras(lectura, extras) if compras else filas_importacion(lectura, operacion, extras):
        ws.append(fila)
    for letra in "EFI" if compras else "EF":
        for celda in ws[letra][1:]:
            celda.number_format = "dd/mm/yyyy"
    for letra in "OPR" if compras else "OPQ":
        for celda in ws[letra][1:]:
            celda.number_format = "#,##0.00"
    for i, c in enumerate(encabezado):
        ws.column_dimensions[chr(65 + i)].width = max(12, len(c) + 2)
    # En compras los impuestos ya van en la hoja; en ventas el modelo no los trae.
    if not compras:
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
        e, t = lectura.encabezado, lectura.totales
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
