from __future__ import annotations

from pathlib import Path

from backend.app.core.paths import carpeta_cliente, dentro_de, limpiar_nombre


def test_limpiar_nombre_saca_caracteres_invalidos():
    assert limpiar_nombre('Cliente / "A" <B>') == "Cliente  A B"


def test_carpeta_cliente_respeta_estructura(tmp_path: Path):
    destino = carpeta_cliente(tmp_path, "Acme S.A.", "03", "2025")
    assert destino == tmp_path / "Acme S.A." / "032025" / "Control de Facturas"
    assert destino.is_dir()


def test_dentro_de_detecta_traversal(tmp_path: Path):
    base = tmp_path / "salidas"
    base.mkdir()
    adentro = base / "ok.xlsx"
    afuera = tmp_path / "otro.xlsx"
    assert dentro_de(base, adentro)
    assert not dentro_de(base, afuera)
