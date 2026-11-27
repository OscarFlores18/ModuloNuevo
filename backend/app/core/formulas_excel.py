"""Calcula fórmulas de Excel para la vista previa.

Los Excel que arma la auditoría (openpyxl) guardan las fórmulas sin el
resultado: Excel las calcula al abrir, pero la vista previa las veía vacías
(«IVA Ajustado», «Diferencia IVA»...). Esto alcanza para las fórmulas que usa
el control: referencias (también a otras hojas), rangos, + - * / ^ & y
comparaciones, y las funciones de ``_FUNCIONES``. Lo que no entiende queda vacío.
"""

from __future__ import annotations

import math
import re
from typing import Callable

from openpyxl.utils import column_index_from_string


class ErrorXL(str):
    """#N/A, #VALUE!, #DIV/0!... como valor de celda (Excel los propaga)."""


NA = ErrorXL("#N/A")
VALOR = ErrorXL("#VALUE!")
DIV0 = ErrorXL("#DIV/0!")
REF = ErrorXL("#REF!")


class NoSoportada(Exception):
    """La fórmula usa algo que este evaluador no conoce."""


# --- Tokens ----------------------------------------------------------------

_CELDA = r"\$?[A-Za-z]{1,3}\$?\d+"
_TOKEN = re.compile(
    r"""\s*(?:
      (?P<texto>"(?:[^"]|"")*")
    | (?P<ref>(?:(?:'(?:[^']|'')+'|[A-Za-z_][\w\.]*)!)?""" + _CELDA + r"(?::" + _CELDA + r""")?)(?![\w(])
    | (?P<func>[A-Za-z_][\w\.]*)\(
    | (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
    | (?P<bool>TRUE|FALSE|VERDADERO|FALSO)(?![\w(])
    | (?P<op><>|>=|<=|[-+*/^&=<>(),%:;])
    )""",
    re.VERBOSE,
)


def _tokens(formula: str) -> list[tuple[str, str]]:
    salida, pos = [], 0
    while pos < len(formula):
        if formula[pos:].strip() == "":
            break
        m = _TOKEN.match(formula, pos)
        if not m:
            raise NoSoportada(formula[pos:pos + 20])
        tipo = m.lastgroup
        salida.append((tipo, m.group(tipo)))
        pos = m.end()
    return salida


# --- Parser (precedencia de Excel) --------------------------------------------


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.t = tokens
        self.i = 0

    def ver(self) -> tuple[str, str] | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def tomar(self, valor: str | None = None) -> tuple[str, str]:
        tok = self.ver()
        if tok is None or (valor is not None and tok[1] != valor):
            raise NoSoportada(f"se esperaba {valor}")
        self.i += 1
        return tok

    def es_op(self, *ops: str) -> bool:
        tok = self.ver()
        return tok is not None and tok[0] == "op" and tok[1] in ops

    def expresion(self):
        izq = self.concatenacion()
        while self.es_op("=", "<>", "<", ">", "<=", ">="):
            op = self.tomar()[1]
            izq = ("cmp", op, izq, self.concatenacion())
        return izq

    def concatenacion(self):
        izq = self.suma()
        while self.es_op("&"):
            self.tomar()
            izq = ("&", izq, self.suma())
        return izq

    def suma(self):
        izq = self.producto()
        while self.es_op("+", "-"):
            op = self.tomar()[1]
            izq = ("arit", op, izq, self.producto())
        return izq

    def producto(self):
        izq = self.potencia()
        while self.es_op("*", "/"):
            op = self.tomar()[1]
            izq = ("arit", op, izq, self.potencia())
        return izq

    def potencia(self):
        izq = self.unario()
        while self.es_op("^"):
            self.tomar()
            izq = ("arit", "^", izq, self.unario())
        return izq

    def unario(self):
        if self.es_op("-"):
            self.tomar()
            return ("neg", self.unario())
        if self.es_op("+"):
            self.tomar()
            return self.unario()
        nodo = self.primario()
        while self.es_op("%"):
            self.tomar()
            nodo = ("arit", "/", nodo, ("num", 100.0))
        return nodo

    def primario(self):
        tok = self.tomar()
        tipo, valor = tok
        if tipo == "num":
            return ("num", float(valor))
        if tipo == "texto":
            return ("texto", valor[1:-1].replace('""', '"'))
        if tipo == "bool":
            return ("bool", valor in ("TRUE", "VERDADERO"))
        if tipo == "ref":
            return ("ref", valor)
        if tipo == "func":
            args = []
            if not self.es_op(")"):
                args.append(self.argumento())
                while self.es_op(",", ";"):
                    self.tomar()
                    args.append(self.argumento())
            self.tomar(")")
            return ("func", valor.upper(), args)
        if tipo == "op" and valor == "(":
            nodo = self.expresion()
            self.tomar(")")
            return nodo
        raise NoSoportada(valor)

    def argumento(self):
        # Argumento vacío: «IF(A1,,2)».
        if self.es_op(",", ";", ")"):
            return ("vacio",)
        return self.expresion()


