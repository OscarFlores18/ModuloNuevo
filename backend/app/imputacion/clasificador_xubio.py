"""Qué producto/servicio de Xubio le corresponde a cada línea de una compra.

Dos capas:

1. **Memoria por CUIT** (determinística): si una persona ya imputó a mano ese
   proveedor con esa alícuota, se usa ese producto (``confianza = 1.0``,
   ``origen = "regla_cuit"``).
2. **Gemini**: si no hay regla, se filtra el catálogo por la alícuota de la línea
   (los de esa tasa, los que no dicen tasa y los comodines de esa tasa) y Gemini
   elige uno, con salida JSON validada por esquema.

Si Gemini no está (sin clave, sin internet, sin cupo) o devuelve un ID que no está en
la lista, la línea va al comodín de su alícuota con confianza 0 para que la revise
una persona. Nunca se devuelve un ID que no sea del catálogo de la empresa.

Configuración (variable de entorno o ``.env``):
- ``GEMINI_API_KEY``: la misma clave que usa el Conversor.
- ``GEMINI_MODELO_CLASIFICADOR``: el modelo (``gemini-2.5-flash`` por defecto). Si
  Google responde 404 (los 2.5 ya no se habilitan a cuentas nuevas), se prueba con
  el modelo del Conversor (``GEMINI_MODELO``).
"""

from __future__ import annotations

import json
import logging
from typing import Callable, Literal

from pydantic import BaseModel, Field

from ..extraccion import gemini
from ..extraccion.entorno import valor
from . import almacen
from .xubio_catalog_parser import ProductoXubio

logger = logging.getLogger(__name__)

MODELO = "gemini-2.5-flash"
TEMPERATURA = 0.1
UMBRAL_REVISION = 0.70
ESPERA_S = 60

Origen = Literal["regla_cuit", "gemini", "comodin", "sin_catalogo"]


class LineaCompra(BaseModel):
    descripcion: str = ""
    alicuota_iva: float | None = None
    importe_neto: float | None = None
    proveedor_nombre: str = ""
    proveedor_cuit: str = ""
    tipo_comprobante: str = ""


class RespuestaGemini(BaseModel):
    """El esquema que se le exige a Gemini (``response_schema``)."""

    producto_id: int
    confianza: float
    motivo: str


class Asignacion(BaseModel):
    producto_id: int | None
    nombre: str = ""  # el nombre exacto de Xubio
    confianza: float = 0.0
    origen: Origen
    motivo: str = ""
    requiere_revision: bool = True
    candidatos: list[int] = Field(default_factory=list)


PROMPT = """Eres un analista contable experto en imputación de comprobantes de compra en Argentina para el sistema ERP Xubio.

Tu tarea es analizar una línea de una factura de compra recibida y asignarle el `PRODUCTOID` más adecuado del catálogo personalizado de la empresa cliente.

DATOS DEL COMPROBANTE:
- Proveedor: {proveedor_nombre} (CUIT: {proveedor_cuit})
- Tipo de comprobante: {tipo_comprobante}
- Descripción / Concepto del ítem: "{descripcion_item}"
- Alícuota de IVA detectada en la línea: {alicuota_iva}%
- Importe neto: ${importe_neto}

CATÁLOGO DISPONIBLE DE LA EMPRESA (elige ESTRICTAMENTE uno de estos IDs):
{lista_productos_filtrados}

CRITERIOS DE DECISIÓN:
1. Coincidencia de alícuota: Prioriza ítems cuya tasa de IVA coincida con la alícuota de la línea ({alicuota_iva}%). Si la línea es Exenta o No Gravada (0%), elige únicamente un ítem marcado como exento.
2. Rubro contable: Deduce el gasto según la actividad del proveedor y el detalle (ej. telecomunicaciones/energía -> servicios públicos; estudios jurídicos/contables -> honorarios; hosting/software -> gastos de sistemas o software; estaciones de servicio -> combustible).
3. Específico sobre genérico: Si existe un rubro específico que encaja con seguridad, elígelo por encima de los comodines ("Producto al 21%", "Producto al 10.5%", "Producto Exento", "GASTOS VARIOS").
4. Manejo de incertidumbre: Si el proveedor o la descripción son demasiado ambiguos para determinar el rubro con certeza, asigna el producto comodín correspondiente a la alícuota ({alicuota_iva}%) y devuelve una `confianza` menor a 0.70 para revisión humana.
5. Restricción estricta: Nunca inventes IDs ni nombres. El `producto_id` devuelto debe existir literalmente en la lista proporcionada arriba.
"""


