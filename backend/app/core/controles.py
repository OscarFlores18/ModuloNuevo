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


def ruta_siguiente_control(
    carpeta: Path | str, operacion: str, fecha_desde: str, cliente: str
) -> tuple[Path, int, list[ControlPrevio]]:
    """Ruta del próximo AUX, su número y los controles que ya había."""
    base = base_aux(operacion, fecha_desde, cliente)
    previos = controles_previos(carpeta, base)
    numero = siguiente_numero(previos)
    ruta = Path(carpeta) / f"{base} - {ordinal(numero)} control.xlsx"
    return ruta, numero, previos


def carpeta_periodo(base_salida: Path | str, cliente: str, fecha_desde: str) -> Path:
    """Donde quedan los AUX del período, sin crearla (misma regla que
    `carpeta_cliente`): <Cliente>/<MMAAAA>/Control de facturas."""
    _, mes, anio = fecha_desde.split("/")
    return Path(base_salida) / nombre_carpeta(cliente) / f"{mes}{anio}" / SUBCARPETA_CONTROL


def resumen_controles(
    base_salida: Path | str, cliente: str, fecha_desde: str, operacion: str
) -> dict[str, object]:
    """Lo que ve el front: cuántos controles hubo y cuál sería el próximo."""
    carpeta = carpeta_periodo(base_salida, cliente, fecha_desde)
    previos = controles_previos(carpeta, base_aux(operacion, fecha_desde, cliente))
    numero = siguiente_numero(previos)
    return {
        "realizados": len(previos),
        "proximo": numero,
        "proximo_texto": f"{ordinal(numero)} control",
        "ultimo": previos[-1].modificado.isoformat(timespec="seconds")
        if previos
        else None,
    }
