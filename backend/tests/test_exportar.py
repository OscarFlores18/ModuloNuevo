"""«Descargar todos»: el servidor copia los archivos a <elegida>/<Cliente>/<mmaaaa>/Control de Facturas."""

from __future__ import annotations

import shutil

from backend.app.core import dialogos
from backend.app.services import ejecuciones as srv


def _ejecucion(tmp_path):
    origen = tmp_path / "salidas" / "Cliente SA" / "102026" / "Control de Facturas"
    origen.mkdir(parents=True)
    for nombre in ("Mis comprobantes - Emitidos - X.xlsx", "IVA VENTAS - X.xlsx", "AUX - Control X.xlsx"):
        (origen / nombre).write_bytes(nombre.encode())
    ej = srv.Ejecucion(
        id="exp123",
        usuario="test",
        parametros={"desde": "01/10/2026", "hasta": "31/10/2026"},
        clientes=[srv.ClienteEnCurso("Cliente SA.", [], carpeta=str(origen))],
        estado="finalizada",
    )
    with srv._lock:
        srv._ejecuciones[ej.id] = ej
    return ej


def test_copia_con_el_formato_de_la_app_de_escritorio(entorno, tmp_path, monkeypatch):
    client, _ = entorno
    _ejecucion(tmp_path)
    destino = tmp_path / "elegida"
    destino.mkdir()
    monkeypatch.setattr(dialogos, "elegir_carpeta", lambda *a, **k: destino)

    r = client.post("/api/ejecuciones/exp123/clientes/0/exportar")

    assert r.status_code == 200, r.text
    datos = r.json()
    # El punto final del nombre se saca: Windows no admite carpetas que terminen en punto.
    carpeta = destino / "Cliente SA" / "102026" / "Control de Facturas"
    assert datos["carpeta"] == str(carpeta)
    assert sorted(p.name for p in carpeta.iterdir()) == sorted(datos["copiados"])
    assert len(datos["copiados"]) == 3 and datos["fallas"] == []
    assert (carpeta / "IVA VENTAS - X.xlsx").read_bytes() == b"IVA VENTAS - X.xlsx"


def test_cerrar_la_ventana_no_copia_nada(entorno, tmp_path, monkeypatch):
    client, _ = entorno
    _ejecucion(tmp_path)
    monkeypatch.setattr(dialogos, "elegir_carpeta", lambda *a, **k: None)

    r = client.post("/api/ejecuciones/exp123/clientes/0/exportar")

    assert r.status_code == 200
    assert r.json() == {"cancelado": True}


def test_un_archivo_bloqueado_no_frena_a_los_demas(entorno, tmp_path, monkeypatch):
    _ejecucion(tmp_path)
    destino = tmp_path / "elegida"
    destino.mkdir()
    copiar = shutil.copy2

    def copia_con_excel_abierto(src, dst):
        if "AUX" in str(dst):
            raise PermissionError(13, "Permission denied")
        return copiar(src, dst)

    monkeypatch.setattr(srv.shutil, "copy2", copia_con_excel_abierto)

    r = srv.exportar_archivos(srv.obtener("exp123"), 0, destino)

    assert len(r["copiados"]) == 2
    assert [f["nombre"] for f in r["fallas"]] == ["AUX - Control X.xlsx"]
    assert "Excel" in r["fallas"][0]["motivo"]


def test_ejecucion_inexistente_da_404(entorno, monkeypatch):
    client, _ = entorno
    monkeypatch.setattr(dialogos, "elegir_carpeta", lambda *a, **k: None)
    assert client.post("/api/ejecuciones/nada/clientes/0/exportar").status_code == 404
