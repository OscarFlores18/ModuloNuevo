"""Lo que usan los endpoints: extraer comprobantes de los archivos y, ya
revisados, prepararlos para Xubio con el mismo traspaso de «Convertir AUX»."""

from __future__ import annotations

import io
import logging
import re
from collections import Counter
from pathlib import Path

import pandas as pd

from ..auditor.csv_arca import PREFIJO_CSV, detectar_faltantes, excel_de, filas_de_tabla, zip_de_operacion
from .documentos import extraer_imagen, extraer_pdf
from .modelo import Comprobante, completar, fila_arca, norm, validar
from .planillas import extraer_planilla

logger = logging.getLogger(__name__)

PLANILLAS = {".xlsx", ".xlsm", ".xls", ".csv", ".txt"}
IMAGENES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
FORMATOS = PLANILLAS | IMAGENES | {".pdf"}


def es_aux(nombre: str, contenido: bytes) -> bool:
    """Un AUX del control (hoja «Faltantes…»): va por «Convertir AUX» como siempre."""
    if Path(nombre).suffix.lower() not in (".xlsx", ".xlsm"):
        return False
    try:
        return bool(detectar_faltantes(io.BytesIO(contenido)))
    except Exception:
        return False


def _cuits_de_clientes(clientes: dict[str, dict]) -> dict[str, str]:
    """CUIT -> nombre del cliente (el representado y el de ingreso)."""
    salida: dict[str, str] = {}
    for nombre, datos in clientes.items():
        arca = datos.get("arca") or {}
        for c in (arca.get("cuit_representado"), arca.get("cuit_login")):
            c = re.sub(r"\D", "", str(c or ""))
            if len(c) == 11:
                salida.setdefault(c, nombre)
    return salida


def _cliente_por_nombre(texto: str, clientes: dict[str, dict]) -> str:
    """El cliente cuyo nombre aparece en el nombre del archivo: entero o, si no,
    su primera palabra distintiva («compras PICCININI.pdf» -> «PICCININI y ASOCIADOS
    S.A.»). Si la palabra es de más de un cliente, no se elige ninguno."""
    t = norm(Path(texto).stem)
    candidatos = [n for n in clientes if norm(n) and norm(n) in t]
    if candidatos:
        return max(candidatos, key=len)
    palabras = set(re.findall(r"[a-z0-9]+", t))
    por_palabra = [n for n in clientes if (p := _palabra_distintiva(n)) and p in palabras]
    return por_palabra[0] if len(por_palabra) == 1 else ""


_GENERICAS = {"sociedad", "anonima", "asociados", "comercial", "servicios", "grupo", "empresa", "compania", "materiales", "consulting"}


def _palabra_distintiva(nombre: str) -> str:
    for p in re.findall(r"[a-z0-9]+", norm(nombre)):
        if len(p) >= 4 and p not in _GENERICAS:
            return p
    return ""


def asignar_cliente(c: Comprobante, cuits: dict[str, str], clientes: dict[str, dict]) -> None:
    """Venta si el emisor es un cliente del estudio; compra si lo es el receptor."""
    if c.emisor.doc_nro in cuits and c.operacion in (None, "Ventas"):
        c.operacion, c.cliente = "Ventas", cuits[c.emisor.doc_nro]
    elif c.receptor.doc_nro in cuits and c.operacion in (None, "Compras"):
        c.operacion, c.cliente = "Compras", cuits[c.receptor.doc_nro]
    if not c.cliente:
        c.cliente = _cliente_por_nombre(c.archivo, clientes)


def extraer_archivo(nombre: str, contenido: bytes) -> tuple[list[Comprobante], list[str]]:
    sufijo = Path(nombre).suffix.lower()
    if sufijo in PLANILLAS:
        return extraer_planilla(nombre, contenido), []
    if sufijo == ".pdf":
        return extraer_pdf(nombre, contenido)
    if sufijo in IMAGENES:
        return extraer_imagen(nombre, contenido)
    raise ValueError(f"«{nombre}»: formato no soportado (Excel, CSV, PDF, JPG o PNG).")


