"""Corrige el Libro IVA bajado de Xubio con los datos de ARCA que se eligieron.

El control encuentra pares ARCA / Xubio del mismo comprobante con la letra, el tipo,
el importe o la CUIT distintos. Si el usuario lo pide, se corrigen con el dato
de ARCA que se elijan («Aplicar cambios»): acá se reescriben esas celdas en el Libro
IVA de la carpeta del control. Con ese libro se vuelve a correr el cruce sobre el mismo AUX:
lo corregido deja de ser un error (y de estar en rojo). En Xubio no se toca nada.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import openpyxl

from ..core.archivos_excel import guardar_excel

CAMPOS = ("letra", "tipo", "importe", "cuit")

# Si el Libro IVA no trae ningún comprobante de esa clase para copiar cómo se escribe.
_TIPO_XUBIO = {"FC": "Factura", "NC": "Nota Crédito", "ND": "Nota Débito"}

_COMPROBANTE = re.compile(r"([A-Za-z])[-\s]*(\d+)[-\s]*(\d+)")


class LibroIlegible(Exception):
    pass


def _digitos(valor) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def _clase(tipo) -> str:
    t = str(tipo or "").lower()
    if "crédito" in t or "credito" in t:
        return "NC"
    if "débito" in t or "debito" in t:
        return "ND"
    return "FC"


def _partes(comprobante) -> tuple[str, int, int] | None:
    m = _COMPROBANTE.search(str(comprobante or ""))
    return (m.group(1).upper(), int(m.group(2)), int(m.group(3))) if m else None


def _numero(valor) -> float | None:
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor or "").replace("$", "").replace(" ", "").strip()
    if not texto:
        return None
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def _cuit_como(valor: str, modelo) -> str:
    d = re.sub(r"\D", "", valor)
    if len(d) == 11 and "-" in str(modelo or ""):
        return f"{d[:2]}-{d[2:10]}-{d[10]}"
    return d


def _cabecera(ws) -> tuple[int, dict[str, int]]:
    """La fila de títulos del Libro IVA (Xubio deja arriba «Fecha desde:», etc.)."""
    for fila in range(1, min(ws.max_row, 20) + 1):
        titulos = {
            str(ws.cell(fila, c).value or "").strip().lower(): c
            for c in range(1, ws.max_column + 1)
        }
        if "comprobante" in titulos and "fecha" in titulos:
            col = {
                "tipo": titulos.get("tipo", 2),
                "comprobante": titulos["comprobante"],
                "cuit": titulos.get("cuit", 5),
                "total": titulos.get("total", 22),
            }
            return fila, col
    raise LibroIlegible("No se encontró la fila de títulos del Libro IVA (Fecha, Comprobante…).")


def corregir_libro(origen: Path | str, destino: Path | str, cambios: list[dict]) -> dict:
    """Escribe en ``destino`` el Libro IVA de ``origen`` con los ``cambios`` aplicados.

    Cada cambio: ``campo`` (letra, tipo, importe o cuit), ``cuit`` (la del Libro IVA),
    ``pv``, ``numero``, ``valor_arca`` y, opcional, ``comprobante`` (como está en el
    Libro IVA, para elegir si hay dos con el mismo número). La fila se busca por
    CUIT + punto de venta + número.

    Devuelve ``{"aplicados": [...], "no_aplicados": [{..., "motivo"}]}``.
    """
    wb = openpyxl.load_workbook(origen)
    ws = wb.active
    fila_tit, col = _cabecera(ws)

    filas: list[tuple[int, str, tuple[str, int, int]]] = []
    tipos_por_clase: dict[str, Counter] = {}
    for r in range(fila_tit + 1, ws.max_row + 1):
        partes = _partes(ws.cell(r, col["comprobante"]).value)
        if not partes:
            continue
        filas.append((r, _digitos(ws.cell(r, col["cuit"]).value), partes))
        tipo = str(ws.cell(r, col["tipo"]).value or "").strip()
        if tipo:
            tipos_por_clase.setdefault(_clase(tipo), Counter())[tipo] += 1

    aplicados: list[dict] = []
    no_aplicados: list[dict] = []
    for cambio in cambios:
        campo = cambio.get("campo")
        if campo not in CAMPOS:
            no_aplicados.append({**cambio, "motivo": "Campo que no se puede corregir."})
            continue
        cuit = _digitos(cambio.get("cuit"))
        try:
            pv, numero = int(cambio.get("pv")), int(cambio.get("numero"))
        except (TypeError, ValueError):
            no_aplicados.append({**cambio, "motivo": "Punto de venta o número inválido."})
            continue
        candidatas = [r for r, c, (_, p, n) in filas if c == cuit and p == pv and n == numero]
        if len(candidatas) > 1 and cambio.get("comprobante"):
            letra = (_partes(cambio["comprobante"]) or ("",))[0]
            candidatas = [r for r in candidatas if (_partes(ws.cell(r, col["comprobante"]).value) or ("",))[0] == letra]
        if not candidatas:
            no_aplicados.append({**cambio, "motivo": "No está en el Libro IVA (CUIT + punto de venta + número)."})
            continue
        if len(candidatas) > 1:
            no_aplicados.append({**cambio, "motivo": "Hay más de un comprobante con ese número: corregilo a mano."})
            continue
        r = candidatas[0]
        valor = cambio.get("valor_arca")
        if campo == "letra":
            letra = str(valor or "").strip().upper()[:1]
            celda = ws.cell(r, col["comprobante"])
            if not letra.isalpha():
                no_aplicados.append({**cambio, "motivo": "La letra de ARCA no es válida."})
                continue
            antes = celda.value
            celda.value = re.sub(r"^\s*[A-Za-z]", letra, str(antes or ""), count=1)
        elif campo == "tipo":
            clase = _clase(valor)
            usados = tipos_por_clase.get(clase)
            celda = ws.cell(r, col["tipo"])
            antes = celda.value
            celda.value = usados.most_common(1)[0][0] if usados else _TIPO_XUBIO[clase]
        elif campo == "importe":
            nuevo = _numero(valor)
            celda = ws.cell(r, col["total"])
            if nuevo is None:
                no_aplicados.append({**cambio, "motivo": "El importe de ARCA no es un número."})
                continue
            antes = celda.value
            previo = _numero(antes)
            # Xubio puede guardar las notas de crédito en negativo: se respeta el signo.
            celda.value = -abs(nuevo) if previo is not None and previo < 0 else abs(nuevo)
        else:  # cuit
            d = re.sub(r"\D", "", str(valor or ""))
            celda = ws.cell(r, col["cuit"])
            if len(d) != 11:
                no_aplicados.append({**cambio, "motivo": "La CUIT de ARCA no es válida."})
                continue
            antes = celda.value
            celda.value = _cuit_como(d, antes)
        aplicados.append({**cambio, "fila": r, "antes": antes, "despues": celda.value})

    guardar_excel(wb, destino)
    return {"aplicados": aplicados, "no_aplicados": no_aplicados}

