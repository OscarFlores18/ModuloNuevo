"""Lectura de imágenes con Gemini (Conversor De Facturas).

Si hay una clave de Gemini, las fotos y capturas se leen con Gemini Flash
en vez de Tesseract: devuelve los campos del comprobante (o la tabla) en JSON y
después pasan por las mismas verificaciones que el OCR (dígito verificador del
CUIT, que los importes sumen el total, etc.).

La clave sale de, en este orden:
1. La variable de entorno ``GEMINI_API_KEY``.
2. Un archivo ``.env`` con ``GEMINI_API_KEY=...`` en la carpeta de datos de la app
   (``Documentos\\ControlFCT\\data`` instalada; ``backend/data`` en desarrollo) o en
   ``backend/``.

El modelo se puede cambiar con ``GEMINI_MODELO`` (variable o ``.env``), por si Google
retira el de ``MODELO``.

Sin clave, o si Gemini falla (sin internet, cuota gratuita agotada…), la app
sigue con el OCR de Tesseract y lo avisa.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import time
from pathlib import Path

from ..core import config

logger = logging.getLogger(__name__)

# gemini-2.5-flash ya no se habilita a cuentas nuevas (404): Google recomienda este.
MODELO = "gemini-3.8-flash"
# Más grande no lee mejor y tarda más; el texto de una factura sigue nítido.
LADO_MAXIMO = 1600
# Por pedido: si un modelo tarda más, se pasa al siguiente.
ESPERA_S = 60


class GeminiNoDisponible(RuntimeError):
    """Gemini no respondió: el mensaje es para mostrarle a la persona."""


def _de_archivo_env(ruta: Path, nombre: str = "GEMINI_API_KEY") -> str:
    try:
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            m = re.match(rf"\s*(?:export\s+)?{nombre}\s*=\s*(.*)$", linea)
            if m:
                return m.group(1).strip().strip("'\"")
    except OSError:
        pass
    return ""


def _valor(nombre: str) -> str:
    if valor := os.getenv(nombre, "").strip():
        return valor
    for carpeta in (config.DATA_DIR, config.BACKEND_DIR):
        if valor := _de_archivo_env(Path(carpeta) / ".env", nombre):
            return valor
    return ""


def clave() -> str:
    return _valor("GEMINI_API_KEY")


def modelo() -> str:
    return _valor("GEMINI_MODELO") or MODELO


def disponible() -> bool:
    if not clave():
        return False
    try:
        import google.genai  # noqa: F401
    except ImportError:
        return False
    return True


PROMPT = """Sos un lector de comprobantes argentinos (facturas, notas de crédito/débito, tiques, recibos).
Leé la imagen y devolvé SOLO un JSON con esta forma, sin texto antes ni después:

{
  "tipo": "comprobante" o "tabla",
  "encabezado": {
    "emisor": razón social de quien emite,
    "emisor_cuit": CUIT del emisor, 11 dígitos sin guiones,
    "receptor": razón social de quien recibe (el cliente / "Señor(es)"),
    "receptor_cuit": CUIT o DNI del receptor, solo dígitos,
    "tipo_codigo": código ARCA del tipo de comprobante (1 Factura A, 6 Factura B, 11 Factura C, 3 NC A, 8 NC B, 13 NC C, 2 ND A, 7 ND B, 12 ND C, 81 Tique Factura A, 82 Tique Factura B, 83 Tique, 111 Tique Factura C, 51 Factura M, 19 Factura E), o null,
    "punto_de_venta": número de punto de venta, o null,
    "numero": número del comprobante (sin el punto de venta), o null,
    "fecha": fecha de emisión en formato dd/mm/aaaa,
    "cae": CAE o CAI de 14 dígitos, o ""
  },
  "items": [ {"codigo": "", "descripcion": "", "cantidad": número o null, "precio": número o null, "alicuota": % de IVA o null, "importe": número o null, "impuestos_internos": número o null} ],
  "totales": {"neto_gravado": número o null, "no_gravado": número o null, "exento": número o null, "iva": número o null, "impuestos_internos": número o null, "percepcion_iibb": número o null, "otros_tributos": número o null, "total": número o null},
  "filas": [ ["celda", "celda", ...] ],
  "texto": todo el texto visible de la imagen, respetando los renglones
}