def extraer(archivos: list[tuple[str, bytes]], clientes: dict[str, dict]) -> dict:
    """{archivos: [{nombre, tipo: aux|comprobantes|error, cantidad, avisos}], comprobantes: [...]}.

    Los AUX no se leen acá: la pantalla los manda a «Convertir AUX»."""
    cuits = _cuits_de_clientes(clientes)
    informe: list[dict] = []
    todos: list[Comprobante] = []
    for nombre, contenido in archivos:
        nombre = Path(nombre).name
        if es_aux(nombre, contenido):
            informe.append({"nombre": nombre, "tipo": "aux", "cantidad": 0, "avisos": []})
            continue
        try:
            comprobantes, avisos = extraer_archivo(nombre, contenido)
        except ValueError as exc:
            informe.append({"nombre": nombre, "tipo": "error", "cantidad": 0, "avisos": [str(exc)]})
            continue
        except Exception as exc:
            logger.exception("No se pudo leer %s", nombre)
            informe.append({"nombre": nombre, "tipo": "error", "cantidad": 0, "avisos": [f"No se pudo leer «{nombre}»: {exc}"]})
            continue
        if not comprobantes:
            # Que el archivo no se pierda: una fila vacía para cargarla a mano.
            avisos.append("No se encontraron datos de un comprobante: completalo a mano o sacalo.")
            comprobantes = [Comprobante(archivo=nombre, metodo="manual")]
        for i, c in enumerate(comprobantes, start=1):
            c.id = f"{nombre}#{i}"
            completar(c)
            asignar_cliente(c, cuits, clientes)
            c.avisos = validar(c)
        informe.append({"nombre": nombre, "tipo": "comprobantes", "cantidad": len(comprobantes), "avisos": avisos})
        todos += comprobantes
    return {"archivos": informe, "comprobantes": [c.model_dump() for c in todos]}


def _periodo(comprobantes: list[Comprobante]) -> str:
    meses = Counter(f"{c.fecha[5:7]}-{c.fecha[2:4]}" for c in comprobantes if re.fullmatch(r"\d{4}-\d{2}-\d{2}", c.fecha))
    return meses.most_common(1)[0][0] if meses else ""


def preparar(comprobantes: list[Comprobante]) -> list[dict]:
    """Los comprobantes revisados, por cliente: el mismo resultado que «Convertir
    AUX» (un .zip por operación con el CSV de Mis Comprobantes y la copia en
    Excel), así el paso «Subilos a Xubio» y la carga del bot no cambian."""
    grupos: dict[str, dict] = {}
    for c in comprobantes:
        if not c.incluir:
            continue
        completar(c)
        g = grupos.setdefault(c.cliente.casefold(), {"cliente": c.cliente, "comprobantes": [], "faltan": []})
        g["comprobantes"].append(c)
        # Lo que impide armar el CSV (lo demás son avisos para mirar).
        if not c.operacion or not c.cliente:
            g["faltan"].append(c)

    resultados: list[dict] = []
    for g in grupos.values():
        lista: list[Comprobante] = g["comprobantes"]
        archivos = sorted({c.archivo for c in lista if c.archivo})
        item = {"cliente": g["cliente"] or "Sin cliente", "periodo": _periodo(lista), "archivos": archivos,
                "partidas": {}, "filas": {}, "zips": {}, "excel": None, "avisos": [], "error": None}
        resultados.append(item)
        if g["faltan"]:
            n = len(g["faltan"])
            item["error"] = (f"{n} {'comprobante' if n == 1 else 'comprobantes'} sin cliente o sin elegir venta/compra: "
                             "completalos en la revisión.")
            continue
        tablas = {
            op: pd.DataFrame([fila_arca(c) for c in lista if c.operacion == op])
            for op in ("Ventas", "Compras")
            if any(c.operacion == op for c in lista)
        }
        sufijo = f" - {item['periodo']}" if item["periodo"] else ""
        try:
            item["zips"] = {
                op: {"nombre": f"{PREFIJO_CSV} {op} - {item['cliente']}{sufijo}.zip",
                     "contenido": zip_de_operacion(t, op, item["cliente"], item["periodo"])}
                for op, t in tablas.items()
            }
            item["excel"] = {"nombre": f"{PREFIJO_CSV} - {item['cliente']}{sufijo}.xlsx", "contenido": excel_de(tablas)}
        except Exception as exc:
            logger.exception("No se pudo armar el CSV de %s", item["cliente"])
            item["zips"], item["error"] = {}, f"No se pudo armar el archivo: {exc}"
            continue
        item["partidas"] = {op: len(t) for op, t in tablas.items()}
        item["filas"] = {op: filas_de_tabla(t, op) for op, t in tablas.items()}
    return resultados
