from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.core.models import JobRequest


def _base(**cambios):
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


def test_fechas_validas():
    req = JobRequest(**_base())
    assert req.fecha_desde == "01/03/2025"


def test_fecha_invalida():
    with pytest.raises(ValidationError):
        JobRequest(**_base(fecha_desde="2025-03-01"))


def test_desde_posterior_a_hasta():
    with pytest.raises(ValidationError):
        JobRequest(**_base(fecha_desde="01/04/2025"))


def test_exige_ventas_o_compras():
    with pytest.raises(ValidationError):
        JobRequest(**_base(ventas=False, compras=False))


def test_la_clave_no_se_serializa():
    req = JobRequest(**_base(xubio_user="u", xubio_pass="super-secreta"))
    assert "super-secreta" not in req.model_dump_json()
    assert req.xubio_pass == "super-secreta"
