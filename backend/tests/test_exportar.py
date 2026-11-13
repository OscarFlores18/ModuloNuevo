"""«Descargar todos»: a <elegida>/<mmaaaa>/ o, sin elegir, a Descargas/<Cliente>/<mmaaaa>/."""

from __future__ import annotations

import os
import shutil
import time

import io
import zipfile

import pytest

from backend.app.core import config, dialogos
from backend.app.services import ejecuciones as srv


@pytest.fixture(autouse=True)
def _con_dialogos(monkeypatch):
    """Estos tests prueban el modo local (ventanas de Windows); el de servidor, aparte."""
    monkeypatch.setattr(config, "DIALOGOS_LOCALES", True)


def _ejecucion(tmp_path):
    origen = tmp_path / "salidas" / "Cliente SA" / "102026"
    origen.mkdir(parents=True)
    for nombre in ("Mis comprobantes - Emitidos - X.xlsx", "IVA VENTAS - X.xlsx", "AUX - Control X.xlsx"):
        (origen / nombre).write_bytes(nombre.encode())
    ej = srv.Ejecucion(
        id="exp123",
        usuario="test",
        parametros={"desde": "01/10/2026", "hasta": "31/10/2026"},
        clientes=[srv.ClienteEnCurso("Cliente SA.", [], carpeta=str(origen), archivos=srv.listar_archivos(origen))],
        estado="finalizada",
    )
    with srv._lock:
        srv._ejecuciones[ej.id] = ej
    return ej


def test_carpeta_elegida_recibe_solo_las_subcarpetas(entorno, tmp_path, monkeypatch):
    """Se elige la carpeta del cliente que ya existe: adentro va mmaaaa con los archivos."""
    client, _ = entorno
    _ejecucion(tmp_path)
    destino = tmp_path / "Clientes" / "Cliente SA"
    destino.mkdir(parents=True)
    monkeypatch.setattr(dialogos, "elegir_carpeta", lambda *a, **k: destino)

    r = client.post("/api/ejecuciones/exp123/clientes/0/exportar")

    assert r.status_code == 200, r.text
    datos = r.json()
    carpeta = destino / "102026"
    assert datos["carpeta"] == str(carpeta) and datos["elegida"] is True
    assert sorted(p.name for p in carpeta.iterdir()) == sorted(datos["copiados"])
    assert len(datos["copiados"]) == 3 and datos["fallas"] == []
    assert (carpeta / "IVA VENTAS - X.xlsx").read_bytes() == b"IVA VENTAS - X.xlsx"


def test_sin_elegir_se_crea_la_carpeta_del_cliente_en_descargas(entorno, tmp_path, monkeypatch):
    client, _ = entorno
    _ejecucion(tmp_path)
    casa = tmp_path / "casa"
    (casa / "Downloads").mkdir(parents=True)
    monkeypatch.setattr(srv.Path, "home", classmethod(lambda cls: casa))
    monkeypatch.setattr(dialogos, "elegir_carpeta", lambda *a, **k: None)

    r = client.post("/api/ejecuciones/exp123/clientes/0/exportar")

    assert r.status_code == 200, r.text
    datos = r.json()
    # El punto final del nombre se saca: Windows no admite carpetas que terminen en punto.
    carpeta = casa / "Downloads" / "Cliente SA" / "102026"
    assert datos["carpeta"] == str(carpeta) and datos["elegida"] is False
    assert len(list(carpeta.iterdir())) == 3


def test_elegir_descargas_tal_cual_crea_la_carpeta_del_cliente(entorno, tmp_path, monkeypatch):
    """La ventana abre en Descargas: «Seleccionar carpeta» sin entrar a ninguna devuelve Descargas."""
    client, _ = entorno
    _ejecucion(tmp_path)
    casa = tmp_path / "casa"
    descargas = casa / "Downloads"
    descargas.mkdir(parents=True)
    monkeypatch.setattr(srv.Path, "home", classmethod(lambda cls: casa))
    monkeypatch.setattr(dialogos, "elegir_carpeta", lambda *a, **k: descargas)

    r = client.post("/api/ejecuciones/exp123/clientes/0/exportar")

    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["carpeta"] == str(descargas / "Cliente SA" / "102026") and datos["elegida"] is False
    assert not (descargas / "102026").exists()


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


