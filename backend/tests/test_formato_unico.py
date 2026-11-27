"""El formato único del conversor: el modelo de Python (conversor/datos.py) y la
interfaz de TypeScript (frontend core/models.ts) tienen que tener los mismos campos.
Si se agrega uno en un lado y no en el otro, este test lo marca."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.app.extraccion.conversor import datos

MODELS_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "app" / "core" / "models.ts"

# Python → TypeScript
PARES = [
    (datos.Lectura, "LecturaExcel"),
    (datos.Item, "ItemLeido"),
    (datos.AlicuotaIva, "AlicuotaIva"),
    (datos.Tributo, "TributoLeido"),
    (datos.Hoja, "HojaLeida"),
    (datos.Verificacion, "Verificacion"),
]
# Lo que existe solo en la pantalla (no viaja al backend).
SOLO_PANTALLA = {"LecturaExcel": {"error"}}


def _campos_ts(nombre: str) -> set[str]:
    texto = MODELS_TS.read_text(encoding="utf-8")
    m = re.search(rf"export interface {nombre} \{{(.*?)\n\}}", texto, re.S)
    assert m, f"No está la interfaz {nombre} en models.ts"
    return set(re.findall(r"^\s+([a-z_]+)\??:", m.group(1), re.M))


@pytest.mark.skipif(not MODELS_TS.exists(), reason="sin el frontend al lado")
@pytest.mark.parametrize(("modelo", "interfaz"), PARES, ids=[i for _, i in PARES])
def test_mismos_campos_en_python_y_typescript(modelo, interfaz):
    assert set(modelo.model_fields) == _campos_ts(interfaz) - SOLO_PANTALLA.get(interfaz, set())
