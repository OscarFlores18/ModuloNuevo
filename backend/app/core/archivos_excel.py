"""Guardar un Excel sin dejarlo a medias.

Se escribe primero un temporal y después se reemplaza el destino: si algo falla,
el archivo anterior queda como estaba y no queda basura. En Windows, si el
destino está abierto en Excel, el reemplazo falla: se avisa con un mensaje que
dice qué cerrar (en vez de «[WinError 32]»).
"""

from __future__ import annotations

from pathlib import Path

from .errors import ArchivoEnUso


def guardar_excel(wb, destino: Path | str) -> Path:
    destino = Path(destino)
    temporal = destino.with_name(destino.name + ".tmp")
    try:
        wb.save(temporal)
        temporal.replace(destino)
    except PermissionError as exc:
        raise ArchivoEnUso(
            f"«{destino.name}» está abierto (seguramente en Excel). Cerralo y volvé a intentar.",
            detalle={"ruta": str(destino)},
        ) from exc
    finally:
        temporal.unlink(missing_ok=True)
        # Guardado (o fallido), el libro ya no se usa: se libera la memoria.
        wb.close()
    return destino