def test_descargar_un_archivo_con_guardar_como(entorno, tmp_path, monkeypatch):
    client, _ = entorno
    _ejecucion(tmp_path)
    destino = tmp_path / "otra" / "mi copia.xlsx"
    destino.parent.mkdir()
    monkeypatch.setattr(dialogos, "guardar_como", lambda *a, **k: destino)

    r = client.post("/api/ejecuciones/exp123/clientes/0/archivos/IVA VENTAS - X.xlsx/guardar")

    assert r.status_code == 200, r.text
    assert r.json() == {"ruta": str(destino)}
    assert destino.read_bytes() == b"IVA VENTAS - X.xlsx"


def test_guardar_como_cancelado_y_archivo_inexistente(entorno, tmp_path, monkeypatch):
    client, _ = entorno
    _ejecucion(tmp_path)
    monkeypatch.setattr(dialogos, "guardar_como", lambda *a, **k: None)

    assert client.post("/api/ejecuciones/exp123/clientes/0/archivos/IVA VENTAS - X.xlsx/guardar").json() == {"cancelado": True}
    assert client.post("/api/ejecuciones/exp123/clientes/0/archivos/no-existe.xlsx/guardar").status_code == 404


def test_solo_se_listan_los_archivos_de_este_control(tmp_path):
    """La carpeta del mes la comparten todos los controles: un Ventas no muestra los Compras de antes."""
    carpeta = tmp_path / "102026"
    carpeta.mkdir(parents=True)
    viejo = carpeta / "IVA COMPRAS - X - 01-10-2026 al 31-10-2026.xlsx"
    viejo.write_bytes(b"de otro control")
    hace_una_hora = time.time() - 3600
    os.utime(viejo, (hace_una_hora, hace_una_hora))
    (carpeta / "~$IVA VENTAS - X.xlsx").write_bytes(b"bloqueo de Excel")

    inicio = time.time()
    (carpeta / "IVA VENTAS - X - 05-10-2026 al 20-10-2026.xlsx").write_bytes(b"de este control")

    assert [a["nombre"] for a in srv.listar_archivos(carpeta, inicio)] == ["IVA VENTAS - X - 05-10-2026 al 20-10-2026.xlsx"]
    assert len(srv.listar_archivos(carpeta)) == 2  # sin filtro: todo menos el bloqueo de Excel


def test_no_se_puede_bajar_un_archivo_de_otro_control(entorno, tmp_path, monkeypatch):
    client, _ = entorno
    ej = _ejecucion(tmp_path)
    ajeno = "IVA COMPRAS - de otro control.xlsx"
    (tmp_path / "salidas" / "Cliente SA" / "102026" / ajeno).write_bytes(b"x")
    monkeypatch.setattr(dialogos, "guardar_como", lambda *a, **k: tmp_path / "copia.xlsx")

    r = client.post(f"/api/ejecuciones/{ej.id}/clientes/0/archivos/{ajeno}/guardar")

    assert r.status_code == 404


def test_ventas_y_compras_van_en_subcarpetas(tmp_path):
    origen = tmp_path / "salidas" / "Cliente SA" / "112026"
    origen.mkdir(parents=True)
    nombres = [
        "Mis comprobantes - Emitidos - X - 11-26 al 11-26.xlsx",
        "Mis comprobantes - Recibidos - X - 11-26 al 11-26.xlsx",
        "IVA VENTAS - X - 01-11-2026 al 30-11-2026.xlsx",
        "IVA COMPRAS - X - 01-11-2026 al 30-11-2026.xlsx",
        "AUX - Control de facturas Ventas con AFIP - 11-26 - X.xlsx",
        "AUX - Control de facturas Compras con AFIP - 11-26 - X.xlsx",
    ]
    for n in nombres:
        (origen / n).write_bytes(b"x")
    ej = srv.Ejecucion(
        id="ambas1", usuario="t", estado="finalizada",
        parametros={"desde": "01/11/2026", "hasta": "30/11/2026", "ventas": True, "compras": True},
        clientes=[srv.ClienteEnCurso("Cliente SA", [], carpeta=str(origen), archivos=srv.listar_archivos(origen))],
    )
    destino = tmp_path / "elegida"
    destino.mkdir()

    r = srv.exportar_archivos(ej, 0, destino)

    carpeta = destino / "112026"
    assert r["fallas"] == [] and len(r["copiados"]) == 6
    assert sorted(p.name for p in carpeta.iterdir()) == ["Compras", "Ventas"]
    assert sorted(p.name for p in (carpeta / "Ventas").iterdir()) == sorted(n for n in nombres if "Emitidos" in n or "VENTAS" in n or "Ventas" in n)
    assert sorted(p.name for p in (carpeta / "Compras").iterdir()) == sorted(n for n in nombres if "Recibidos" in n or "COMPRAS" in n or "Compras" in n)


