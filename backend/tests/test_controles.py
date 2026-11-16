"""1er / 2do control: cada auditoría del mismo período genera un AUX nuevo."""

from __future__ import annotations

from pathlib import Path

from backend.app.auditor import procesar_cruce
from backend.app.core import config
from backend.app.core.controles import (
    base_aux,
    carpeta_periodo,
    ordinal,
    ruta_siguiente_control,
)

from .test_auditor import _crear_arca, _crear_xubio

def _realizado(carpeta: Path) -> Path:
    """Simula un control que terminó: la carpeta con un archivo adentro."""
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "IVA VENTAS - X.xlsx").write_bytes(b"x")
    return carpeta


ARCA = {"cuit_login": "20-23510216-2", "clave": "secreta", "cuit_representado": "30-71080988-3"}


def test_ordinales():
    assert [ordinal(n) for n in (1, 2, 3, 4, 7, 10, 11)] == [
        "1er", "2do", "3er", "4to", "7mo", "10mo", "11°",
    ]


def test_primer_control_en_carpeta_vacia(tmp_path: Path):
    ruta, numero, previos = ruta_siguiente_control(tmp_path, "Ventas", "01/10/2026", "Acme S.A.")
    assert numero == 1 and previos == []
    assert ruta.name == "AUX - Control de facturas Ventas con AFIP - 10-26 - Acme S.A - 1er control.xlsx"


def test_aux_viejo_sin_numero_cuenta_como_primero(tmp_path: Path):
    (tmp_path / f"{base_aux('Ventas', '01/10/2026', 'Acme')}.xlsx").write_bytes(b"x")
    ruta, numero, previos = ruta_siguiente_control(tmp_path, "Ventas", "01/10/2026", "Acme")
    assert numero == 2 and [p.numero for p in previos] == [1]
    assert ruta.name.endswith(" - 2do control.xlsx")


def test_no_mezcla_operaciones_ni_periodos(tmp_path: Path):
    for op, desde in (("Compras", "01/10/2026"), ("Ventas", "01/09/2026")):
        (tmp_path / f"{base_aux(op, desde, 'Acme')} - 1er control.xlsx").write_bytes(b"x")
    _, numero, _ = ruta_siguiente_control(tmp_path, "Ventas", "01/10/2026", "Acme")
    assert numero == 1


def test_auditar_dos_veces_conserva_los_dos(tmp_path: Path):
    arca, xubio = tmp_path / "arca.xlsx", tmp_path / "xubio.xlsx"
    _crear_arca(arca)
    _crear_xubio(xubio)
    log: list[str] = []
    kwargs = dict(ruta_arca_manual=str(arca), ruta_xubio_manual=str(xubio), log=log.append)

    primero = procesar_cruce("Acme", "Ventas", "01/03/2025", "31/03/2025", str(tmp_path), **kwargs)
    segundo = procesar_cruce("Acme", "Ventas", "01/03/2025", "31/03/2025", str(tmp_path), **kwargs)

    assert Path(primero).name.endswith(" - 1er control.xlsx")
    assert Path(segundo).name.endswith(" - 2do control.xlsx")
    assert Path(primero).is_file() and Path(segundo).is_file()
    assert any("1er control del período" in linea for linea in log)
    assert any("2do control (el 1er fue el" in linea for linea in log)


def test_endpoint_controles_fecha_invalida(entorno):
    client, _ = entorno
    r = client.post("/api/controles", json={"clientes": [], "desde": "2026-10-01"})
    assert r.status_code == 422


def test_el_proximo_aux_sigue_la_numeracion_del_anterior(tmp_path: Path):
    from backend.app.core.controles import ruta_siguiente_control

    periodo = tmp_path / "Acme" / "102026"
    nueva = periodo / "Control de facturas"
    nueva.mkdir(parents=True)
    (periodo / f"{base_aux('Compras', '01/10/2026', 'Acme')}.xlsx").write_bytes(b"x")

    ruta, numero, previos = ruta_siguiente_control(nueva, "Compras", "01/10/2026", "Acme")

    assert numero == 2 and len(previos) == 1
    assert ruta.parent == nueva and ruta.name.endswith("2do control.xlsx")


# --- Carpetas por control ----------------------------------------------------------


def test_cada_control_crea_su_carpeta(tmp_path: Path):
    from backend.app.core.controles import nueva_carpeta_control

    raiz = tmp_path / "Acme" / "102026" / "Control de facturas"
    primera, n1 = nueva_carpeta_control(raiz)
    _realizado(primera)
    segunda, n2 = nueva_carpeta_control(raiz)
    assert (n1, primera.name) == (1, "1er control")
    assert (n2, segunda.name) == (2, "2do control")
    assert primera.is_dir() and segunda.is_dir()