# --- Valores -------------------------------------------------------------------


def _es_error(v) -> bool:
    return isinstance(v, ErrorXL)


def _numero(v):
    """Coerción de Excel para operar: vacío → 0, VERDADERO → 1, «12» → 12."""
    if _es_error(v):
        return v
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip().replace(",", "."))
        except ValueError:
            return VALOR
    return VALOR


def _texto(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _plano(valores) -> list:
    salida = []
    for v in valores:
        if isinstance(v, list):
            salida.extend(_plano(v))
        else:
            salida.append(v)
    return salida


def _comparar(op: str, a, b):
    if _es_error(a):
        return a
    if _es_error(b):
        return b
    if a is None:
        a = "" if isinstance(b, str) else 0.0
    if b is None:
        b = "" if isinstance(a, str) else 0.0
    if isinstance(a, str) and isinstance(b, str):
        a, b = a.lower(), b.lower()
    elif isinstance(a, str) or isinstance(b, str):
        # Excel: los textos son «mayores» que cualquier número.
        a, b = (1, 0) if isinstance(a, str) else (0, 1)
    else:
        a, b = float(a), float(b)
    return {
        "=": a == b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b,
    }[op]


def _verdad(v):
    if _es_error(v):
        return v
    if isinstance(v, str):
        if v.upper() in ("TRUE", "VERDADERO"):
            return True
        if v.upper() in ("FALSE", "FALSO"):
            return False
        return VALOR
    n = _numero(v)
    return n if _es_error(n) else n != 0


# --- Funciones ---------------------------------------------------------------------


def _f_if(ev, args):
    cond = _verdad(ev(args[0]))
    if _es_error(cond):
        return cond
    if cond:
        return ev(args[1]) if len(args) > 1 else True
    return ev(args[2]) if len(args) > 2 else False


def _f_iferror(ev, args):
    v = ev(args[0])
    return ev(args[1]) if _es_error(v) else v


def _f_ifna(ev, args):
    v = ev(args[0])
    return ev(args[1]) if v == NA and _es_error(v) else v


def _f_search(ev, args):
    buscado, dentro = _texto(ev(args[0])), _texto(ev(args[1]))
    desde = int(_numero(ev(args[2]))) if len(args) > 2 else 1
    # SEARCH acepta comodines ? y *; FIND no (y distingue mayúsculas).
    patron = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in buscado)
    m = re.compile(patron, re.IGNORECASE).search(dentro, max(desde - 1, 0))
    return float(m.start() + 1) if m else VALOR


def _f_find(ev, args):
    buscado, dentro = _texto(ev(args[0])), _texto(ev(args[1]))
    desde = int(_numero(ev(args[2]))) if len(args) > 2 else 1
    pos = dentro.find(buscado, max(desde - 1, 0))
    return float(pos + 1) if pos >= 0 else VALOR


def _f_logica(todos: bool):
    def f(ev, args):
        valores = []
        for a in args:
            v = ev(a)
            for x in _plano(v if isinstance(v, list) else [v]):
                if x is None or (isinstance(x, str) and not _es_error(x) and isinstance(v, list)):
                    continue
                b = _verdad(x)
                if _es_error(b):
                    return b
                valores.append(b)
        return all(valores) if todos else any(valores)

    return f


def _f_sum(ev, args):
    total = 0.0
    for a in args:
        v = ev(a)
        if isinstance(v, list):
            for x in _plano(v):
                if _es_error(x):
                    return x
                if isinstance(x, (int, float)) and not isinstance(x, bool):
                    total += x
        else:
            n = _numero(v)
            if _es_error(n):
                return n
            total += n
    return total


def _f_vlookup(ev, args):
    buscado = ev(args[0])
    if _es_error(buscado):
        return buscado
    tabla = ev(args[1])
    columna = _numero(ev(args[2]))
    if _es_error(columna):
        return columna
    aproximado = _verdad(ev(args[3])) if len(args) > 3 and args[3] != ("vacio",) else True
    if not isinstance(tabla, list):
        tabla = [[tabla]]
    col = int(columna) - 1
    if col < 0 or (tabla and col >= len(tabla[0])):
        return REF
    if aproximado is True:
        candidato = None
        for fila in tabla:
            r = _comparar("<=", fila[0], buscado)
            if r is True and fila[0] is not None:
                candidato = fila
            elif r is False:
                break
        return candidato[col] if candidato is not None else NA
    for fila in tabla:
        if _comparar("=", fila[0], buscado) is True and fila[0] is not None:
            return fila[col]
    return NA


