"""Identidad local del módulo.

El frontend de Control de facturas puede venir de ContadorWeb con un pase, o
funcionar en modo local con un usuario de prueba. Este backend, por ahora, no
valida pases de ContadorWeb: en modo local toma la identidad de los encabezados
`X-Usuario-*` que manda el frontend (o un supervisor por defecto, para que la
API sea usable desde `/docs` y los tests).

Cuando se conecte ContadorWeb, esta es la única pieza que hay que cambiar.
"""

from __future__ import annotations

from urllib.parse import unquote

from fastapi import Request
from pydantic import BaseModel, Field


class Usuario(BaseModel):
    id: str
    nombre: str
    roles: list[str] = Field(default_factory=list)
    nivel: str = ""
    nivel_nombre: str = ""
    estudio_nombre: str = ""
    conectado: bool = False

    def tiene(self, rol: str) -> bool:
        return rol in self.roles


NIVELES = {"200": "Administrador General", "100": "Administrador", "10": "Usuario"}


def _roles_para_nivel(nivel: str) -> list[str]:
    return ["supervisor"] if nivel in ("200", "100") else ["empleado"]


def usuario_actual(request: Request) -> Usuario:
    """La identidad del pedido (modo local)."""
    id_ = request.headers.get("X-Usuario-Id") or "local"
    nombre = unquote(request.headers.get("X-Usuario-Nombre") or "") or "Usuario local"
    nivel = request.headers.get("X-Usuario-Nivel") or "100"
    if nivel not in NIVELES:
        nivel = "10" if nivel.isdigit() else "100"
    return Usuario(
        id=id_,
        nombre=nombre,
        roles=_roles_para_nivel(nivel),
        nivel=nivel,
        nivel_nombre=NIVELES.get(nivel, ""),
        conectado=False,
    )


def usuario_por_defecto() -> Usuario:
    return Usuario(
        id="local",
        nombre="Usuario local",
        roles=["supervisor"],
        nivel="100",
        nivel_nombre=NIVELES["100"],
        conectado=False,
    )