def test_el_aux_lleva_el_numero_de_su_carpeta(tmp_path: Path):
    from backend.app.core.controles import nueva_carpeta_control

    raiz = tmp_path / "Acme" / "102026" / "Control de facturas"
    _realizado(nueva_carpeta_control(raiz)[0])
    segunda, _ = nueva_carpeta_control(raiz)
    arca, xubio = segunda / "arca.xlsx", segunda / "xubio.xlsx"
    _crear_arca(arca)
    _crear_xubio(xubio)
    log: list[str] = []

    salida = procesar_cruce(
        "Acme", "Ventas", "01/03/2025", "31/03/2025", str(segunda),
        ruta_arca_manual=str(arca), ruta_xubio_manual=str(xubio), log=log.append,
    )

    assert Path(salida).parent == segunda
    assert Path(salida).name.endswith(" - 2do control.xlsx")
    assert any("2do control (el 1er fue el" in linea for linea in log)


def test_endpoint_cuenta_las_carpetas_de_control(entorno):
    client, _ = entorno
    client.post("/api/clientes", json={"nombre": "Acme", "arca": ARCA})
    raiz = carpeta_periodo(config.OUTPUT_DIR, "Acme", "01/10/2026")
    _realizado(raiz / "1er control")
    _realizado(raiz / "2do control")
    _realizado(raiz / "otra carpeta")

    datos = client.post("/api/controles", json={"clientes": ["Acme"], "desde": "01/10/2026"}).json()

    for op in ("ventas", "compras"):
        assert datos["Acme"][op]["realizados"] == 2
        assert datos["Acme"][op]["proximo_texto"] == "3er control"


def test_los_aux_sueltos_de_antes_no_cuentan(tmp_path: Path):
    """Solo cuentan las carpetas de control: el primero con carpeta es el 1er."""
    from backend.app.core.controles import nueva_carpeta_control

    periodo = tmp_path / "Acme" / "102026"
    raiz = periodo / "Control de facturas"
    raiz.mkdir(parents=True)
    (periodo / f"{base_aux('Ventas', '01/10/2026', 'Acme')}.xlsx").write_bytes(b"x")
    (raiz / f"{base_aux('Compras', '01/10/2026', 'Acme')} - 5to control.xlsx").write_bytes(b"x")

    carpeta, numero = nueva_carpeta_control(raiz)
    assert (numero, carpeta.name) == (1, "1er control")


def test_endpoint_controles(entorno):
    client, _ = entorno
    client.post("/api/clientes", json={"nombre": "Acme", "arca": ARCA})
    raiz = carpeta_periodo(config.OUTPUT_DIR, "Acme", "01/10/2026")
    raiz.mkdir(parents=True)
    # Un AUX suelto viejo no cuenta; la carpeta «1er control» sí.
    (raiz / f"{base_aux('Ventas', '01/10/2026', 'Acme')} - 4to control.xlsx").write_bytes(b"x")

    r = client.post("/api/controles", json={"clientes": ["Acme", "No existe"], "desde": "01/10/2026"})
    assert r.status_code == 200
    datos = r.json()
    assert list(datos) == ["Acme"]
    assert datos["Acme"]["ventas"]["proximo_texto"] == "1er control"
    assert datos["Acme"]["ventas"]["ultimo"] is None

    _realizado(raiz / "1er control")
    datos = client.post("/api/controles", json={"clientes": ["Acme"], "desde": "01/10/2026"}).json()
    for op in ("ventas", "compras"):
        assert datos["Acme"][op]["realizados"] == 1
        assert datos["Acme"][op]["proximo_texto"] == "2do control"


def test_un_control_sin_archivos_no_cuenta_y_se_reutiliza(tmp_path: Path):
    """Si un control falla antes de bajar nada, el próximo usa su número."""
    from backend.app.core.controles import controles_del_periodo, nueva_carpeta_control

    raiz = tmp_path / "Acme" / "102026" / "Control de facturas"
    _realizado(nueva_carpeta_control(raiz)[0])
    fallido, n_fallido = nueva_carpeta_control(raiz)
    assert (n_fallido, fallido.name) == (2, "2do control")
    assert [p.numero for p in controles_del_periodo(raiz)] == [1]

    reintento, n_reintento = nueva_carpeta_control(raiz)
    assert (n_reintento, reintento) == (2, fallido)


def test_descartar_si_vacia(tmp_path: Path):
    from backend.app.core.controles import descartar_si_vacia

    vacia = tmp_path / "1er control"
    vacia.mkdir()
    con_algo = _realizado(tmp_path / "2do control")
    otra = tmp_path / "otra carpeta"
    otra.mkdir()

    assert descartar_si_vacia(vacia) is True and not vacia.exists()
    assert descartar_si_vacia(con_algo) is False and con_algo.exists()
    # Solo borra carpetas de control.
    assert descartar_si_vacia(otra) is False and otra.exists()