def test_una_sola_operacion_queda_sin_subcarpetas(entorno, tmp_path, monkeypatch):
    client, _ = entorno
    ej = _ejecucion(tmp_path)  # solo los parámetros de fecha: ni ventas ni compras juntas
    ej.parametros.update({"ventas": True, "compras": False})
    destino = tmp_path / "elegida"
    destino.mkdir()
    monkeypatch.setattr(dialogos, "elegir_carpeta", lambda *a, **k: destino)

    r = client.post("/api/ejecuciones/exp123/clientes/0/exportar")

    assert r.status_code == 200, r.text
    assert sorted(p.name for p in (destino / "102026").iterdir()) == sorted(r.json()["copiados"])


def test_operacion_de_cada_archivo():
    assert srv.operacion_de("Mis comprobantes - Emitidos - X - 10-26 al 10-26.xlsx") == "Ventas"
    assert srv.operacion_de("Mis comprobantes - Recibidos - X - 10-26 al 10-26.xlsx") == "Compras"
    assert srv.operacion_de("IVA VENTAS - X.xlsx") == "Ventas"
    assert srv.operacion_de("IVA COMPRAS - X.xlsx") == "Compras"
    assert srv.operacion_de("AUX - Control de facturas Compras con AFIP - 10-26 - X.xlsx") == "Compras"
    assert srv.operacion_de("otro.xlsx") is None


def test_en_servidor_no_se_abren_ventanas(entorno, tmp_path, monkeypatch):
    client, _ = entorno
    _ejecucion(tmp_path)
    monkeypatch.setattr(config, "DIALOGOS_LOCALES", False)

    def no_llamar(*a, **k):
        raise AssertionError("no tiene que abrir ventanas en el servidor")

    monkeypatch.setattr(dialogos, "elegir_carpeta", no_llamar)
    monkeypatch.setattr(dialogos, "guardar_como", no_llamar)

    assert client.post("/api/ejecuciones/exp123/clientes/0/exportar").status_code == 409
    assert client.post("/api/ejecuciones/exp123/clientes/0/archivos/IVA VENTAS - X.xlsx/guardar").status_code == 409
    assert client.get("/api/ajustes").json()["dialogos_locales"] is False
    # Bajar por el navegador sigue andando.
    assert client.get("/api/ejecuciones/exp123/clientes/0/archivos/IVA VENTAS - X.xlsx").status_code == 200


def test_zip_con_la_misma_estructura_de_carpetas(entorno, tmp_path, monkeypatch):
    client, _ = entorno
    ej = _ejecucion(tmp_path)
    ej.parametros.update({"ventas": True, "compras": True})
    monkeypatch.setattr(config, "DIALOGOS_LOCALES", False)

    r = client.get("/api/ejecuciones/exp123/clientes/0/archivos.zip")

    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert "Cliente%20SA%20-%20102026.zip" in r.headers["content-disposition"]
    nombres = sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert nombres == [
        "Cliente SA/102026/AUX - Control X.xlsx",
        "Cliente SA/102026/Ventas/IVA VENTAS - X.xlsx",
        "Cliente SA/102026/Ventas/Mis comprobantes - Emitidos - X.xlsx",
    ]
