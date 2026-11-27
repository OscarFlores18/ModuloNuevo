"""Lectura de imágenes con Gemini (Conversor De Facturas).

Si hay una clave de Gemini, las fotos y capturas se leen con Gemini Flash: devuelve
los campos del comprobante (o la tabla) en JSON, con el IVA desglosado por alícuota
y sin CAE, y después pasan por las mismas verificaciones que el OCR (dígito
verificador del CUIT, con el cambio 0↔8 de las ticketeadoras; que los importes
sumen el total, etc.).

La clave y la configuración salen de la variable de entorno o de un ``.env`` (ver
entorno.py):
- ``GEMINI_API_KEY``: la clave.
- ``GEMINI_MODELO``: el modelo, por si Google retira el de ``MODELO``.
- ``GEMINI_PLAZO_S``: en modo «auto», cuánto se le espera antes de leer con el OCR
  local (15 s).

Sin clave, o si Gemini falla (sin internet, cuota gratuita agotada…), la app
sigue con el OCR local (PaddleOCR o Tesseract) y lo avisa.
"""

from __future__ import annotations

import io
import json
import logging
import re
import time

from .entorno import numero, valor

logger = logging.getLogger(__name__)

# gemini-2.5-flash ya no se habilita a cuentas nuevas (404): Google recomienda este.
MODELO = "gemini-3.8-flash"
# Más grande no lee mejor y tarda más; el texto de una factura sigue nítido.
LADO_MAXIMO = 1600
# Por pedido: si un modelo tarda más, se pasa al siguiente.
ESPERA_S = 60
# En modo «auto» del Conversor: si Gemini no contesta en este tiempo (o da 429/503),
# la imagen se lee con el OCR local. Se cambia con GEMINI_PLAZO_S.
PLAZO_AUTO_S = 15.0
# Google rechaza (400) un pedido con un plazo menor a este.
PLAZO_MINIMO_S = 10.0
MOTOR = "gemini"


class GeminiNoDisponible(RuntimeError):
    """Gemini no respondió: el mensaje es para mostrarle a la persona."""


def clave() -> str:
    return valor("GEMINI_API_KEY")


def modelo() -> str:
    return valor("GEMINI_MODELO") or MODELO


def plazo_auto() -> float:
    return numero("GEMINI_PLAZO_S", PLAZO_AUTO_S)


def disponible() -> bool:
    if not clave():
        return False
    try:
        import google.genai  # noqa: F401
    except ImportError:
        return False
    return True


