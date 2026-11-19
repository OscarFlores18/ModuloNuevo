"""«Aplicar cambios»: el dato de ARCA elegido se escribe en el Libro IVA del control
y el mismo AUX se vuelve a cruzar (no quedan copias aparte)."""

from __future__ import annotations

from pathlib import Path

import openpyxl

from backend.app.auditor.corregir_libro import corregir_libro
from backend.app.services import ejecuciones as srv

from .test_errores_reales import _arca, _xubio

CUIT = "27479591992"


def _carpeta(tmp_path: Path) -> Path:
    """Un control terminado con los tres errores que se corrigen: letra, tipo e importe."""
    carpeta = tmp_path / "salidas" / "PICCININI" / "042026" / "Control de facturas" / "1er control"
    carpeta.mkdir(parents=True)
    _arca(
        carpeta / "Mis comprobantes - Recibidos - PICCININI - 04-26 al 04-26.xlsx",
        [
            {"cuit": CUIT, "numero": 6, "tipo": "11 - Factura C", "total": 1000},
            {"cuit": CUIT, "numero": 7, "tipo": "11 - Factura C", "total": 2000},
            {"cuit": CUIT, "numero": 8, "tipo": "11 - Factura C", "total": 3000},
        ],
    )
    _xubio(
        carpeta / "IVA COMPRAS - PICCININI - 01-04-2026 al 30-04-2026.xlsx",
        [
            {"cuit": "27-47959199-2", "comprobante": "A-0002-00000006", "total": 1000},
            {"cuit": "27-47959199-2", "comprobante": "C-0002-00000007", "tipo": "Nota Débito", "total": 2000},
            {"cuit": "27-47959199-2", "comprobante": "C-0002-00000008", "total": 3100},
        ],
    )
    return carpeta


def _ejecucion(carpeta: Path) -> srv.Ejecucion:
    errores: list[dict] = []
    resumenes: list[dict] = []
    srv.procesar_cruce(
        "PICCININI", "Compras", "01/04/2026", "30/04/2026", str(carpeta),
        log=lambda _: None, avisar_errores=errores.extend, avisar_resumen=resumenes.append,
    )
    ej = srv.Ejecucion(
        id="apl123",
        usuario="test",
        parametros={"desde": "01/04/2026", "hasta": "30/04/2026"},
        clientes=[srv.ClienteEnCurso("PICCININI", [], carpeta=str(carpeta), archivos=srv.listar_archivos(carpeta))],
        estado="finalizada",
        advertencias=[{**e, "cliente": "PICCININI"} for e in errores],
        resumenes=[{**r, "cliente": "PICCININI"} for r in resumenes],
    )
    with srv._lock:
        srv._ejecuciones[ej.id] = ej
    return ej


CAMBIOS = [
    {"cliente": "PICCININI", "operacion": "Compras", "campo": "letra", "cuit": CUIT, "pv": 2, "numero": 6, "comprobante": "A-00002-00000006", "valorArca": "C"},
    {"cliente": "PICCININI", "operacion": "Compras", "campo": "tipo", "cuit": CUIT, "pv": 2, "numero": 7, "comprobante": "C-00002-00000007", "valorArca": "11 - Factura C"},
    {"cliente": "PICCININI", "operacion": "Compras", "campo": "importe", "cuit": CUIT, "pv": 2, "numero": 8, "comprobante": "C-00002-00000008", "valorArca": 3000},
]


def test_corrige_las_celdas_en_una_copia(tmp_path: Path):
    carpeta = _carpeta(tmp_path)
    original = carpeta / "IVA COMPRAS - PICCININI - 01-04-2026 al 30-04-2026.xlsx"
    copia = tmp_path / "corregido.xlsx"
    cambios = [{**c, "valor_arca": c["valorArca"]} for c in CAMBIOS]
    cambios.append({"campo": "importe", "cuit": CUIT, "pv": 2, "numero": 99, "valor_arca": 1})

    r = corregir_libro(original, copia, cambios)

    assert len(r["aplicados"]) == 3
    assert [n["numero"] for n in r["no_aplicados"]] == [99]
    filas = list(openpyxl.load_workbook(copia).active.iter_rows(min_row=2, values_only=True))
    assert [(f[1], f[2], f[21]) for f in filas] == [
        ("Factura", "C-0002-00000006", 1000),
        ("Factura", "C-0002-00000007", 2000),
        ("Factura", "C-0002-00000008", 3000),
    ]
    # El de Xubio queda como estaba.
    assert openpyxl.load_workbook(original).active.cell(2, 3).value == "A-0002-00000006"


