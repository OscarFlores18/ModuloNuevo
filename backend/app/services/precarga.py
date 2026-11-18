"""Clientes precargados del estudio.

Todos entran a ARCA con el mismo CUIT (``CUIT_INGRESO``) y cambian el CUIT
representado. Precargar solo agrega los que faltan: un cliente que ya existe
no se toca.

La clave fiscal no está en el código. Si algún cliente con el mismo CUIT de
ingreso ya tiene la clave cargada, se reutiliza para los nuevos: se descifra en
memoria y se vuelve a cifrar con una DEK nueva para cada uno.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.crypto import cifrar, descifrar_secreto
from ..core.storage import ClientesRepository

CUIT_INGRESO = "20235102162"

# (nombre tal cual figura en Xubio, CUIT representado)
CLIENTES_PRECARGADOS: tuple[tuple[str, str], ...] = (
    ("CONTENIDOS INTERACTIVOS SA", "30709648965"),
    ("EL FENOMENO SOCIEDAD ANONIMA", "30621024503"),
    ("FISCALES SAN MARTIN S.A.", "30717538826"),
    ("M&M COMEX CONSULTING S.R.L.", "30716425432"),
    ("MATERIALES SAN FERNANDO S.A.", "30709250538"),
    ("PICCININI y ASOCIADOS S.A.", "30710809883"),
    ("TRUCKNOR S.A.", "33716409169"),
)


@dataclass
class ResultadoPrecarga:
    agregados: list[str]
    ya_estaban: list[str]
    con_clave: bool
    completados: list[str]

    def a_dict(self) -> dict:
        return {
            "agregados": self.agregados,
            "ya_estaban": self.ya_estaban,
            "con_clave": self.con_clave,
            "completados": self.completados,
            "cuit_ingreso": CUIT_INGRESO,
        }


def _clave_compartida(repo: ClientesRepository) -> str:
    """El valor cifrado de la clave de algún cliente con el mismo CUIT de ingreso."""
    for datos in repo.listar_cifrado().values():
        arca = (datos or {}).get("arca") or {}
        if str(arca.get("cuit_login") or "") == CUIT_INGRESO and arca.get("clave"):
            return str(arca["clave"])
    return ""


def _recifrar(cifrada: str) -> str:
    """Una DEK propia por credencial: se re-cifra, no se copia."""
    if not cifrada:
        return ""
    secreto = descifrar_secreto(cifrada, credencial="clave fiscal compartida")
    try:
        return cifrar(secreto)
    finally:
        secreto.borrar()


def precargar(repo: ClientesRepository) -> ResultadoPrecarga:
    """Agrega los que faltan y, si hay clave compartida, completa la clave de
    los precargados que todavía no la tienen. No toca nada más."""
    existentes = {n.casefold(): n for n in repo.listar_cifrado()}
    cifrada = _clave_compartida(repo)
    agregados: list[str] = []
    ya_estaban: list[str] = []
    completados: list[str] = []

    for nombre, cuit_representado in CLIENTES_PRECARGADOS:
        if nombre.casefold() in existentes:
            ya_estaban.append(nombre)
            real = existentes[nombre.casefold()]
            datos = repo.obtener_cifrado(real) or {}
            arca = dict(datos.get("arca") or {})
            if cifrada and not arca.get("clave") and arca.get("cuit_login") == CUIT_INGRESO:
                arca["clave"] = _recifrar(cifrada)
                repo.guardar(real, {**datos, "arca": arca})
                completados.append(real)
            continue
        clave = _recifrar(cifrada)
        repo.guardar(
            nombre,
            {
                "arca": {
                    "cuit_login": CUIT_INGRESO,
                    "cuit_representado": cuit_representado,
                    "clave": clave,
                }
            },
        )
        agregados.append(nombre)

    return ResultadoPrecarga(agregados, ya_estaban, bool(cifrada), completados)
