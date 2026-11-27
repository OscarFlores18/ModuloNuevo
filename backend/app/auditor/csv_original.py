"""«Subir a Xubio» con el CSV que bajó ARCA, sin reescribirlo.

Xubio no acepta un CSV rearmado por la app: se sube el que exportó ARCA («Mis
comprobantes - Recibidos/Emitidos - … .csv», bajado en el mismo control). Como ese
CSV trae todo el período, se dejan solo las líneas de las partidas que no están en
el Libro IVA (la hoja «Faltantes en Libro IVA» del AUX), copiadas byte por byte: el
encabezado, la codificación, los separadores y los finales de línea quedan como
los mandó ARCA.

Una línea se reconoce por CUIT de la contraparte + código de tipo + punto de venta
+ número, igual en el CSV de ARCA y en la hoja del AUX.
"""

from __future__ import annotations

import csv
import io
import re

import pandas as pd

from .csv_arca import _codigo_tipo, _columna, _entero, _norm, clave_fila

Clave = tuple[str, str, int, int]


def _texto(contenido: bytes) -> str:
    for codificacion in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return contenido.decode(codificacion)
        except UnicodeDecodeError:
            continue
    return contenido.decode("latin-1", errors="replace")


def _celdas(linea: str) -> list[str]:
    separador = ";" if linea.count(";") >= linea.count(",") else ","
    return next(csv.reader([linea], delimiter=separador), [])


def _digitos(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def _numero(v) -> int | None:
    """«00014», 14, 14.0 → 14 (el Excel puede traer el número como float)."""
    d = _entero(v)
    return int(d) if d.lstrip("-").isdigit() else None


def clave(cuit, tipo, punto_venta, numero) -> Clave | None:
    pv, nro, tp = _numero(punto_venta), _numero(numero), _codigo_tipo(tipo)
    if pv is None or nro is None or not tp.isdigit():
        return None
    return (_digitos(_entero(cuit) or cuit).lstrip("0"), str(int(tp)), pv, nro)


def _indices(encabezado: list[str], operacion: str) -> dict[str, int] | None:
    parte = "emisor" if operacion == "Compras" else "receptor"
    pos = {_norm(c): i for i, c in enumerate(encabezado)}
    buscados = {
        "tipo": ("tipo de comprobante", "tipo"),
        "pv": ("punto de venta",),
        "nro": ("numero desde", "numero de comprobante", "numero"),
        "cuit": (f"nro. doc. {parte}", f"nro doc {parte}", f"cuit {parte}"),
    }
    indices = {}
    for k, nombres in buscados.items():
        i = next((pos[n] for n in nombres if n in pos), None)
        if i is None:
            return None
        indices[k] = i
    return indices


def claves_de_faltantes(tabla: pd.DataFrame, operacion: str, excluir: set[str] | None = None) -> dict[Clave, str]:
    """Las partidas de la hoja del AUX que van (sin las destildadas): clave → «CUIT|comprobante»."""
    parte = "Emisor" if operacion == "Compras" else "Receptor"
    salida: dict[Clave, str] = {}
    for _, fila in tabla.iterrows():
        visible = clave_fila(fila, operacion)
        if excluir and visible in excluir:
            continue
        k = clave(
            _entero(_columna(fila, f"Nro. Doc. {parte}")),
            _columna(fila, "Tipo", "Tipo de Comprobante"),
            _columna(fila, "Punto de Venta"),
            _columna(fila, "Número Desde"),
        )
        if k:
            salida[k] = visible
    return salida


class CsvArcaIlegible(ValueError):
    """El archivo no tiene el encabezado del CSV de Mis Comprobantes."""


def filtrar_csv_arca(contenido: bytes, operacion: str, buscadas: dict[Clave, str]) -> tuple[bytes, list[str]]:
    """El CSV de ARCA con el encabezado y solo las líneas de `buscadas`, sin tocar
    nada de cada línea. Devuelve (csv, las «CUIT|comprobante» que no se encontraron)."""
    lineas = contenido.splitlines(keepends=True)
    encabezado_i = None
    indices = None
    for i, crudo in enumerate(lineas[:10]):
        indices = _indices(_celdas(_texto(crudo).strip("\r\n")), operacion)
        if indices:
            encabezado_i = i
            break
    if encabezado_i is None or indices is None:
        raise CsvArcaIlegible("El CSV de ARCA no tiene el encabezado de Mis Comprobantes.")

    salida = lineas[: encabezado_i + 1]
    encontradas: set[Clave] = set()
    for crudo in lineas[encabezado_i + 1 :]:
        celdas = _celdas(_texto(crudo).strip("\r\n"))
        if len(celdas) <= max(indices.values()):
            continue  # vacía o el encabezado repetido de otra parte de la descarga
        k = clave(celdas[indices["cuit"]], celdas[indices["tipo"]], celdas[indices["pv"]], celdas[indices["nro"]])
        if k in buscadas and k not in encontradas:
            encontradas.add(k)
            salida.append(crudo)
    # La última línea copiada puede no tener salto (era la última del archivo).
    if len(salida) > encabezado_i + 1 and not salida[-1].endswith((b"\n", b"\r")):
        fin = re.search(rb"\r?\n", salida[0])
        salida[-1] += fin.group(0) if fin else b"\n"
    faltan = [v for k, v in buscadas.items() if k not in encontradas]
    return b"".join(salida), faltan


def unir_partes(partes: list[bytes]) -> bytes:
    """Las partes que bajó ARCA (un período largo se pide en tramos) en un solo
    CSV: el encabezado una vez y las filas de cada parte tal cual."""
    if not partes:
        return b""
    unido = partes[0]
    primera = partes[0].splitlines(keepends=True)
    encabezado = primera[0].strip() if primera else b""
    for parte in partes[1:]:
        lineas = parte.splitlines(keepends=True)
        if lineas and lineas[0].strip() == encabezado:
            lineas = lineas[1:]
        if unido and not unido.endswith((b"\n", b"\r")) and lineas:
            unido += b"\r\n" if b"\r\n" in unido else b"\n"
        unido += b"".join(lineas)
    return unido