def _filas_rojas(ruta: Path) -> int:
    ws = openpyxl.load_workbook(ruta)["Libro Iva"]
    return sum(
        1 for fila in ws.iter_rows(min_row=2)
        if any(c.fill.fill_type == "solid" and str(c.fill.start_color.rgb).endswith("FFCCCC") for c in fila)
    )


def test_aplicar_corrige_y_vuelve_a_cruzar(entorno, tmp_path: Path):
    client, _ = entorno
    carpeta = _carpeta(tmp_path)
    ej = _ejecucion(carpeta)
    antes = {a["tipo"] for a in ej.advertencias}
    assert {"letra_distinta", "tipo_distinto", "importe_distinto"} <= antes
    [aux] = [a["nombre"] for a in ej.clientes[0].archivos if a["nombre"].startswith("AUX - ")]
    # Los tres pares mal cargados se ven en rojo claro en el Excel.
    assert _filas_rojas(carpeta / aux) == 3

    r = client.post("/api/ejecuciones/apl123/aplicar-cambios", json={"cambios": CAMBIOS})

    assert r.status_code == 200, r.text
    datos = r.json()
    [res] = datos["resultados"]
    assert res["aplicados"] == 3 and res["no_aplicados"] == []
    assert res["libro"] == "IVA COMPRAS - PICCININI - 01-04-2026 al 30-04-2026.xlsx"
    assert res["aux"].startswith("AUX - Control de facturas Compras") and "corregido" not in res["aux"]
    # Re-chequeado: los tres pares ya no son errores.
    tipos = {a["tipo"] for a in datos["ejecucion"]["advertencias"]}
    assert not tipos & {"letra_distinta", "tipo_distinto", "importe_distinto", "faltante_en_xubio", "faltante_en_arca"}
    [resumen] = datos["ejecucion"]["resumenes"]
    assert resumen["coinciden"] == 3
    cli = datos["ejecucion"]["detalle_clientes"][0]
    nombres = {a["nombre"] for a in cli["archivos"]}
    assert {res["libro"], res["aux"]} <= nombres
    assert len(cli["corregidos"]["Compras"]["cambios"]) == 3
    # El AUX del control se reemplaza: sigue habiendo uno solo, sin copias.
    assert client.get(f"/api/ejecuciones/apl123/clientes/0/archivos/{res['aux']}").status_code == 200
    assert len([n for n in nombres if n.startswith("AUX - Control de facturas Compras")]) == 1
    assert not any("corregido" in p.name for p in carpeta.iterdir())
    libro = [c.value for fila in openpyxl.load_workbook(carpeta / res["libro"]).active.iter_rows() for c in fila]
    assert "C-0002-00000006" in libro and "A-0002-00000006" not in libro
    # Corregidos: en el mismo AUX ya no están en rojo.
    assert _filas_rojas(carpeta / res["aux"]) == 0


def test_no_aplica_con_el_control_corriendo(entorno, tmp_path: Path):
    client, _ = entorno
    ej = _ejecucion(_carpeta(tmp_path))
    ej.estado = "en_curso"
    r = client.post("/api/ejecuciones/apl123/aplicar-cambios", json={"cambios": CAMBIOS})
    assert r.status_code == 409


# --- Fallas a mitad de camino ------------------------------------------------


def test_excel_abierto_avisa_que_lo_cierren_y_no_deja_temporales(tmp_path: Path, monkeypatch):
    from backend.app.core.archivos_excel import guardar_excel
    from backend.app.core.errors import ArchivoEnUso

    destino = tmp_path / "libro.xlsx"

    def ocupado(self, *_):
        raise PermissionError(32, "El proceso no tiene acceso al archivo")

    monkeypatch.setattr(Path, "replace", ocupado)
    try:
        guardar_excel(openpyxl.Workbook(), destino)
    except ArchivoEnUso as exc:
        assert "«libro.xlsx» está abierto" in str(exc) and "Cerralo" in str(exc)
    else:
        raise AssertionError("tenía que avisar")
    assert list(tmp_path.iterdir()) == []


