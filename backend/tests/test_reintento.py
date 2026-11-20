"""Reintento al final del control: lo que falló por ARCA o Xubio se prueba una vez más."""

from __future__ import annotations

import time

import pytest

from backend.app.core.errors import LoginError
from backend.app.services import ejecuciones as ejecuciones_srv

from .test_web import _GestorFalso, _SesionXubioFalsa, sin_pausa  # noqa: F401


@pytest.fixture(autouse=True)
def _historial_limpio():
    yield
    with ejecuciones_srv._lock:
        ejecuciones_srv._ejecuciones.clear()
        ejecuciones_srv._orden.clear()


def _correr(client, tmp_path, monkeypatch, falla) -> tuple[dict, list[int]]:
    """Xubio falla con `falla` la primera vez y anda la segunda."""
    intentos: list[int] = []

    class _Xubio(_SesionXubioFalsa):
        def descargar(self, *a, **k):
            intentos.append(1)
            if len(intentos) == 1:
                raise falla
            return super().descargar(*a, **k)

    class _Gestor(_GestorFalso):
        def xubio(self, usuario, password):
            return _Xubio()

    monkeypatch.setattr(ejecuciones_srv, "GestorSesiones", _Gestor)
    ejecucion_id = client.post(
        "/api/ejecuciones",
        json={
            "clientes": ["Acme"],
            "desde": "01/03/2025",
            "hasta": "31/03/2025",
            "ventas": True,
            "compras": False,
            "pasos": {"arca": False, "xubio": True, "auditoria": True},
            "carpeta": str(tmp_path / "salidas"),
            "xubio": {"usuario": "operador", "clave": "clave-xubio"},
        },
    ).json()["id"]
    for _ in range(300):
        detalle = client.get(f"/api/ejecuciones/{ejecucion_id}").json()
        if detalle["estado"] in {"finalizada", "cancelada", "error"}:
            return detalle, intentos
        time.sleep(0.02)
    return detalle, intentos


def _paso(detalle: dict, id_paso: str) -> dict:
    return next(p for p in detalle["detalle_clientes"][0]["pasos"] if p["id"] == id_paso)


def test_una_falla_pasajera_se_reintenta_y_queda_ok(cliente_demo, sin_pausa, tmp_path, monkeypatch):
    detalle, intentos = _correr(cliente_demo, tmp_path, monkeypatch, TimeoutError("Timeout 30000ms exceeded."))
    assert len(intentos) == 2
    assert _paso(detalle, "xubio")["estado"] == "ok"
    # La auditoría que se había omitido por la falla también se hizo.
    assert _paso(detalle, "auditoria_ventas")["estado"] == "ok"
    assert detalle["detalle_clientes"][0]["estado"] == "ok"
    assert not [a for a in detalle.get("advertencias", []) if a["tipo"] == "paso_error"]
    assert any("[REINTENTO]" in linea["texto"] for linea in detalle["log"])


def test_una_clave_mal_no_se_reintenta(cliente_demo, sin_pausa, tmp_path, monkeypatch):
    detalle, intentos = _correr(cliente_demo, tmp_path, monkeypatch, LoginError("Usuario o clave incorrectos."))
    assert len(intentos) == 1
    assert _paso(detalle, "xubio")["estado"] == "error"
    assert detalle["detalle_clientes"][0]["estado"] == "con_errores"
