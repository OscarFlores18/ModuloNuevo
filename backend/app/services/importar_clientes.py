"""Cargar varios clientes de una vez desde un JSON (Clientes > «Cargar varios»).

Formatos que se aceptan (las claves sin importar mayúsculas):

    {"CLIENTES": {"ACME S.A.": {"ARCA": {"cuit_login": "...", "clave": "...", "cuit_representado": "..."}}}}
    {"ACME S.A.": {"arca": {...}}}                      # sin «CLIENTES»
    {"ACME S.A.": {"cuit_login": "...", ...}}           # sin «ARCA»
    [{"nombre": "ACME S.A.", "arca": {...}}]            # una lista

Si el texto no es JSON válido se intenta arreglar lo típico de escribirlo a mano:
`{CLIENTES{...` (sin comillas ni dos puntos), llaves sin cerrar y comas de más.
Nunca se registra el texto: trae claves fiscales.
"""

from __future__ import annotations

import json
import re

MAX_CLIENTES = 500


class JsonInvalido(ValueError):
    pass


def _cerrar_llaves(texto: str) -> str:
    """Agrega las llaves/corchetes que falten al final (fuera de las cadenas)."""
    pila: list[str] = []
    en_cadena = escape = False
    for ch in texto:
        if en_cadena:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                en_cadena = False
            continue
        if ch == '"':
            en_cadena = True
        elif ch in "{[":
            pila.append("}" if ch == "{" else "]")
        elif ch in "}]" and pila and pila[-1] == ch:
            pila.pop()
    return texto + "".join(reversed(pila))


def _reparar(texto: str) -> str:
    t = texto.strip().lstrip("﻿")
    # {CLIENTES{  /  {"CLIENTES"{  /  {CLIENTES: {
    t = re.sub(r'^\{\s*"?clientes"?\s*:?\s*\{', '{"CLIENTES": {', t, flags=re.IGNORECASE)
    # Comas antes de un cierre.
    t = re.sub(r",\s*([}\]])", r"\1", t)
    t = _cerrar_llaves(t)
    return re.sub(r",\s*([}\]])", r"\1", t)


def _parsear(texto: str):
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_reparar(texto))
    except json.JSONDecodeError as exc:
        raise JsonInvalido(
            f"El JSON no es válido (línea {exc.lineno}, columna {exc.colno}). "
            'Revisá comillas, dos puntos y llaves: {"CLIENTES": {"NOMBRE": {"ARCA": {...}}}}'
        ) from None


def _sin_mayusculas(d: dict) -> dict:
    return {str(k).strip().lower(): v for k, v in d.items()}


def _cliente(nombre: str, datos) -> dict:
    if not isinstance(datos, dict):
        raise JsonInvalido(f"«{nombre}»: se esperaba un objeto con los datos de ARCA.")
    d = _sin_mayusculas(datos)
    arca = d.get("arca")
    if not isinstance(arca, dict):
        # Los datos de ARCA directo en el cliente.
        arca = {k: v for k, v in d.items() if k in ("cuit_login", "clave", "cuit_representado")}
    arca = _sin_mayusculas(arca)
    extras = {k: v for k, v in d.items() if k not in ("arca", "nombre", "xubio", "cuit_login", "clave", "cuit_representado")}
    return {
        "nombre": str(nombre).strip(),
        "arca": {
            "cuit_login": str(arca.get("cuit_login") or "").strip(),
            "clave": str(arca.get("clave") or ""),
            "cuit_representado": str(arca.get("cuit_representado") or "").strip(),
        },
        "extras": extras,
    }


def leer(texto: str) -> list[dict]:
    """[{nombre, arca: {cuit_login, clave, cuit_representado}, extras}] o JsonInvalido."""
    if not (texto or "").strip():
        raise JsonInvalido("Pegá el JSON o elegí un archivo .json.")
    datos = _parsear(texto)
    if isinstance(datos, dict):
        d = _sin_mayusculas(datos)
        if isinstance(d.get("clientes"), (dict, list)) and len(d) == 1:
            datos = d["clientes"]
    if isinstance(datos, dict):
        clientes = [_cliente(n, v) for n, v in datos.items()]
    elif isinstance(datos, list):
        clientes = []
        for i, item in enumerate(datos, start=1):
            if not isinstance(item, dict) or not str(_sin_mayusculas(item).get("nombre") or "").strip():
                raise JsonInvalido(f"El elemento {i} de la lista no tiene «nombre».")
            clientes.append(_cliente(_sin_mayusculas(item)["nombre"], item))
    else:
        raise JsonInvalido("Se esperaba un objeto con los clientes.")
    if not clientes:
        raise JsonInvalido("El JSON no tiene clientes.")
    if len(clientes) > MAX_CLIENTES:
        raise JsonInvalido(f"Son {len(clientes)} clientes: cargá hasta {MAX_CLIENTES} por vez.")
    return clientes