# --- Filtro y comodines ------------------------------------------------------------


def _misma_tasa(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) < 0.001


def filtrar_por_alicuota(productos: list[ProductoXubio], alicuota: float | None) -> list[ProductoXubio]:
    """Los de la misma tasa, los que no dicen tasa y los comodines de esa tasa. Sin
    alícuota en la línea (o si el filtro deja la lista vacía), todo el catálogo."""
    if alicuota is None:
        return list(productos)
    filtrados = [p for p in productos if p.alicuota_iva is None or _misma_tasa(p.alicuota_iva, alicuota)]
    return filtrados or list(productos)


def comodin(productos: list[ProductoXubio], alicuota: float | None) -> ProductoXubio | None:
    """El comodín de la alícuota («Producto al 21%»); si no hay, uno genérico sin tasa
    («GASTOS VARIOS»), salvo en una línea exenta, que solo va a un ítem exento."""
    genericos = [p for p in productos if p.es_generico]
    de_la_tasa = [p for p in genericos if _misma_tasa(p.alicuota_iva, alicuota)]
    if de_la_tasa:
        return de_la_tasa[0]
    if alicuota is not None and alicuota == 0:
        exentos = [p for p in productos if _misma_tasa(p.alicuota_iva, 0.0)]
        return exentos[0] if exentos else None
    sin_tasa = [p for p in genericos if p.alicuota_iva is None]
    return sin_tasa[0] if sin_tasa else None


# --- Prompt -----------------------------------------------------------------------


def _tasa(alicuota: float | None) -> str:
    return "no informada" if alicuota is None else f"{alicuota:g}"


def _importe(valor_: float | None) -> str:
    if valor_ is None:
        return "no informado"
    return f"{valor_:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _renglon(p: ProductoXubio) -> str:
    detalles = []
    if p.alicuota_iva is not None:
        detalles.append("exento" if p.alicuota_iva == 0 else f"IVA {p.alicuota_iva:g}%")
    if p.es_generico:
        detalles.append("comodín")
    if p.usrcode:
        detalles.append(f"código {p.usrcode}")
    return f"- ID {p.producto_id}: {p.nombre_limpio}" + (f" [{' | '.join(detalles)}]" if detalles else "")


def armar_prompt(linea: LineaCompra, candidatos: list[ProductoXubio]) -> str:
    return PROMPT.format(
        proveedor_nombre=linea.proveedor_nombre or "sin nombre",
        proveedor_cuit=almacen.solo_digitos(linea.proveedor_cuit) or "sin CUIT",
        tipo_comprobante=linea.tipo_comprobante or "no informado",
        descripcion_item=(linea.descripcion or "").replace('"', "'").strip() or "sin descripción",
        alicuota_iva=_tasa(linea.alicuota_iva),
        importe_neto=_importe(linea.importe_neto),
        lista_productos_filtrados="\n".join(_renglon(p) for p in candidatos),
    )


# --- Gemini -----------------------------------------------------------------------


def modelo() -> str:
    return valor("GEMINI_MODELO_CLASIFICADOR") or MODELO


def consultar_gemini(prompt: str) -> RespuestaGemini:
    """Una llamada a Gemini con salida JSON según `RespuestaGemini`."""
    from google import genai
    from google.genai import errors, types

    cliente = genai.Client(
        api_key=gemini.clave(),
        http_options=types.HttpOptions(timeout=ESPERA_S * 1000, retry_options=types.HttpRetryOptions(attempts=2)),
    )
    configuracion = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=RespuestaGemini,
        temperature=TEMPERATURA,
    )
    modelos = list(dict.fromkeys([modelo(), gemini.modelo()]))
    for i, m in enumerate(modelos):
        try:
            respuesta = cliente.models.generate_content(model=m, contents=prompt, config=configuracion)
            break
        except errors.APIError as exc:
            if exc.code == 404 and i + 1 < len(modelos):
                logger.info("Clasificador: el modelo %s no está disponible (404); se prueba con %s.", m, modelos[i + 1])
                continue
            raise
    if isinstance(respuesta.parsed, RespuestaGemini):
        return respuesta.parsed
    return RespuestaGemini.model_validate(json.loads(respuesta.text or ""))


Consultor = Callable[[str], RespuestaGemini]


# --- Clasificación -----------------------------------------------------------------


