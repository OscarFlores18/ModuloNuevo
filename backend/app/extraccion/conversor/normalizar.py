"""Paso 2, normalizar: lo leído (texto del OCR o JSON de Gemini) a la `Lectura`
limpia: encabezado, ítems, totales, alícuotas y tributos, con las correcciones.
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
from .datos import ALICUOTAS, AlicuotaIva, ETIQUETAS_ENCABEZADO, ETIQUETAS_TOTALES, Hoja, Item, Lectura, MAX_FILAS, TOLERANCIA, TOTALES, Tributo

logger = logging.getLogger(__name__)


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
    if v := _vencimiento_de_texto(texto):
        e["vencimiento"] = v
    return e


def _vencimiento_de_texto(texto: str) -> str | None:
    """«Fecha de Vto. para el pago: 15/12/2026» o «Vencimiento: …»; nunca el «Vto. de CAE»."""
    for m in re.finditer(r"(?:\bvto\b|\bvenc\w*)([^\d\n]{0,30})(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})", texto, re.IGNORECASE):
        if "cae" in m.group(1).lower():
            continue
        iso = fecha_iso(m.group(2))
        return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso else m.group(2)
    return None


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
    if v := str(_primero(g, "fecha_vencimiento", "vencimiento") or "").strip():
        iso = fecha_iso(v)
        e["vencimiento"] = f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso else v
    # El CAE no se muestra, pero se guarda: lo pide la importación de Xubio.
    if len(cae := re.sub(r"\D", "", str(_primero(g, "cae", "codigo_autorizacion") or ""))) == 14:
        e["cae"] = cae
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
        # Las advertencias de Gemini no se muestran: lo que no cierra ya lo dicen las cuentas.
        if metodo != "ia" and "cambiando 0 y 8" in advertencia:
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
        # Con renglones, los renglones mandan: un tributo borrado deja de sumar.
        for g in GRUPOS_TRIBUTO:
            t[g] = grupos.get(g) or None
    return t


GRUPOS_TRIBUTO = ("impuestos_internos", "percepcion_iibb", "otros_tributos")


def _tributos_sueltos_a_renglones(lectura: Lectura) -> None:
    """Si vinieron renglones de tributos y además un total suelto de un grupo sin
    renglón (p. ej. impuestos internos leídos aparte), ese total pasa a ser un
    renglón: así los renglones tienen todo y nada se pierde ni se cuenta dos veces."""
    if not lectura.tributos:
        return
    grupos = {grupo_tributo(x) for x in lectura.tributos}
    # «Otros tributos» suelto no: casi siempre es «Importe total otros tributos», la
    # suma de los renglones (y si el OCR lo leyó mal, inventaría un tributo).
    for g in ("impuestos_internos", "percepcion_iibb"):
        suelto = lectura.totales.get(g)
        if not suelto or g in grupos:
            continue
        lectura.tributos.append(Tributo(descripcion=ETIQUETAS_TOTALES[g], importe=suelto))


def completar_desglose(lectura: Lectura) -> Lectura:
    """Sin desglose (OCR, PDF): una fila con el IVA leído, en la alícuota que da
    con el neto si da alguna. Con desglose, el IVA total es la suma de las filas."""
    if lectura.tipo != "comprobante":
        return lectura
    _tributos_sueltos_a_renglones(lectura)
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


def _aplicar_qr(lectura: Lectura, qr: dict) -> Lectura:
    """Los datos del QR de ARCA son exactos: mandan sobre lo leído."""
    from ..modelo import fecha as fecha_iso_qr

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
    # El CAE no se muestra, pero se guarda (lo pide la importación de Xubio).
    if len(cae := re.sub(r"\D", "", str(qr.get("codAut") or ""))) == 14:
        lectura.encabezado["cae"] = cae
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


def _fmt_alicuota(a: float | None) -> str:
    return "" if a is None else f"{a:g}".replace(".", ",") + "%"