def _f_text(ev, args):
    v, formato = ev(args[0]), _texto(ev(args[1]))
    if _es_error(v):
        return v
    n = _numero(v)
    if _es_error(n):
        return _texto(v)
    if re.fullmatch(r"0+", formato):
        return f"{int(round(n)):0{len(formato)}d}"
    m = re.fullmatch(r"0+\.(0+)", formato)
    if m:
        return f"{n:.{len(m.group(1))}f}"
    return _texto(v)


def _uno(f: Callable):
    """Función de un argumento que propaga errores."""

    def g(ev, args):
        v = ev(args[0])
        return v if _es_error(v) else f(v)

    return g


def _f_round(ev, args):
    n, d = _numero(ev(args[0])), _numero(ev(args[1])) if len(args) > 1 else 0.0
    if _es_error(n):
        return n
    if _es_error(d):
        return d
    factor = 10 ** int(d)
    return math.floor(abs(n) * factor + 0.5) / factor * (1 if n >= 0 else -1)


def _f_extremo(f):
    def g(ev, args):
        numeros = []
        for a in args:
            v = ev(a)
            for x in _plano(v if isinstance(v, list) else [v]):
                if _es_error(x):
                    return x
                if isinstance(x, (int, float)) and not isinstance(x, bool):
                    numeros.append(float(x))
        return f(numeros) if numeros else 0.0

    return g


def _f_left(ev, args):
    t = ev(args[0])
    n = int(_numero(ev(args[1]))) if len(args) > 1 else 1
    return t if _es_error(t) else _texto(t)[:n]


def _f_right(ev, args):
    t = ev(args[0])
    n = int(_numero(ev(args[1]))) if len(args) > 1 else 1
    return t if _es_error(t) else (_texto(t)[-n:] if n > 0 else "")


def _f_mid(ev, args):
    t, desde, n = ev(args[0]), int(_numero(ev(args[1]))), int(_numero(ev(args[2])))
    return t if _es_error(t) else _texto(t)[desde - 1:desde - 1 + n]


def _f_concat(ev, args):
    partes = []
    for a in args:
        v = ev(a)
        for x in _plano(v if isinstance(v, list) else [v]):
            if _es_error(x):
                return x
            partes.append(_texto(x))
    return "".join(partes)


def _f_countif(ev, args):
    rango, criterio = ev(args[0]), ev(args[1])
    op, valor = "=", criterio
    if isinstance(criterio, str):
        m = re.match(r"^(<>|>=|<=|=|<|>)(.*)$", criterio)
        if m:
            op, valor = m.group(1), m.group(2)
            n = _numero(valor)
            valor = valor if _es_error(n) else n
    return float(sum(1 for x in _plano(rango if isinstance(rango, list) else [rango]) if _comparar(op, x, valor) is True))


_FUNCIONES: dict[str, Callable] = {
    "IF": _f_if, "SI": _f_if,
    "IFERROR": _f_iferror, "SI.ERROR": _f_iferror,
    "IFNA": _f_ifna,
    # Las IS… no propagan el error: ISNUMBER(#VALUE!) es FALSO.
    "ISNUMBER": lambda ev, a: isinstance(v := ev(a[0]), (int, float)) and not isinstance(v, bool),
    "ISTEXT": lambda ev, a: isinstance(v := ev(a[0]), str) and not _es_error(v),
    "ISBLANK": lambda ev, a: ev(a[0]) is None,
    "ISERR": lambda ev, a: _es_error(v := ev(a[0])) and v != NA,
    "ISERROR": lambda ev, a: _es_error(ev(a[0])),
    "ISNA": lambda ev, a: ev(a[0]) == NA,
    "SEARCH": _f_search, "FIND": _f_find,
    "AND": _f_logica(True), "OR": _f_logica(False),
    "NOT": lambda ev, a: (lambda b: b if _es_error(b) else not b)(_verdad(ev(a[0]))),
    "ABS": _uno(lambda v: (lambda n: n if _es_error(n) else abs(n))(_numero(v))),
    "SUM": _f_sum,
    "VLOOKUP": _f_vlookup, "BUSCARV": _f_vlookup,
    "TEXT": _f_text,
    "ROUND": _f_round,
    "MAX": _f_extremo(max), "MIN": _f_extremo(min),
    "LEFT": _f_left, "RIGHT": _f_right, "MID": _f_mid,
    "LEN": _uno(lambda v: float(len(_texto(v)))),
    "TRIM": _uno(lambda v: " ".join(_texto(v).split())),
    "UPPER": _uno(lambda v: _texto(v).upper()),
    "LOWER": _uno(lambda v: _texto(v).lower()),
    "VALUE": _uno(_numero),
    "CONCATENATE": _f_concat, "CONCAT": _f_concat,
    "COUNTIF": _f_countif,
    "NA": lambda ev, a: NA,
}


