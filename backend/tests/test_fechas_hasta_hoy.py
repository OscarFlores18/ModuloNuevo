"""ARCA no acepta fechas futuras: si el mes no terminó, se controla hasta hoy."""

from __future__ import annotations

import time

import pytest
from datetime import datetime, timedelta

from backend.app.bots.arca import limitar_a_hoy
from backend.app.services import ejecuciones as ejecuciones_srv

from .test_web import sin_pausa  # noqa: F401  (fixture: sin navegadores ni pausas)


@pytest.fixture(autouse=True)
def _historial_limpio():
    """El historial de ejecuciones es global: no dejar nada para otros tests."""
    yield
    with ejecuciones_srv._lock:
        ejecuciones_srv._ejecuciones.clear()
        ejecuciones_srv._orden.clear()


def test_limitar_a_hoy():
    hoy = datetime(2026, 11, 26, 15, 30)
    assert limitar_a_hoy("30/11/2026", hoy) == "26/11/2026"
    assert limitar_a_hoy("26/11/2026", hoy) == "26/11/2026"
    assert limitar_a_hoy("31/10/2026", hoy) == "31/10/2026"


def _esperar(client, ejecucion_id):
    for _ in range(200):
        detalle = client.get(f"/api/ejecuciones/{ejecucion_id}").json()
        if detalle["estado"] in {"finalizada", "cancelada", "error"}:
            return detalle
        time.sleep(0.02)
    return detalle


def _pedido(desde: str, hasta: str, carpeta: str) -> dict:
    return {
        "clientes": ["Acme"],
        "desde": desde,
        "hasta": hasta,
        "ventas": True,
        "compras": False,
        "pasos": {"arca": True, "xubio": False, "auditoria": False},
        "carpeta": carpeta,
    }


def test_mes_en_curso_se_controla_hasta_hoy(cliente_demo, sin_pausa, tmp_path):
    hoy = datetime.now()
    fin_de_mes = (hoy.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    r = cliente_demo.post(
        "/api/ejecuciones",
        json=_pedido(hoy.strftime("01/%m/%Y"), fin_de_mes.strftime("%d/%m/%Y"), str(tmp_path)),
    )
    assert r.status_code == 201, r.text
    detalle = _esperar(cliente_demo, r.json()["id"])

    assert detalle["periodo"].endswith(" al " + hoy.strftime("%d/%m/%Y"))
    if fin_de_mes.date() > hoy.date():
        assert any("todavía no llegó" in linea["texto"] for linea in detalle["log"])


def test_periodo_futuro_no_se_acepta(cliente_demo, sin_pausa, tmp_path):
    futuro = datetime.now() + timedelta(days=40)
    r = cliente_demo.post(
        "/api/ejecuciones",
        json=_pedido(futuro.strftime("01/%m/%Y"), futuro.strftime("%d/%m/%Y"), str(tmp_path)),
    )
    assert r.status_code == 422
    assert "todavía no llegó" in r.json()["detail"]["mensaje"]


def test_mes_pasado_no_cambia(cliente_demo, sin_pausa, tmp_path):
    r = cliente_demo.post(
        "/api/ejecuciones", json=_pedido("01/03/2025", "31/03/2025", str(tmp_path))
    )
    detalle = _esperar(cliente_demo, r.json()["id"])
    assert detalle["periodo"] == "01/03/2025 al 31/03/2025"
    assert not any("todavía no llegó" in linea["texto"] for linea in detalle["log"])


def test_dia_que_el_mes_no_tiene_se_lleva_al_ultimo():
    """31/02 o 31/11 no cortan el control: se usa el último día del mes y se avisa."""
    from backend.app.services.ejecuciones import _validar_fecha

    ajustes: list[str] = []
    assert _validar_fecha("31/02/2026", "Hasta", ajustes).day == 28
    assert _validar_fecha("31/02/2028", "Hasta", ajustes).day == 29  # bisiesto
    assert _validar_fecha("31/11/2026", "Hasta", ajustes).day == 30
    assert _validar_fecha("30/11/2026", "Hasta", ajustes).day == 30
    assert len(ajustes) == 3 and "último día del mes" in ajustes[0]


def test_fecha_sin_formato_sigue_dando_error():
    import pytest
    from backend.app.services.ejecuciones import ErrorEjecucion, _validar_fecha

    for mala in ("2026-11-30", "30/13/2026", "00/11/2026", ""):
        with pytest.raises(ErrorEjecucion):
            _validar_fecha(mala, "Desde")