def _por_comodin(productos: list[ProductoXubio], linea: LineaCompra, motivo: str, candidatos: list[int]) -> Asignacion:
    p = comodin(productos, linea.alicuota_iva)
    return Asignacion(
        producto_id=p.producto_id if p else None,
        nombre=p.nombre if p else "",
        confianza=0.0,
        origen="comodin",
        motivo=motivo,
        requiere_revision=True,
        candidatos=candidatos,
    )


def clasificar_linea(
    empresa: str,
    linea: LineaCompra,
    productos: list[ProductoXubio] | None = None,
    consultor: Consultor | None = None,
) -> Asignacion:
    productos = almacen.catalogo(empresa) if productos is None else productos
    if not productos:
        return Asignacion(
            producto_id=None, origen="sin_catalogo", motivo="La empresa no tiene el catálogo de Xubio cargado."
        )
    por_id = {p.producto_id: p for p in productos}

    # Capa 1: memoria por CUIT.
    if linea.proveedor_cuit and (r := almacen.regla(empresa, linea.proveedor_cuit, linea.alicuota_iva)):
        if (p := por_id.get(r.producto_id)) is not None:
            almacen.contar_uso(empresa, linea.proveedor_cuit, linea.alicuota_iva)
            return Asignacion(
                producto_id=p.producto_id,
                nombre=p.nombre,
                confianza=1.0,
                origen="regla_cuit",
                motivo="Regla guardada para este proveedor y alícuota.",
                requiere_revision=False,
            )
        logger.info("Regla de %s apunta a %s, que ya no está en el catálogo: se ignora.", linea.proveedor_cuit, r.producto_id)

    # Capa 2: pre-filtro por IVA + Gemini.
    candidatos = filtrar_por_alicuota(productos, linea.alicuota_iva)
    ids = [p.producto_id for p in candidatos]
    if consultor is None:
        if not gemini.disponible():
            return _por_comodin(productos, linea, "Gemini no está configurado (falta GEMINI_API_KEY).", ids)
        consultor = consultar_gemini
    try:
        respuesta = consultor(armar_prompt(linea, candidatos))
    except Exception as exc:  # sin internet, sin cupo, respuesta inválida…
        logger.warning("Clasificador: Gemini falló: %s", exc)
        return _por_comodin(productos, linea, f"Gemini no respondió ({type(exc).__name__}).", ids)

    elegido = next((p for p in candidatos if p.producto_id == respuesta.producto_id), None)
    if elegido is None:
        logger.warning("Clasificador: Gemini devolvió el ID %s, que no está en la lista.", respuesta.producto_id)
        return _por_comodin(productos, linea, f"Gemini devolvió un ID que no está en el catálogo ({respuesta.producto_id}).", ids)
    confianza = max(0.0, min(1.0, float(respuesta.confianza)))
    return Asignacion(
        producto_id=elegido.producto_id,
        nombre=elegido.nombre,
        confianza=confianza,
        origen="gemini",
        motivo=respuesta.motivo.strip(),
        requiere_revision=confianza < UMBRAL_REVISION,
        candidatos=ids,
    )


def clasificar_lineas(
    empresa: str, lineas: list[LineaCompra], consultor: Consultor | None = None
) -> list[Asignacion]:
    """Varias líneas: el catálogo se lee una vez y las líneas iguales (mismo
    proveedor, alícuota y descripción) se le preguntan a Gemini una sola vez."""
    productos = almacen.catalogo(empresa)
    vistas: dict[tuple, Asignacion] = {}
    salida = []
    for linea in lineas:
        clave = (almacen.solo_digitos(linea.proveedor_cuit), almacen.clave_alicuota(linea.alicuota_iva), (linea.descripcion or "").strip().casefold())
        if clave not in vistas or vistas[clave].origen == "regla_cuit":
            vistas[clave] = clasificar_linea(empresa, linea, productos, consultor)
        salida.append(vistas[clave].model_copy())
    return salida


def corregir(
    empresa: str, linea: LineaCompra, producto_id: int
) -> almacen.Regla:
    """La corrección manual: valida que el producto sea del catálogo de la empresa y
    guarda la regla (CUIT + alícuota → producto) para las próximas compras."""
    if not any(p.producto_id == producto_id for p in almacen.catalogo(empresa)):
        raise ValueError(f"El producto {producto_id} no está en el catálogo de Xubio de «{empresa}».")
    return almacen.guardar_regla(empresa, linea.proveedor_cuit, linea.alicuota_iva, producto_id, linea.proveedor_nombre)