# --- Libro ---------------------------------------------------------------------------


def _partir_ref(ref: str, hoja_actual: str) -> tuple[str, str]:
    if "!" in ref:
        hoja, celdas = ref.rsplit("!", 1)
        if hoja.startswith("'"):
            hoja = hoja[1:-1].replace("''", "'")
        return hoja, celdas
    return hoja_actual, ref


def _coordenada(texto: str) -> tuple[int, int]:
    m = re.fullmatch(r"\$?([A-Za-z]{1,3})\$?(\d+)", texto)
    if not m:
        raise NoSoportada(texto)
    return int(m.group(2)), column_index_from_string(m.group(1).upper())


class Libro:
    """Celdas de un libro (valores o fórmulas) y el cálculo, con memoria.

    ``celdas[hoja][(fila, columna)]`` es el valor crudo: un número, texto, fecha
    o, si empieza con «=», la fórmula.
    """

    MAX_RANGO = 200_000

    def __init__(self, celdas: dict[str, dict[tuple[int, int], object]]) -> None:
        self.celdas = celdas
        self._hojas = {h.lower(): h for h in celdas}
        self._calculado: dict[tuple[str, int, int], object] = {}
        self._en_curso: set[tuple[str, int, int]] = set()
        self._arboles: dict[str, object] = {}

    def valor(self, hoja: str, fila: int, columna: int):
        """Valor que mostraría Excel; ``None`` si la fórmula no se pudo calcular."""
        clave = (hoja, fila, columna)
        if clave in self._calculado:
            return self._calculado[clave]
        crudo = self.celdas.get(hoja, {}).get((fila, columna))
        if not (isinstance(crudo, str) and crudo.startswith("=")):
            return crudo
        if clave in self._en_curso:
            return REF  # referencia circular
        self._en_curso.add(clave)
        try:
            arbol = self._arboles.get(crudo)
            if arbol is None:
                parser = _Parser(_tokens(crudo[1:]))
                arbol = parser.expresion()
                if parser.ver() is not None:
                    raise NoSoportada(crudo)
                self._arboles[crudo] = arbol
            resultado = self._evaluar(arbol, hoja)
            if isinstance(resultado, list):  # rango suelto: Excel muestra la primera celda
                resultado = _plano(resultado)[0] if resultado else None
        except (NoSoportada, RecursionError, ValueError, TypeError, IndexError, OverflowError, ZeroDivisionError):
            resultado = None
        finally:
            self._en_curso.discard(clave)
        self._calculado[clave] = resultado
        return resultado

    def _rango(self, hoja_actual: str, ref: str):
        hoja, celdas = _partir_ref(ref, hoja_actual)
        hoja = self._hojas.get(hoja.lower())
        if hoja is None:
            return REF
        if ":" not in celdas:
            f, c = _coordenada(celdas)
            return self.valor(hoja, f, c)
        a, b = celdas.split(":")
        (f1, c1), (f2, c2) = _coordenada(a), _coordenada(b)
        f1, f2 = sorted((f1, f2))
        c1, c2 = sorted((c1, c2))
        if (f2 - f1 + 1) * (c2 - c1 + 1) > self.MAX_RANGO:
            raise NoSoportada("rango demasiado grande")
        return [[self.valor(hoja, f, c) for c in range(c1, c2 + 1)] for f in range(f1, f2 + 1)]

    def _evaluar(self, nodo, hoja: str):
        tipo = nodo[0]
        if tipo in ("num", "texto", "bool"):
            return nodo[1]
        if tipo == "vacio":
            return None
        if tipo == "ref":
            return self._rango(hoja, nodo[1])
        ev = lambda n: self._evaluar(n, hoja)  # noqa: E731
        if tipo == "neg":
            n = _numero(ev(nodo[1]))
            return n if _es_error(n) else -n
        if tipo == "&":
            a, b = ev(nodo[1]), ev(nodo[2])
            for v in (a, b):
                if _es_error(v):
                    return v
            return _texto(a) + _texto(b)
        if tipo == "cmp":
            return _comparar(nodo[1], ev(nodo[2]), ev(nodo[3]))
        if tipo == "arit":
            a, b = _numero(ev(nodo[2])), _numero(ev(nodo[3]))
            if _es_error(a):
                return a
            if _es_error(b):
                return b
            op = nodo[1]
            if op == "+":
                return a + b
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            if op == "/":
                return DIV0 if b == 0 else a / b
            return a ** b
        if tipo == "func":
            funcion = _FUNCIONES.get(nodo[1])
            if funcion is None:
                raise NoSoportada(nodo[1])
            return funcion(ev, nodo[2])
        raise NoSoportada(str(tipo))