Reglas:
- "tipo": "comprobante" si es una factura, tique, nota o recibo; "tabla" si es una planilla o un listado.
- Para "comprobante" completá encabezado, items y totales; "filas" va vacío.
- Para "tabla" completá "filas" con cada renglón (la primera fila, los títulos) y dejá encabezado, items y totales vacíos.
- Los importes como números JSON con punto decimal (1234.5), sin $ ni separador de miles. En Argentina «1.234,50» es 1234.5.
- "iva" es la suma de todo el IVA discriminado.
- "impuestos_internos": el total de impuestos internos (y en cada ítem, el suyo si figura).
- "percepcion_iibb": la percepción de Ingresos Brutos de Buenos Aires (ARBA).
- "otros_tributos": el resto (percepción de IVA, IIBB de otras jurisdicciones, etc.), sin repetir lo anterior.
- Si un dato no está o no se lee, usá "" o null. No inventes nada.
"""


def _achicar(imagen):
    """Proporcional, sin pasar de LADO_MAXIMO px de ancho o alto."""
    from PIL import Image

    lado = max(imagen.size)
    if lado <= LADO_MAXIMO:
        return imagen
    escala = LADO_MAXIMO / lado
    return imagen.resize((max(1, round(imagen.width * escala)), max(1, round(imagen.height * escala))), Image.LANCZOS)


def _json_de(texto: str) -> dict:
    texto = texto.strip()
    # Por las dudas viene envuelto en ```json … ```.
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto)
    datos = json.loads(texto)
    if not isinstance(datos, dict):
        raise ValueError("Gemini no devolvió un objeto JSON.")
    return datos


# Si un modelo está saturado (503), falla del lado de Google (500) o tarda
# demasiado, se reintenta una vez y se pasa al siguiente. El «lite» es el que mejor
# aguanta los picos de demanda (respondió en 2 s cuando los otros daban 503).
RESPALDOS = ("gemini-3.5-flash", "gemini-3.1-flash-lite")
PAUSAS_S = (2,)


def _pedir(cliente, types, errors, jpeg: bytes):
    modelos = list(dict.fromkeys([modelo(), *RESPALDOS]))
    ultimo: Exception | None = None
    for m in modelos:
        for intento in range(len(PAUSAS_S) + 1):
            try:
                return cliente.models.generate_content(
                    model=m,
                    contents=[types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"), PROMPT],
                    # Sin «pensar»: copiar los datos de una factura no lo necesita y
                    # responde mucho más rápido (las verificaciones atrapan los errores).
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0,
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    ),
                )
            except errors.APIError as exc:
                if exc.code not in (500, 503, 504):
                    raise
                ultimo = exc
                logger.info("Gemini %s respondió %s (intento %s).", m, exc.code, intento + 1)
            except Exception as exc:  # tiempo agotado: al siguiente modelo
                ultimo = exc
                logger.info("Gemini %s no respondió a tiempo: %s", m, exc)
                break
            if intento < len(PAUSAS_S):
                time.sleep(PAUSAS_S[intento])
    assert ultimo is not None
    raise ultimo


def leer_imagen(imagen) -> dict:
    """La imagen (PIL, RGB) → el JSON del PROMPT. Lanza GeminiNoDisponible con un
    mensaje claro si no se pudo."""
    from google import genai
    from google.genai import errors, types

    imagen = _achicar(imagen)
    buffer = io.BytesIO()
    imagen.save(buffer, format="JPEG", quality=90)
    try:
        # Sin los reintentos propios del SDK (5 por pedido, con esperas de hasta 60 s):
        # los maneja `_pedir`, que ante un 503 pasa enseguida al modelo siguiente.
        cliente = genai.Client(
            api_key=clave(),
            http_options=types.HttpOptions(timeout=ESPERA_S * 1000, retry_options=types.HttpRetryOptions(attempts=1)),
        )
        respuesta = _pedir(cliente, types, errors, buffer.getvalue())
    except errors.APIError as exc:
        logger.warning("Gemini respondió %s: %s", exc.code, exc.message)
        if exc.code in (500, 503):
            raise GeminiNoDisponible("Gemini está saturado en este momento (mucha demanda). Probá de nuevo en un rato.") from exc
        if exc.code == 429:
            raise GeminiNoDisponible("Gemini: se llegó al límite gratuito por minuto. Esperá un minuto y volvé a subir la imagen.") from exc
        if exc.code == 404:
            raise GeminiNoDisponible(f"Gemini: el modelo «{modelo()}» ya no está disponible. Poné otro en GEMINI_MODELO (por ejemplo gemini-3.8-flash).") from exc
        if exc.code in (400, 401, 403) and re.search(r"api.?key|permission|unauth", str(exc.message or ""), re.I):
            raise GeminiNoDisponible("Gemini: la clave (GEMINI_API_KEY) no es válida o no tiene permiso.") from exc
        raise GeminiNoDisponible(f"Gemini no pudo leer la imagen (error {exc.code}).") from exc
    except Exception as exc:  # sin internet, tiempo agotado…
        logger.warning("Gemini no respondió: %s", exc)
        raise GeminiNoDisponible("No se pudo conectar con Gemini (¿hay internet?).") from exc
    try:
        return _json_de(respuesta.text or "")
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Gemini devolvió algo que no es JSON: %r", (respuesta.text or "")[:300])
        raise GeminiNoDisponible("Gemini devolvió una respuesta que no se entiende.") from exc
