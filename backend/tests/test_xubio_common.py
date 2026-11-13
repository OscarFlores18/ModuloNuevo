from __future__ import annotations

from backend.app.bots.xubio_common import _coincide, _normalizar


def test_normalizar_ignora_acentos_puntuacion_y_sufijos():
    assert _normalizar("Estudio: PICCININI y ASOCIADOS S.A.") == (
        "estudio piccinini y asociados"
    )
    assert _normalizar("Acme S.R.L.") == "acme"
    assert _normalizar("ACME  SRL") == "acme"


def test_coincide_nombre_completo_y_abreviado():
    # El nombre de config.json puede ser parte del nombre real de Xubio.
    assert _coincide("Cliente Prueba", "CLIENTE PRUEBA S.R.L.")
    assert _coincide("COSENZA PEDRO HECTOR", "Cosenza Pedro Hector")
    assert _coincide("Acme S.A.", "ACME")


def test_coincide_tolera_un_typo_chico():
    assert _coincide("Piccinini y Asociados", "PICCININI y ASOCIDOS")


def test_coincide_no_confunde_empresas_distintas():
    assert not _coincide("ClientePruebaSRL", "COSENZA PEDRO HECTOR")
    assert not _coincide("Acme", "Beta")
    assert not _coincide("", "Acme")
