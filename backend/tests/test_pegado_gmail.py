"""Pegado automático en Gmail: espera a que Gmail quede adelante y quieto."""

from app.core.pegado import esperar_y_pegar


class _Reloj:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def dormir(self, s: float) -> None:
        self.t += s


def _correr(titulos) -> tuple[bool, list[float], _Reloj]:
    reloj = _Reloj()
    pegados: list[float] = []
    ok = esperar_y_pegar(
        titulo=lambda: titulos(reloj.t),
        pegar=lambda: pegados.append(reloj.t),
        dormir=reloj.dormir,
        reloj=reloj,
    )
    return ok, pegados, reloj


def test_pega_cuando_gmail_esta_adelante_y_cargado():
    def titulos(t: float) -> str:
        if t < 1:
            return "Control FCT"
        if t < 2.5:
            return "Gmail - Google Chrome"
        return "Mensaje nuevo - tito@estudio.com.ar - Gmail - Google Chrome"

    ok, pegados, _ = _correr(titulos)
    assert ok
    # Gmail apareció en t=1; pega con al menos 3 s adelante y 2 s con el título quieto.
    assert len(pegados) == 1 and pegados[0] >= 4.5


def test_no_pega_si_la_persona_se_va_a_otra_ventana():
    ok, pegados, _ = _correr(lambda t: "Gmail - Google Chrome" if t < 2 else "Excel")
    assert not ok and pegados == []


def test_se_rinde_si_gmail_nunca_aparece():
    ok, pegados, reloj = _correr(lambda t: "Control FCT")
    assert not ok and pegados == [] and reloj.t >= 30