def test_si_el_recruce_falla_el_libro_corregido_vuelve_a_como_estaba(entorno, tmp_path: Path, monkeypatch):
    client, _ = entorno
    carpeta = _carpeta(tmp_path)
    ej = _ejecucion(carpeta)
    antes = list(ej.advertencias)
    previos = {p.name: p.read_bytes() for p in carpeta.iterdir()}

    def falla(*_a, **_k):
        raise RuntimeError("Page.click: Target page, context or browser has been closed")

    monkeypatch.setattr(srv, "procesar_cruce", falla)
    r = client.post("/api/ejecuciones/apl123/aplicar-cambios", json={"cambios": CAMBIOS})

    assert r.status_code == 500
    assert "No se aplicó ningún cambio" in r.json()["detail"]["mensaje"]
    assert not any(p.name.endswith(" - corregido.xlsx") for p in carpeta.iterdir())
    assert {p.name: p.read_bytes() for p in carpeta.iterdir()} == previos
    assert ej.advertencias == antes


def test_una_operacion_que_falla_no_frena_ni_borra_las_demas(entorno, tmp_path: Path):
    client, _ = entorno
    ej = _ejecucion(_carpeta(tmp_path))
    cambios = CAMBIOS + [{**CAMBIOS[0], "operacion": "Ventas"}]  # Ventas no tiene archivos

    r = client.post("/api/ejecuciones/apl123/aplicar-cambios", json={"cambios": cambios})

    assert r.status_code == 200, r.text
    por_op = {x["operacion"]: x for x in r.json()["resultados"]}
    assert por_op["Compras"]["aplicados"] == 3
    assert "falta" in por_op["Ventas"]["error"]
    # Lo aplicado quedó guardado en disco (sobrevive a un reinicio).
    guardado = srv._carpeta_historial() / "apl123.json"
    assert guardado.is_file() and "corregido" in guardado.read_text(encoding="utf-8")
    assert ej.clientes[0].corregidos["Compras"]["cambios"]


def test_consumidor_final_sin_cuit_se_puede_aplicar(tmp_path: Path):
    libro = tmp_path / "libro.xlsx"
    _xubio(libro, [{"cuit": None, "comprobante": "A-0002-00000006", "total": 900}])
    r = corregir_libro(libro, tmp_path / "copia.xlsx", [
        {"campo": "importe", "cuit": "", "pv": 2, "numero": 6, "valor_arca": 1000},
    ])
    assert len(r["aplicados"]) == 1 and r["no_aplicados"] == []


def test_los_errores_se_arreglan_solos_al_terminar_el_cruce(tmp_path: Path):
    carpeta = _carpeta(tmp_path)
    ej = _ejecucion(carpeta)
    # Los cambios salen de los hallazgos, sin elegir nada a mano.
    cambios = srv.cambios_de_errores(ej.advertencias)
    assert sorted((c["campo"], c["numero"], c["valor_arca"]) for c in cambios) == [
        ("importe", 8, 3000.0), ("letra", 6, "C"), ("tipo", 7, "11 - Factura C"),
    ]

    assert srv.arreglar_solo(ej, 0, "Compras") == 3

    assert not {a["tipo"] for a in ej.advertencias} & {"letra_distinta", "tipo_distinto", "importe_distinto"}
    hechos = ej.clientes[0].corregidos["Compras"]["cambios"]
    assert sorted((c["campo"], c["antes"], c["despues"]) for c in hechos)[1] == ("letra", "A-0002-00000006", "C-0002-00000006")
    [aux] = [a["nombre"] for a in ej.clientes[0].archivos if a["nombre"].startswith("AUX - ")]
    assert _filas_rojas(carpeta / aux) == 0
    # Una segunda vez no hay nada para arreglar.
    assert srv.arreglar_solo(ej, 0, "Compras") == 0
