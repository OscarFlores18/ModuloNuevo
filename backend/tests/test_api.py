from __future__ import annotations

import time

from backend.app.core.models import JobProgress, JobResult


def _job_valido(**cambios):
    datos = {
        "clientes": ["Acme"],
        "fecha_desde": "01/03/2025",
        "fecha_hasta": "31/03/2025",
        "ventas": True,
        "compras": False,
        "pasos": ["arca"],
    }
    datos.update(cambios)
    return datos


def test_health(entorno):
    client, _ = entorno
    respuesta = client.get("/health")
    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "ok"


def test_crud_clientes(entorno):
    client, _ = entorno

    assert client.get("/api/clientes").json() == []

    alta = client.post(
        "/api/clientes",
        json={
            "nombre": "Acme",
            "arca": {"cuit_login": "20", "clave": "x", "cuit_representado": "21"},
        },
    )
    assert alta.status_code == 201, alta.text
    assert alta.json()["nombre"] == "Acme"
    assert alta.json()["arca"]["tiene_clave"] is True
    assert "clave" not in alta.json()["arca"]

    listado = client.get("/api/clientes").json()
    assert [c["nombre"] for c in listado] == ["Acme"]

    editado = client.put(
        "/api/clientes/Acme",
        json={
            "nombre": "Acme",
            "arca": {"cuit_login": "20", "clave": "", "cuit_representado": "21"},
        },
    )
    assert editado.status_code == 200
    # Una clave vacía al editar conserva la que había.
    assert editado.json()["arca"]["tiene_clave"] is True

    renombrado = client.post("/api/clientes/Acme/renombrar", json={"nuevo": "Acme 2"})
    assert renombrado.status_code == 200
    assert client.get("/api/clientes/Acme").status_code == 404

    assert client.delete("/api/clientes/Acme 2").status_code == 204
    assert client.get("/api/clientes").json() == []


def test_conserva_claves_extra(entorno):
    client, _ = entorno
    client.post(
        "/api/clientes",
        json={"nombre": "Acme", "arca": {"cuit_login": "20"}, "agip": {"numero": "123"}},
    )
    datos = client.get("/api/clientes/Acme").json()
    assert datos["agip"] == {"numero": "123"}


def test_credenciales_no_devuelven_password(entorno):
    client, _ = entorno

    respuesta = client.put(
        "/api/xubio/credenciales",
        json={"user": "operador", "pass": "super-secreta", "recordar": True},
    )
    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "user": "operador",
        "recordar": True,
        "tiene_pass": True,
    }

    guardadas = client.get("/api/xubio/credenciales").json()
    assert "super-secreta" not in str(guardadas)


def test_job_con_cliente_inexistente_da_400(entorno):
    client, _ = entorno
    respuesta = client.post("/api/jobs", json=_job_valido(clientes=["NoExiste"]))
    assert respuesta.status_code == 400


def test_job_con_fecha_invalida_da_422(cliente_demo):
    respuesta = cliente_demo.post(
        "/api/jobs", json=_job_valido(fecha_desde="2025-03-01")
    )
    assert respuesta.status_code == 422


def test_job_completa_y_expone_archivos(cliente_demo, monkeypatch, tmp_path):
    archivo = tmp_path / "salidas" / "AUX - Control de facturas.xlsx"
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_bytes(b"contenido-xlsx")

    def fake_ejecutar(**kwargs):
        kwargs["log"]("      [OK] archivo de prueba")
        kwargs["on_progreso"](
            JobProgress(actual=1, total=1, cliente="Acme", paso="fin")
        )
        return JobResult(archivos=[str(archivo)])

    monkeypatch.setattr(
        "backend.app.services.jobs.ejecutar_control_facturas", fake_ejecutar
    )

    alta = cliente_demo.post("/api/jobs", json=_job_valido())
    assert alta.status_code == 201, alta.text
    job_id = alta.json()["id"]

    estado = None
    for _ in range(100):
        estado = cliente_demo.get(f"/api/jobs/{job_id}").json()
        if estado["status"] in {"succeeded", "failed", "partial", "cancelled"}:
            break
        time.sleep(0.05)

    assert estado is not None
    assert estado["status"] == "succeeded"
    assert estado["progress"]["actual"] == 1
    assert [a["nombre"] for a in estado["files"]] == [archivo.name]
    assert "ruta" not in estado["files"][0]

    logs = cliente_demo.get(f"/api/jobs/{job_id}/logs").json()
    assert any("archivo de prueba" in entrada["message"] for entrada in logs)

    descarga = cliente_demo.get(f"/api/jobs/{job_id}/files/{archivo.name}")
    assert descarga.status_code == 200
    assert descarga.content == b"contenido-xlsx"

    assert cliente_demo.get(f"/api/jobs/{job_id}/files/otro.xlsx").status_code == 404