PROMPT = """Sos un auditor contable que lee comprobantes fiscales argentinos (tiques térmicos, tique factura A/B/C, facturas electrónicas, notas de crédito/débito, remitos).
Leé la imagen y devolvé SOLO un JSON válido con esta forma, sin texto antes ni después:

{
  "tipo": "comprobante" o "tabla",
  "encabezado": {
    "emisor_razon_social": "string",
    "emisor_cuit": "11 dígitos sin guiones",
    "receptor_razon_social": "string" o null,
    "receptor_cuit": "11 dígitos (o DNI) sin guiones" o null,
    "tipo_comprobante": "código ARCA - nombre, p. ej. '81 - Tique Factura A'",
    "punto_venta": "5 dígitos",
    "numero_comprobante": "8 dígitos",
    "fecha": "DD/MM/AAAA"
  },
  "importes": {
    "neto_gravado": 0.00,
    "no_gravado": 0.00,
    "exento": 0.00,
    "desglose_iva": [ {"alicuota": 21.0, "base_imponible": 0.00, "importe_iva": 0.00} ],
    "desglose_tributos": [ {"codigo": "07", "descripcion": "Percepción IIBB Bs As", "base_imponible": 0.00, "importe": 0.00} ],
    "impuestos_internos": 0.00,
    "percepcion_iibb": 0.00,
    "otros_tributos": 0.00,
    "total": 0.00
  },
  "items": [ {"codigo": "string" o null, "descripcion": "string", "cantidad": 0.00, "precio_unitario": 0.00, "alicuota_iva": 21.0, "importe": 0.00, "impuestos_internos": 0.00 o null, "producto": "categoría"} ],
  "validaciones": {
    "cuit_emisor_valido": true,
    "cuit_receptor_valido": true,
    "suma_items_coincide": true,
    "total_coincide": true,
    "advertencias": ["string"]
  },
  "filas": [ ["celda", "celda", ...] ],
  "texto": "todo el texto visible de la imagen, respetando los renglones"
}

Códigos ARCA de "tipo_comprobante": 1 Factura A, 6 Factura B, 11 Factura C, 51 Factura M, 19 Factura E, 2 ND A, 7 ND B, 12 ND C, 3 NC A, 8 NC B, 13 NC C, 81 Tique Factura A, 82 Tique Factura B, 111 Tique Factura C, 83 Tique, 91 Remito.

Reglas:
1. Ceros y ochos en impresoras térmicas: en la tipografía de las ticketeadoras el cero ('0') suele tener una barra diagonal en el centro y en fotos borrosas parece un ocho ('8'). Mirá la geometría de cada dígito y el contexto numérico: un 8 tiene dos lazos cerrados apilados; un 0 tachado es un solo óvalo con una barra. No confundas ceros tachados con ochos.
2. CUIT (módulo 11): tiene 11 dígitos y el último es verificador (pesos 5, 4, 3, 2, 7, 6, 5, 4, 3, 2 sobre los 10 primeros; resto = suma mod 11; verificador = 11 - resto, 11 → 0). Si un CUIT leído no pasa la validación y tiene 0 u 8, cambiá 8 por 0 (y viceversa) hasta encontrar la combinación válida. El CUIT suele repetirse en «Ingresos Brutos»: usalo para confirmar.
3. Los tiques fiscales (controlador fiscal, p. ej. Tique Factura A/B) no llevan CAE. No incluyas ningún campo CAE.
4. IVA por alícuota: no agrupes el IVA en un solo número. Cada alícuota presente (21, 10.5, 27, 5, 2.5) va en "desglose_iva" con su base imponible y su importe de IVA. Si el comprobante no discrimina IVA (Factura B/C, tique a consumidor final), "desglose_iva" va vacío.
5. Cuadratura: verificá que los ítems sumen el neto gravado y que neto gravado + no gravado + exento + suma de IVA + otros tributos = total (tolerancia ±0.05). Si algo no cierra, volvé a mirar los dígitos dudosos; si sigue sin cerrar, dejá lo que leíste y explicalo en "advertencias".
6. "tipo": "comprobante" si es una factura, tique, nota, remito o recibo; "tabla" si es una planilla o un listado. Para "tabla" completá "filas" (la primera, los títulos) y dejá encabezado, importes e items vacíos; para "comprobante", "filas" va vacío.
7. Otros tributos: cada percepción o impuesto que no es IVA (impuestos internos, percepción de IIBB, percepción de IVA, tasas municipales…) va en "desglose_tributos" con su código si figura (p. ej. «07 - PERCEPCION IIBB BS AS» → codigo "07"), la descripción, la base imponible y el importe. "otros_tributos" es la suma de esos importes (o el «Importe total otros tributos» del comprobante). Si no hay, la lista va vacía. La leyenda «Régimen de Transparencia Fiscal al Consumidor (Ley 27.743)» repite importes: no la cuentes como tributo.
8. Importes como números JSON con punto decimal (1234.5), sin $ ni separador de miles. En Argentina «1.234,50» es 1234.5.
9. Si un dato no está o no se lee, usá null. No inventes nada.
10. Impuestos internos y percepción de IIBB de Buenos Aires: además de ir en "desglose_tributos", poné su total en "impuestos_internos" y "percepcion_iibb" (ARBA) de "importes"; en cada ítem, "impuestos_internos" si figura. "otros_tributos" sigue siendo la suma de todo "desglose_tributos".
11. "producto" de cada ítem: la categoría que mejor le queda, una de estas: Mercaderías, Servicios, Honorarios, Combustible, Refrigerio (comidas y bebidas, p. ej. un restaurante), Gastos de limpieza, Alquiler.
12. "descripcion" de cada ítem tal como figura pero sin errores de lectura (p. ej. «AGUA MINERAL SMART WATER 591ML»).
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
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip())
    datos = json.loads(texto)
    if not isinstance(datos, dict):
        raise ValueError("Gemini no devolvió un objeto JSON.")
    return datos


# Si un modelo está saturado (503), falla del lado de Google (500) o tarda
# demasiado, se reintenta una vez y se pasa al siguiente. El «lite» es el que mejor
# aguanta los picos de demanda (respondió en 2 s cuando los otros daban 503).
RESPALDOS = ("gemini-3.5-flash", "gemini-3.1-flash-lite")
PAUSAS_S = (2,)


# Sin plazo (no hay OCR local para seguir): si todos los modelos dan el límite por
# minuto, se espera lo que pide Google (hasta esto) y se prueba una vez más.
ESPERA_CUPO_S = 65.0


def espera_por_cupo(exc) -> float | None:
    """Cuánto esperar ante un 429 por minuto (el «retryDelay» de Google, o 30 s);
    None si es el cupo del día (no tiene sentido esperar)."""
    texto = str(exc)
    if "PerDay" in texto:
        return None
    m = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", texto)
    return min(float(m.group(1)) + 1 if m else 30.0, numero("GEMINI_ESPERA_CUPO_S", ESPERA_CUPO_S))


def _pedir(cliente, types, errors, jpeg: bytes, plazo_s: float | None = None):
    """Con `plazo_s`, todo (modelos de respaldo incluidos) tiene que terminar en ese
    tiempo; si no, TimeoutError. Sin plazo, si todos los modelos están en el límite
    por minuto, espera y vuelve a probar una vez."""
    try:
        return _pedir_una_vuelta(cliente, types, errors, jpeg, plazo_s)
    except errors.APIError as exc:
        if plazo_s or exc.code != 429 or (espera := espera_por_cupo(exc)) is None:
            raise
        logger.info("Gemini: todos los modelos en el límite por minuto; se espera %.0f s y se reintenta.", espera)
        time.sleep(espera)
        return _pedir_una_vuelta(cliente, types, errors, jpeg, None)


def _pedir_una_vuelta(cliente, types, errors, jpeg: bytes, plazo_s: float | None = None):
    modelos = list(dict.fromkeys([modelo(), *RESPALDOS]))
    if plazo_s:
        # Con plazo, primero el más rápido: el resto suele tardar más que el plazo.
        modelos = list(dict.fromkeys([RESPALDOS[-1], *modelos]))
    fin = time.monotonic() + plazo_s if plazo_s else None
    ultimo: Exception | None = None
    for m in modelos:
        for intento in range(len(PAUSAS_S) + 1):
            restante = fin - time.monotonic() if fin else None
            if restante is not None and restante <= 0.2:
                raise TimeoutError(f"Gemini no contestó en {plazo_s:g} s.")
            # Google no acepta plazos menores a PLAZO_MINIMO_S (responde 400).
            extra = {"http_options": types.HttpOptions(timeout=int(max(restante, PLAZO_MINIMO_S) * 1000))} if restante else {}
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
                        **extra,
                    ),
                )
            except errors.APIError as exc:
                if exc.code == 429:
                    # El cupo gratuito es por modelo: sin reintentar, al siguiente.
                    if not isinstance(ultimo, errors.APIError) or ultimo.code != 429:
                        ultimo = exc
                    logger.info("Gemini %s sin cupo (429).", m)
                    break
                if exc.code not in (500, 503, 504):
                    raise
                if exc.code == 504:
                    # Se le acabó el plazo: reintentar el mismo modelo tarda lo mismo.
                    if not (isinstance(ultimo, errors.APIError) and ultimo.code == 429):
                        ultimo = exc
                    logger.info("Gemini %s no contestó a tiempo (504).", m)
                    break
                if not (isinstance(ultimo, errors.APIError) and ultimo.code == 429):
                    ultimo = exc
                logger.info("Gemini %s respondió %s (intento %s).", m, exc.code, intento + 1)
            except Exception as exc:  # tiempo agotado: al siguiente modelo
                ultimo = exc
                logger.info("Gemini %s no respondió a tiempo: %s", m, exc)
                break
            if intento < len(PAUSAS_S) and not fin:
                time.sleep(PAUSAS_S[intento])
    assert ultimo is not None
    raise ultimo


def leer_imagen(imagen, plazo_s: float | None = None) -> dict:
    """La imagen (PIL, RGB) → el JSON del PROMPT. Lanza GeminiNoDisponible con un
    mensaje claro si no se pudo (o si con `plazo_s` no contestó a tiempo)."""
    from google import genai
    from google.genai import errors, types

    imagen = _achicar(imagen)
    buffer = io.BytesIO()
    imagen.convert("RGB").save(buffer, format="JPEG", quality=90)
    try:
        # Sin los reintentos propios del SDK (5 por pedido, con esperas de hasta 60 s):
        # los maneja `_pedir`, que ante un 503 pasa enseguida al modelo siguiente.
        cliente = genai.Client(
            api_key=clave(),
            http_options=types.HttpOptions(timeout=ESPERA_S * 1000, retry_options=types.HttpRetryOptions(attempts=1)),
        )
        respuesta = _pedir(cliente, types, errors, buffer.getvalue(), plazo_s)
    except errors.APIError as exc:
        logger.warning("Gemini respondió %s: %s", exc.code, exc.message)
        if exc.code == 504:
            raise GeminiNoDisponible(f"Gemini no contestó en {plazo_s or ESPERA_S:g} s.") from exc
        if exc.code in (500, 503):
            raise GeminiNoDisponible("Gemini está saturado en este momento (mucha demanda). Probá de nuevo en un rato.") from exc
        if exc.code == 429:
            if "PerDay" in str(exc):
                raise GeminiNoDisponible("Gemini: se usó todo el cupo gratuito del día. Se renueva mañana.") from exc
            raise GeminiNoDisponible("Gemini: se llegó al límite gratuito por minuto. Esperá un minuto y volvé a subir la imagen.") from exc
        if exc.code == 404:
            raise GeminiNoDisponible(f"Gemini: el modelo «{modelo()}» ya no está disponible. Poné otro en GEMINI_MODELO (por ejemplo gemini-3.8-flash).") from exc
        if exc.code in (400, 401, 403) and re.search(r"api.?key|permission|unauth", str(exc.message or ""), re.I):
            raise GeminiNoDisponible("Gemini: la clave (GEMINI_API_KEY) no es válida o no tiene permiso.") from exc
        raise GeminiNoDisponible(f"Gemini no pudo leer la imagen (error {exc.code}).") from exc
    except Exception as exc:  # sin internet, tiempo agotado…
        logger.warning("Gemini no respondió: %s", exc)
        if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower() or "timeout" in type(exc).__name__.lower():
            raise GeminiNoDisponible(f"Gemini no contestó en {plazo_s or ESPERA_S:g} s.") from exc
        raise GeminiNoDisponible("No se pudo conectar con Gemini (¿hay internet?).") from exc
    try:
        datos = _json_de(respuesta.text or "")
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Gemini devolvió algo que no es JSON: %r", (respuesta.text or "")[:300])
        raise GeminiNoDisponible("Gemini devolvió una respuesta que no se entiende.") from exc
    validaciones = datos.get("validaciones") if isinstance(datos.get("validaciones"), dict) else {}
    datos["validaciones"] = {**validaciones, "motor_utilizado": MOTOR}
    return datos
