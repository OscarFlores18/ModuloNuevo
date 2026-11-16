"""Numeración de controles: 1er control, 2do control...

Cada vez que se audita un cliente para el mismo período y operación se genera
un Excel nuevo («AUX - ... - 2do control.xlsx») y se conservan los anteriores.
El número sale de los AUX que ya hay en la carpeta del período, así no hace
falta guardar ningún estado aparte.

Un AUX viejo sin número («AUX - ... .xlsx», de antes de este cambio) cuenta
como el 1er control.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .paths import SUBCARPETA_CONTROL, limpiar_nombre, nombre_carpeta

_ORDINALES = {
    1: "1er",
    2: "2do",
    3: "3er",
    4: "4to",
    5: "5to",
    6: "6to",
    7: "7mo",
    8: "8vo",
    9: "9no",
    10: "10mo",
}


def ordinal(numero: int) -> str:
    """1 -> «1er», 2 -> «2do»... (después del 10, «11°»)."""
    return _ORDINALES.get(numero, f"{numero}°")


def mes_anio(fecha_ddmmaaaa: str) -> str:
    """«01/10/2026» -> «10-26» (el formato que usan los nombres de archivo)."""
    partes = fecha_ddmmaaaa.split("/")
    if len(partes) == 3:
        return f"{partes[1]}-{partes[2][-2:]}"
    return fecha_ddmmaaaa.replace("/", "-")


def base_aux(operacion: str, fecha_desde: str, cliente: str) -> str:
    """Nombre del AUX sin número ni extensión.

    Sin el punto final del nombre («S.A.»): si no, el archivo queda «S.A..xlsx».
    """
    cliente_limpio = limpiar_nombre(cliente).rstrip(". ")
    return (
        f"AUX - Control de facturas {operacion} con AFIP - "
        f"{mes_anio(fecha_desde)} - {cliente_limpio}"
    )


@dataclass(frozen=True)
class ControlPrevio:
    numero: int
    ruta: Path
    modificado: datetime


def _patron(base: str) -> re.Pattern[str]:
    return re.compile(
        re.escape(base) + r"(?: - (\d+)\S* control)?\.xlsx$", re.IGNORECASE
    )


def _carpetas_a_revisar(carpeta: Path) -> list[Path]:
    """La carpeta de los AUX y, si es «Control de facturas», también la del
    período: ahí quedaron los AUX de antes de crear esa subcarpeta."""
    carpetas = [carpeta]
    if carpeta.name.casefold() == SUBCARPETA_CONTROL.casefold():
        carpetas.append(carpeta.parent)
    return [c for c in carpetas if c.is_dir()]


def controles_previos(carpeta: Path | str, base: str) -> list[ControlPrevio]:
    """Los AUX que ya existen para esa base, ordenados por número."""
    patron = _patron(base)
    previos: list[ControlPrevio] = []
    archivos = [a for c in _carpetas_a_revisar(Path(carpeta)) for a in c.iterdir()]
    for archivo in archivos:
        if not archivo.is_file():
            continue
        coincide = patron.fullmatch(archivo.name)
        if not coincide:
            continue
        numero = int(coincide.group(1)) if coincide.group(1) else 1
        previos.append(
            ControlPrevio(
                numero=numero,
                ruta=archivo,
                modificado=datetime.fromtimestamp(archivo.stat().st_mtime),
            )
        )
    return sorted(previos, key=lambda p: (p.numero, p.modificado))


def siguiente_numero(previos: list[ControlPrevio]) -> int:
    return max((p.numero for p in previos), default=0) + 1


# --- Carpetas por control -------------------------------------------------------------
#
# <Cliente>/<MMAAAA>/Control de facturas/
#     1er control/   <- todo lo del primer control (ARCA, Xubio y los AUX)
#     2do control/   <- el segundo control del mismo período
#
# No se guarda ningún estado: el número sale de lo que ya se hizo en la carpeta.
# - El primer control del período es siempre el «1er control».
# - Solo cuentan los controles realizados: carpetas que tienen algún archivo.
#   Si un control falla antes de bajar nada, su carpeta queda vacía (o se borra)
#   y el próximo control la reutiliza con el mismo número.
# - Los AUX sueltos de antes de las carpetas no cuentan.

_CARPETA_CONTROL = re.compile(r"(\d+)\S*\s+control", re.IGNORECASE)


def numero_de_carpeta(carpeta: Path | str) -> int | None:
    """«2do control» -> 2. None si no es una carpeta de control."""
    coincide = _CARPETA_CONTROL.fullmatch(Path(carpeta).name.strip())
    return int(coincide.group(1)) if coincide else None


def nombre_carpeta_control(numero: int) -> str:
    return f"{ordinal(numero)} control"


def _modificado(ruta: Path) -> datetime:
    return datetime.fromtimestamp(ruta.stat().st_mtime)


def tiene_archivos(carpeta: Path) -> bool:
    """True si el control dejó algún archivo (se considera realizado)."""
    return any(hijo.is_file() for hijo in carpeta.iterdir())


def controles_del_periodo(carpeta_control: Path | str) -> list[ControlPrevio]:
    """Los controles realizados en el período: carpetas «1er control»,
    «2do control»… que tienen algún archivo.

    ``carpeta_control`` es <Cliente>/<MMAAAA>/Control de facturas.
    """
    raiz = Path(carpeta_control)
    vistos: dict[int, ControlPrevio] = {}

    def sumar(numero: int, ruta: Path) -> None:
        previo = ControlPrevio(numero, ruta, _modificado(ruta))
        actual = vistos.get(numero)
        if actual is None or previo.modificado > actual.modificado:
            vistos[numero] = previo

    if raiz.is_dir():
        for hijo in raiz.iterdir():
            numero = numero_de_carpeta(hijo) if hijo.is_dir() else None
            if numero and tiene_archivos(hijo):
                sumar(numero, hijo)
    return sorted(vistos.values(), key=lambda p: p.numero)


def nueva_carpeta_control(carpeta_control: Path | str) -> tuple[Path, int]:
    """La carpeta del próximo control («1er control», «2do control»…).

    Si ya existe vacía (un control anterior que no llegó a bajar nada), se
    reutiliza. Si existe con archivos sin contar como realizado (no debería
    pasar), se pasa al número siguiente para no mezclar archivos.
    """
    raiz = Path(carpeta_control)
    raiz.mkdir(parents=True, exist_ok=True)
    numero = siguiente_numero(controles_del_periodo(raiz))
    while True:
        destino = raiz / nombre_carpeta_control(numero)
        if not destino.exists():
            destino.mkdir()
            return destino, numero
        if destino.is_dir() and not any(destino.iterdir()):
            return destino, numero
        numero += 1


def descartar_si_vacia(carpeta: Path | str) -> bool:
    """Borra la carpeta de un control que no generó nada. True si la borró."""
    carpeta = Path(carpeta)
    try:
        if numero_de_carpeta(carpeta) and carpeta.is_dir() and not any(carpeta.iterdir()):
            carpeta.rmdir()
            return True
    except OSError:
        pass
    return False


def ruta_siguiente_control(
    carpeta: Path | str, operacion: str, fecha_desde: str, cliente: str
) -> tuple[Path, int, list[ControlPrevio]]:
    """Ruta del AUX, su número y los controles que ya había.

    Si ``carpeta`` es la carpeta de un control («2do control»), el AUX lleva ese
    número. Si no (uso suelto, tests, app de escritorio), se numera por los AUX
    que ya hay en la carpeta, como antes.
    """
    carpeta = Path(carpeta)
    base = base_aux(operacion, fecha_desde, cliente)
    numero = numero_de_carpeta(carpeta)
    if numero:
        previos = [p for p in controles_del_periodo(carpeta.parent) if p.numero < numero]
    else:
        previos = controles_previos(carpeta, base)
        numero = siguiente_numero(previos)
    ruta = carpeta / f"{base} - {ordinal(numero)} control.xlsx"
    return ruta, numero, previos


def carpeta_periodo(base_salida: Path | str, cliente: str, fecha_desde: str) -> Path:
    """<Cliente>/<MMAAAA>/Control de facturas, sin crearla (misma regla que
    `carpeta_cliente`): ahí adentro están las carpetas de cada control."""
    _, mes, anio = fecha_desde.split("/")
    return Path(base_salida) / nombre_carpeta(cliente) / f"{mes}{anio}" / SUBCARPETA_CONTROL


def resumen_controles(
    base_salida: Path | str, cliente: str, fecha_desde: str, operacion: str
) -> dict[str, object]:
    """Lo que ve el front: cuántos controles hubo y cuál sería el próximo.

    Un control nuevo es una carpeta con Ventas y Compras juntos, así que el
    número es el mismo para las dos operaciones.
    """
    previos = controles_del_periodo(carpeta_periodo(base_salida, cliente, fecha_desde))
    numero = siguiente_numero(previos)
    ultimo = max((p.modificado for p in previos), default=None)
    return {
        "realizados": len(previos),
        "proximo": numero,
        "proximo_texto": f"{ordinal(numero)} control",
        "ultimo": ultimo.isoformat(timespec="seconds") if ultimo else None,
    }
