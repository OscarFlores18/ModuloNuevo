"""Carga del período en el filtro de fechas de ARCA (sin navegador)."""

from __future__ import annotations

import pytest

from backend.app.bots.arca import SesionArca
from backend.app.core.errors import TimeoutDescarga


class _Pagina:
    def __init__(self) -> None:
        self.esperas: list[int] = []
        self.keyboard = self
        self.url = "https://fes.afip.gob.ar/mcmp/jsp/comprobantesEmitidos.do"

    def wait_for_timeout(self, ms: int) -> None:
        self.esperas.append(ms)

    def press(self, tecla: str) -> None:
        pass

    def content(self) -> str:
        return "<html></html>"


class _CampoFecha:
    """El campo falla los primeros `fallas` clicks, como cuando ARCA no terminó de cargar."""

    def __init__(self, fallas: int, toma_el_valor: bool = True) -> None:
        self.fallas = fallas
        self.toma_el_valor = toma_el_valor
        self.valor = ""
        self.clicks = 0

    def wait_for(self, **_) -> None:
        pass

    def click(self, **_) -> None:
        self.clicks += 1
        if self.fallas:
            self.fallas -= 1
            raise RuntimeError('Locator.click: Timeout 3000ms exceeded. waiting for locator("#fechaEmision")')

    def press_sequentially(self, texto: str, **_) -> None:
        if self.toma_el_valor:
            self.valor = texto

    def press(self, _tecla: str) -> None:
        pass

    def input_value(self, **_) -> str:
        return self.valor


def _sesion() -> tuple[SesionArca, list[str]]:
    log: list[str] = []
    return SesionArca("20111111112", "x", log=log.append), log


def test_reintenta_si_el_campo_no_estaba_listo():
    sesion, log = _sesion()
    campo = _CampoFecha(fallas=1)

    sesion._escribir_rango(_Pagina(), campo, "01/04/2026 - 30/04/2026")

    assert campo.valor == "01/04/2026 - 30/04/2026"
    assert any("todavía no estaba listo" in linea for linea in log)


def test_si_nunca_responde_da_un_error_claro():
    sesion, _ = _sesion()
    # FiltroNoDisponible (un TimeoutDescarga): el control reabre Mis Comprobantes y reintenta.
    from backend.app.bots.arca import FiltroNoDisponible

    with pytest.raises(FiltroNoDisponible, match="no respondió después de 3 intentos") as error:
        sesion._escribir_rango(_Pagina(), _CampoFecha(fallas=99), "01/04/2026 - 30/04/2026")
    assert "Timeout 3000ms" in error.value.detalle["causa"]


def test_si_arca_no_toma_el_periodo_lo_avisa():
    sesion, log = _sesion()
    with pytest.raises(TimeoutDescarga, match="no aceptó el período"):
        sesion._escribir_rango(_Pagina(), _CampoFecha(fallas=0, toma_el_valor=False), "01/04/2026 - 30/04/2026")
    assert sum("no tomó el período" in linea for linea in log) == 3
