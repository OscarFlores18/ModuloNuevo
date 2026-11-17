"""El link de la empresa en Xubio llega a la ejecución («Corregir en Xubio»)."""

from __future__ import annotations

import time

import pytest

from backend.app.bots.xubio_common import es_url_de_xubio
from backend.app.services import ejecuciones as ejecuciones_srv

from .test_web import _GestorFalso, _SesionXubioFalsa, sin_pausa  # noqa: F401


@pytest.fixture(autouse=True)
def _historial_limpio():
    yield
    with ejecuciones_srv._lock:
        ejecuciones_srv._ejecuciones.clear()
        ejecuciones_srv._orden.clear()


def test_es_url_de_xubio():
    assert es_url_de_xubio("https://xubio.com/NXV/Relogin?tenant=123")
    assert es_url_de_xubio("https://www.xubio.com/NXV/")
    assert not es_url_de_xubio("http://xubio.com/NXV/")
    assert not es_url_de_xubio("javascript:alert(1)")
    assert not es_url_de_xubio("https://xubio.com.otro.com/")


def _correr(client, tmp_path, url: str | None, monkeypatch) -> dict:
    class _Xubio(_SesionXubioFalsa):
        def cambiar_empresa(self, nombre_cliente: str):
            return url

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
            "pasos": {"arca": False, "xubio": True, "auditoria": False},
            "carpeta": str(tmp_path / "salidas"),
            "xubio": {"usuario": "operador", "clave": "clave-xubio"},
        },
    ).json()["id"]
    for _ in range(200):
        detalle = client.get(f"/api/ejecuciones/{ejecucion_id}").json()
        if detalle["estado"] in {"finalizada", "cancelada", "error"}:
            return detalle
        time.sleep(0.02)
    return detalle


def test_la_ejecucion_guarda_el_link_de_la_empresa(cliente_demo, sin_pausa, tmp_path, monkeypatch):
    url = "https://xubio.com/NXV/Relogin?tenant=987"
    detalle = _correr(cliente_demo, tmp_path, url, monkeypatch)
    assert detalle["detalle_clientes"][0]["xubio_url"] == url


def test_un_link_que_no_es_de_xubio_se_descarta(cliente_demo, sin_pausa, tmp_path, monkeypatch):
    detalle = _correr(cliente_demo, tmp_path, "https://otro-sitio.com/NXV/Relogin", monkeypatch)
    assert detalle["detalle_clientes"][0]["xubio_url"] is None
