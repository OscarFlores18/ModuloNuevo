"""Auditoría FO 04-01: cruce entre comprobantes de ARCA y el Libro IVA de Xubio.

Reglas:
- No escribe en consola: reporta con `log(mensaje)`.
- Devuelve la ruta del Excel generado.
- Si falta alguno de los dos insumos, falla con `ArchivoNoEncontrado`.
"""

import os
import re
import unicodedata
from collections import Counter
from typing import Callable

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.dataframe import dataframe_to_rows

from ..core.archivos_excel import guardar_excel
from ..core.controles import ordinal, ruta_siguiente_control
from ..core.errors import ArchivoIlegible, ArchivoNoEncontrado

def formatear_mes_anio(fecha_str):
    partes = fecha_str.split('/')
    if len(partes) == 3:
        return f"{partes[1]}-{partes[2][-2:]}"
    return fecha_str.replace('/', '-')

def _solo_digitos(valor) -> str:
    if valor is None or (isinstance(valor, float) and np.isnan(valor)):
        return ""
    texto = str(valor)
    if re.fullmatch(r"\d+\.0", texto):  # pandas lee el CUIT como float
        texto = texto[:-2]
    return re.sub(r"\D", "", texto)


def _nombre_legible(nombre) -> str:
    texto = " ".join(str(nombre or "").split())
    return texto.title() if texto.isupper() else texto


def _celda(valor):
    """Valor de una celda listo para JSON (sin NaN ni tipos de numpy)."""
    if valor is None:
        return None
    if isinstance(valor, (float, np.floating)):
        return None if np.isnan(valor) else float(valor)
    if isinstance(valor, (np.integer,)):
        return int(valor)
    texto = str(valor).strip()
    return texto or None


def _fila_vista(fila, columnas: list[tuple[str, object]]) -> list[dict]:
    """[(título, columna del DataFrame)] -> [{columna, valor}] para mostrar en la web."""
    return [
        {"columna": titulo, "valor": _celda(fila.get(col)) if col is not None else None}
        for titulo, col in columnas
    ]


def detectar_letras_distintas(
    df_arca, df_xubio, col_cuit_arca, col_prov_arca, col_fecha_arca=None, col_total_arca=None
) -> list[dict]:
    """Comprobantes del Libro IVA que en ARCA tienen el mismo CUIT, punto de
    venta y número pero otra letra (ej. ARCA «C-00002-00000006», Xubio «A-...»).

    Devuelve una entrada por comprobante: proveedor, comprobante de ARCA, la
    letra con la que quedó cargado en Xubio y las dos filas tal como están en
    cada planilla (para mostrarlas en la web con el error remarcado).
    """
    if col_cuit_arca is None or 'CUIT' not in df_xubio.columns:
        return []
    columnas_arca = [
        ("Fecha", col_fecha_arca),
        ("Tipo", 'Tipo' if 'Tipo' in df_arca.columns else None),
        ("Comprobante", 'Clave_Py'),
        ("Nro. Doc.", col_cuit_arca),
        ("Denominación", col_prov_arca),
        ("Imp. Total", col_total_arca),
    ]
    columnas_xubio = [
        ("Fecha", 'Fecha'),
        ("Tipo", 'Tipo'),
        ("Comprobante", 'Comprobante'),
        ("CUIT", 'CUIT'),
        ("Proveedor", 'Proveedor'),
        ("Condición IVA", 'Condición IVA'),
        ("Total", 'Total'),
    ]
    claves_arca = set(df_arca['Clave_Py'].astype(str))
    por_numero: dict[str, list[tuple[str, str, object]]] = {}
    for _, fila in df_arca.iterrows():
        letra, _, resto = str(fila['Clave_Py']).partition('-')
        if not resto:
            continue
        por_numero.setdefault(resto, []).append(
            (letra, _solo_digitos(fila.get(col_cuit_arca)), fila)
        )

    encontrados: list[dict] = []
    vistos: set[tuple[str, str]] = set()
    for _, fila in df_xubio.iterrows():
        comprobante = str(fila['Comprobante'])
        if comprobante in claves_arca:
            continue
        letra_x, _, resto = comprobante.partition('-')
        cuit_x = _solo_digitos(fila.get('CUIT'))
        if not resto or not cuit_x:
            continue
        for letra_a, cuit_a, fila_a in por_numero.get(resto, []):
            if cuit_a == cuit_x and letra_a != letra_x and (cuit_x, comprobante) not in vistos:
                vistos.add((cuit_x, comprobante))
                prov_a = fila_a.get(col_prov_arca) if col_prov_arca else None
                encontrados.append({
                    "proveedor": _nombre_legible(prov_a or fila.get('Proveedor')),
                    "cuit": cuit_x,
                    "comprobante_arca": f"{letra_a}-{resto}",
                    "comprobante_xubio": comprobante,
                    "letra_arca": letra_a,
                    "letra_xubio": letra_x,
                    "fila_arca": _fila_vista(fila_a, columnas_arca),
                    "fila_xubio": _fila_vista(fila, columnas_xubio),
                })
                break
    return encontrados


def texto_letras_distintas(cliente: str, operacion: str, letras: list[dict]) -> str:
    """«PICCININI – Compras: 1 comprobante con letra distinta ... Corregilo en Xubio.»"""
    n = len(letras)
    detalle = "; ".join(
        f"{l['proveedor']}, {l['comprobante_arca']} cargado como {l['letra_xubio']}"
        for l in letras
    )
    comprobantes = "1 comprobante" if n == 1 else f"{n} comprobantes"
    corregir = "Corregilo" if n == 1 else "Corregilos"
    return (
        f"{cliente} – {operacion}: {comprobantes} con letra distinta entre ARCA y Xubio "
        f"({detalle}). {corregir} en Xubio."
    )


def _valor(item: dict, columna: str):
    """El valor de una columna en una fila de la ventana de errores."""
    for celda in item.get("fila", []):
        if celda["columna"] == columna:
            return celda["valor"]
    return None


def _numero(valor) -> float | None:
    """Un importe de ARCA o de Xubio como número («604887,5», «1.234,56», 604887.5)."""
    if valor is None:
        return None
    if isinstance(valor, (int, float, np.integer, np.floating)):
        v = float(valor)
        return None if np.isnan(v) else v
    texto = str(valor).replace("$", "").replace(" ", "").strip()
    if not texto:
        return None
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


# Diferencias menores a esto son redondeos (centavos, tipo de cambio).
TOLERANCIA_IMPORTE = 1.0


def _clase_comprobante(tipo) -> str:
    """«FC», «NC» o «ND» según el tipo (para no confundir factura con nota)."""
    t = str(tipo or "").lower()
    if "crédito" in t or "credito" in t:
        return "NC"
    if "débito" in t or "debito" in t:
        return "ND"
    return "FC"


def _clave_identidad(df, col_cuit, col_comp, usa_cuit: bool = True) -> pd.Series:
    """«CUIT|A-00001-00000005|FC» por fila: lo que identifica un comprobante.

    En Compras va la CUIT (dos proveedores pueden repetir número). En Ventas el
    número lo pone la empresa, así que alcanza con comprobante + clase; la CUIT del
    cliente se controla aparte (consumidor final puede no tenerla)."""
    tipo = df['Tipo'] if 'Tipo' in df.columns else pd.Series([""] * len(df), index=df.index)
    if usa_cuit and col_cuit is not None and col_cuit in df.columns:
        cuit = df[col_cuit].map(_solo_digitos)
    else:
        cuit = pd.Series([""] * len(df), index=df.index)
    return cuit + "|" + df[col_comp].astype(str) + "|" + tipo.map(_clase_comprobante)


def _faltantes_por_cuit(df_arca, df_xubio, col_cuit_arca, usa_cuit: bool = True):
    """Filas de ARCA sin par en Xubio y al revés, comparando (CUIT +) comprobante +
    tipo (factura / nota de crédito / débito). Un comprobante cargado dos veces
    en Xubio no se repite como faltante: ya se informa como duplicado."""
    def claves(df, col_cuit, col_comp):
        return _clave_identidad(df, col_cuit, col_comp, usa_cuit)

    ka = claves(df_arca, col_cuit_arca, 'Clave_Py')
    kx = claves(df_xubio, 'CUIT', 'Comprobante')

    def sin_par(propias, ajenas):
        disponibles = Counter(ajenas)
        marca = []
        for k in propias:
            if disponibles[k] > 0:
                disponibles[k] -= 1
                marca.append(False)
            else:
                marca.append(True)
        return pd.Series(marca, index=propias.index, dtype=bool)

    falta_en_xubio = sin_par(ka, kx)
    falta_en_arca = sin_par(kx, ka) & ~kx.duplicated(keep=False)
    return df_arca[falta_en_xubio], df_xubio[falta_en_arca]


# --- Libro IVA de Xubio: columnas por nombre ---------------------------------
COLUMNAS_LIBRO_IVA = [
    'Fecha', 'Tipo', 'Comprobante', 'Proveedor', 'CUIT', 'Condición IVA',
    'Neto gravado 21%', 'IVA 21%', 'Neto gravado 10.5%', 'IVA 10.5%',
    'Neto gravado 27%', 'IVA 27%', 'Neto gravado 2.5%', 'IVA 2.5%',
    'Neto gravado 5%', 'IVA 5%', 'Retención IVA', 'Percepción IVA',
    'Percepción IIBB', 'Impuestos internos', 'No gravado exento', 'Total',
]
# Sin estas el cruce no tiene sentido: se avisa en vez de cruzar mal.
COLUMNAS_LIBRO_IVA_CLAVE = ('Fecha', 'Tipo', 'Comprobante', 'CUIT', 'Total')


def _norm_titulo(texto) -> str:
    sin_tildes = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return " ".join(sin_tildes.lower().replace(",", ".").split())


# Ventas trae «Cliente» donde Compras trae «Proveedor».
_SINONIMOS_LIBRO_IVA = {"cliente": "Proveedor", "razon social": "Proveedor", "cuit/cuil": "CUIT"}
_CANONICA_LIBRO_IVA = {_norm_titulo(c): c for c in COLUMNAS_LIBRO_IVA}


def fila_de_titulos_libro_iva(crudo: pd.DataFrame) -> int | None:
    """La fila con «Fecha» y «Comprobante» (Xubio pone arriba «Fecha desde:», etc.)."""
    for i in range(min(20, len(crudo))):
        titulos = {_norm_titulo(v) for v in crudo.iloc[i].tolist() if not pd.isna(v)}
        if "fecha" in titulos and "comprobante" in titulos:
            return i
    return None


def columnas_libro_iva(df: pd.DataFrame, nombre_archivo: str = "") -> pd.DataFrame:
    """El Libro IVA con las columnas de siempre, tomadas por su título (no por
    posición: si Xubio agrega o mueve una columna, el CUIT no cae en «Proveedor»).
    Las de importes que no vengan quedan vacías; sin las clave, ``ArchivoIlegible``."""
    origen: dict[str, object] = {}
    for c in df.columns:
        n = _norm_titulo(c)
        canonica = _CANONICA_LIBRO_IVA.get(n) or _SINONIMOS_LIBRO_IVA.get(n)
        if canonica and canonica not in origen:
            origen[canonica] = c
    faltan = [c for c in COLUMNAS_LIBRO_IVA_CLAVE if c not in origen]
    if faltan:
        raise ArchivoIlegible(
            f"El Libro IVA de Xubio{f' «{nombre_archivo}»' if nombre_archivo else ''} no tiene "
            f"{'la columna' if len(faltan) == 1 else 'las columnas'} {', '.join(faltan)}. "
            "Volvé a bajarlo de Xubio sin cambiarle las columnas.",
            detalle={"faltan": faltan},
        )
    return pd.DataFrame(
        {c: (df[origen[c]] if c in origen else pd.Series(np.nan, index=df.index)) for c in COLUMNAS_LIBRO_IVA},
        index=df.index,
    )


def _cuits_distintos_ventas(df_arca, df_xubio, columnas_arca, columnas_xubio, col_cuit_arca) -> list[dict]:
    """Ventas: el mismo comprobante (número y clase) cargado en Xubio a otro cliente.

    Solo cuando los dos lados traen una CUIT (11 dígitos): a consumidor final ARCA
    informa DNI o 0 y Xubio suele dejarla vacía, y eso no es un error."""
    if col_cuit_arca is None or 'CUIT' not in df_xubio.columns or 'Clave_Py' not in df_arca.columns:
        return []
    ka = _clave_identidad(df_arca, None, 'Clave_Py', usa_cuit=False)
    kx = _clave_identidad(df_xubio, None, 'Comprobante', usa_cuit=False)
    unicas_a, unicas_x = ka[~ka.duplicated(keep=False)], kx[~kx.duplicated(keep=False)]
    por_clave_x = {k: i for i, k in unicas_x.items()}
    salida = []
    for i, k in unicas_a.items():
        j = por_clave_x.get(k)
        if j is None:
            continue
        fa, fx = df_arca.loc[i], df_xubio.loc[j]
        cuit_a, cuit_x = _solo_digitos(fa.get(col_cuit_arca)), _solo_digitos(fx.get('CUIT'))
        if len(cuit_a) == 11 and len(cuit_x) == 11 and cuit_a != cuit_x:
            salida += [
                {"origen": "arca", "fila": _fila_vista(fa, columnas_arca)},
                {"origen": "xubio", "fila": _fila_vista(fx, columnas_xubio)},
            ]
    return salida


# Se muestran todos; el tope solo evita mandar miles de filas a la web.
MAX_FILAS_POR_ERROR = 500


def _importes_distintos(operacion, df_arca, df_xubio, columnas_arca, columnas_xubio, col_cuit_arca, col_total_arca):
    """Pares ARCA / Xubio del mismo comprobante (mismo CUIT en Compras, mismo tipo)
    cuyo total difiere en más de ``TOLERANCIA_IMPORTE``. Los importes de ARCA en
    otra moneda se pasan a pesos con su tipo de cambio. Solo se comparan
    comprobantes únicos de cada lado: los repetidos ya se informan aparte."""
    if col_total_arca is None or "Total" not in df_xubio.columns or "Clave_Py" not in df_arca.columns:
        return []
    usa_cuit = operacion == "Compras" and col_cuit_arca is not None and "CUIT" in df_xubio.columns
    col_tc = next((c for c in df_arca.columns if "tipo cambio" in str(c).lower()), None)
    tipo_a = df_arca["Tipo"] if "Tipo" in df_arca.columns else pd.Series([""] * len(df_arca), index=df_arca.index)
    tipo_x = df_xubio["Tipo"] if "Tipo" in df_xubio.columns else pd.Series([""] * len(df_xubio), index=df_xubio.index)

    def clave(cuit, comp, tipo):
        return (_solo_digitos(cuit) if usa_cuit else "", str(comp), _clase_comprobante(tipo))

    por_clave_a: dict[tuple, list] = {}
    for i, fila in df_arca.iterrows():
        por_clave_a.setdefault(clave(fila.get(col_cuit_arca) if usa_cuit else "", fila["Clave_Py"], tipo_a[i]), []).append(fila)
    por_clave_x: dict[tuple, list] = {}
    for i, fila in df_xubio.iterrows():
        if "extracto" in str(tipo_x[i]).lower():
            continue
        por_clave_x.setdefault(clave(fila.get("CUIT") if usa_cuit else "", fila["Comprobante"], tipo_x[i]), []).append(fila)

    salida = []
    for k, filas_x in por_clave_x.items():
        filas_a = por_clave_a.get(k)
        if not filas_a or len(filas_a) != 1 or len(filas_x) != 1:
            continue
        fa, fx = filas_a[0], filas_x[0]
        total_a, total_x = _numero(fa.get(col_total_arca)), _numero(fx.get("Total"))
        if total_a is None or total_x is None:
            continue
        tc = _numero(fa.get(col_tc)) if col_tc else None
        if tc and tc > 0:
            total_a *= tc
        if abs(abs(total_a) - abs(total_x)) > TOLERANCIA_IMPORTE:
            fila_a = _fila_vista(fa, columnas_arca)
            if tc and tc > 1:
                # El Imp. Total de ARCA está en la moneda original; «Aplicar» tiene
                # que escribir en el Libro IVA (en pesos) este valor, no aquel.
                fila_a.append({"columna": "Total en pesos", "valor": round(total_a, 2)})
            salida += [
                {"origen": "arca", "fila": fila_a},
                {"origen": "xubio", "fila": _fila_vista(fx, columnas_xubio)},
            ]
    return salida


# Las columnas de «Mis Comprobantes» que forman el Imp. Total (en minúsculas).
COLUMNAS_QUE_SUMAN = ("neto gravado total", "neto no gravado", "op. exentas", "otros tributos", "total iva")


def _totales_que_no_cierran(operacion, df_arca, df_xubio, columnas_arca, col_cuit_arca, col_total_arca):
    """Comprobantes de ARCA cuyas columnas (neto gravado, no gravado, exento,
    otros tributos e IVA) no suman el Imp. Total: ARCA no discrimina una parte
    (típico de combustibles). Los que no traen ningún desglose (facturas B/C) no
    se miran. Devuelve ``(filas, cuantos_bien_en_xubio)``: cuántos ya están en
    Xubio con el total completo."""
    if col_total_arca is None or "Clave_Py" not in df_arca.columns:
        return [], 0
    por_nombre = {str(c).strip().lower(): c for c in df_arca.columns}
    partes = [por_nombre[n] for n in COLUMNAS_QUE_SUMAN if n in por_nombre]
    if not partes:
        return [], 0
    usa_cuit = operacion == "Compras" and col_cuit_arca is not None and "CUIT" in df_xubio.columns
    totales_x: dict[tuple, float] = {}
    if "Total" in df_xubio.columns and "Comprobante" in df_xubio.columns:
        for _, fx in df_xubio.iterrows():
            total_x = _numero(fx.get("Total"))
            if total_x is not None:
                clave = (_solo_digitos(fx.get("CUIT")) if usa_cuit else "", str(fx["Comprobante"]), _clase_comprobante(fx.get("Tipo")))
                totales_x[clave] = total_x

    salida, bien_en_xubio = [], 0
    for _, fa in df_arca.iterrows():
        total = _numero(fa.get(col_total_arca))
        valores = [_numero(fa.get(c)) or 0.0 for c in partes]
        if total is None or not any(valores):
            continue
        suma = round(sum(valores), 2)
        diferencia = round(total - suma, 2)
        if abs(diferencia) <= TOLERANCIA_IMPORTE:
            continue
        clave = (_solo_digitos(fa.get(col_cuit_arca)) if usa_cuit else "", str(fa["Clave_Py"]), _clase_comprobante(fa.get("Tipo")))
        total_x = totales_x.get(clave)
        if total_x is not None and abs(abs(total_x) - abs(total)) <= TOLERANCIA_IMPORTE:
            bien_en_xubio += 1
        fila = _fila_vista(fa, columnas_arca) + [
            {"columna": "Suma columnas", "valor": suma},
            {"columna": "Diferencia", "valor": diferencia},
            {"columna": "Total Xubio", "valor": None if total_x is None else round(total_x, 2)},
        ]
        salida.append({"origen": "arca", "fila": fila})
    return salida, bien_en_xubio


def _clave_cuit(cuit, comprobante) -> tuple[str, str]:
    return (_solo_digitos(cuit), str(comprobante))


def diagnosticar(
    operacion: str,
    df_arca,
    df_xubio,
    df_faltantes_arca,
    df_faltantes_xubio,
    letras_distintas: list[dict],
    col_cuit_arca,
    col_prov_arca,
    col_fecha_arca,
    col_total_arca,
) -> list[dict]:
    """Los errores que encontró la auditoría, cada uno con cómo solucionarlo.

    Tipos: ``arca_vacio``, ``xubio_vacio``, ``letra_distinta``,
    ``faltante_en_xubio``, ``faltante_en_arca`` y ``duplicado``. Cada error
    trae hasta ``MAX_FILAS_POR_ERROR`` filas tal como están en la planilla.
    """
    libro = f"Libro IVA {operacion}"
    columnas_arca = [
        ("Fecha", col_fecha_arca),
        ("Tipo", 'Tipo' if 'Tipo' in df_arca.columns else None),
        ("Comprobante", 'Clave_Py'),
        ("Nro. Doc.", col_cuit_arca),
        ("Denominación", col_prov_arca),
        ("Imp. Total", col_total_arca),
    ]
    columnas_xubio = [
        ("Fecha", 'Fecha'),
        ("Tipo", 'Tipo'),
        ("Comprobante", 'Comprobante'),
        ("CUIT", 'CUIT'),
        ("Proveedor" if operacion == "Compras" else "Cliente", 'Proveedor'),
        ("Total", 'Total'),
    ]

    def filas(df, columnas, origen, excluir=frozenset(), col_cuit=None, col_comp=None):
        salida = []
        for _, fila in df.iterrows():
            if excluir and _clave_cuit(fila.get(col_cuit), fila.get(col_comp)) in excluir:
                continue
            salida.append({"origen": origen, "fila": _fila_vista(fila, columnas)})
        return salida

    def error(tipo, gravedad, titulo, detalle, acciones, items, hoja=None, a_corregir=None):
        return {
            "tipo": tipo,
            "gravedad": gravedad,  # «grave»: el control no sirve; «corregir»: cargar bien en Xubio
            "operacion": operacion,
            "titulo": titulo,
            "detalle": detalle,
            "acciones": acciones,  # qué hacer, en etiquetas cortas («Corregir en Xubio»)
            "total": len(items),
            "comprobantes": items[:MAX_FILAS_POR_ERROR],
            # Cuántos hay que tocar de verdad (sin los que son normales).
            "a_corregir": len(items) if a_corregir is None else a_corregir,
            "hoja": hoja,
        }

    errores: list[dict] = []

    # Un lado vacío: el cruce entero queda mal, no tiene sentido listar faltantes.
    if len(df_arca) == 0 and len(df_xubio) > 0:
        errores.append(error(
            "arca_vacio", "grave",
            "ARCA no devolvió comprobantes",
            f"Xubio tiene {len(df_xubio)} en el {libro}; ARCA, ninguno. "
            "El CUIT representado tiene que ser el de la empresa.",
            ["Corregir CUIT del cliente"],
            [],
        ))
        return errores
    if len(df_xubio) == 0 and len(df_arca) > 0:
        errores.append(error(
            "xubio_vacio", "grave",
            f"Xubio no devolvió el {libro}",
            f"ARCA tiene {len(df_arca)}; Xubio, ninguno. "
            "La empresa en Xubio tiene que llamarse igual que el cliente.",
            ["Revisar empresa en Xubio"],
            [],
        ))
        return errores

    if letras_distintas:
        errores.append(error(
            "letra_distinta", "corregir",
            "Letra distinta entre ARCA y Xubio",
            "Mismo CUIT, punto de venta y número; cambia solo la letra.",
            ["Corregir en Xubio"],
            letras_distintas,
        ))

    # Las letras distintas ya se informan aparte: no se repiten como faltantes.
    letras_arca = {(l["cuit"], l["comprobante_arca"]) for l in letras_distintas}
    letras_xubio = {(l["cuit"], l["comprobante_xubio"]) for l in letras_distintas}

    # En Compras el número solo no identifica un comprobante: dos proveedores
    # pueden tener «C-00001-00000001», y una factura y su nota de crédito también.
    # Para el diagnóstico se compara por CUIT + comprobante + tipo.
    if operacion == "Compras" and col_cuit_arca is not None and 'CUIT' in df_xubio.columns:
        df_faltantes_arca, df_faltantes_xubio = _faltantes_por_cuit(df_arca, df_xubio, col_cuit_arca)

    falta_x = filas(df_faltantes_arca, columnas_arca, "arca", letras_arca, col_cuit_arca, 'Clave_Py')

    # Liquidaciones de bancos (tipo 63 «Liquidaciones A»): en Xubio se cargan
    # como «Extracto Bancario» del mismo banco, con otro número.
    cuits_extracto = set()
    if 'CUIT' in df_xubio.columns:
        es_extracto = df_xubio['Tipo'].astype(str).str.lower().str.contains('extracto', na=False)
        cuits_extracto = set(df_xubio.loc[es_extracto, 'CUIT'].map(_solo_digitos))
    liquidaciones = [f for f in falta_x if 'liquidaci' in str(_valor(f, "Tipo") or "").lower()]
    liq_con_extracto = [f for f in liquidaciones if _solo_digitos(_valor(f, "Nro. Doc.")) in cuits_extracto]
    con_extracto = {id(f) for f in liq_con_extracto}
    falta_x = [f for f in falta_x if id(f) not in con_extracto]

    # Factura y nota de crédito del mismo período quedan las dos como faltantes:
    # quien sube elige a mano cuáles cargar (no se emparejan solas).


    # Tiques (peajes, combustible...) y extractos bancarios casi nunca figuran en
    # Mis Comprobantes: se informan aparte para que no parezcan errores.
    es_tique = df_faltantes_xubio['Tipo'].astype(str).str.lower().str.contains('tique|extracto', na=False)
    tiques = filas(df_faltantes_xubio[es_tique], columnas_xubio, "xubio", letras_xubio, 'CUIT', 'Comprobante')
    falta_a = filas(df_faltantes_xubio[~es_tique], columnas_xubio, "xubio", letras_xubio, 'CUIT', 'Comprobante')

    # Mismo CUIT y número con distinto tipo (ARCA «Factura», Xubio «Nota Débito»):
    # es un error de carga del tipo, no dos faltantes.
    # En Ventas, sin la CUIT (a consumidor final puede no estar en Xubio).
    def _clave_tipo(f, col_cuit):
        cuit = _solo_digitos(_valor(f, col_cuit)) if operacion == "Compras" else ""
        return (cuit, str(_valor(f, "Comprobante")))
    por_clave_x = {}
    for f in falta_a:
        por_clave_x.setdefault(_clave_tipo(f, "CUIT"), []).append(f)
    tipo_distinto = []
    for f in list(falta_x):
        pares = por_clave_x.get(_clave_tipo(f, "Nro. Doc."))
        if pares:
            xf = pares.pop(0)
            tipo_distinto += [f, xf]
            falta_x.remove(f)
            falta_a.remove(xf)
    if tipo_distinto:
        errores.append(error(
            "tipo_distinto", "corregir",
            "Tipo de comprobante distinto entre ARCA y Xubio",
            "Mismo CUIT y número; en Xubio quedó cargado con otro tipo (factura / nota de crédito / débito).",
            ["Corregir en Xubio"],
            tipo_distinto,
        ))

    # Mismo comprobante e importe, pero en Xubio con otro CUIT: es el mismo
    # comprobante cargado a otro proveedor, no dos faltantes.
    cuit_distinto = []
    if operacion == "Compras":
        # Por (comprobante, clase): una pasada en vez de comparar todos contra todos.
        candidatos_x: dict[tuple, list] = {}
        for xf in falta_a:
            candidatos_x.setdefault(
                (str(_valor(xf, "Comprobante")), _clase_comprobante(_valor(xf, "Tipo"))), []
            ).append(xf)
        sobran_a = []
        usados_x: set[int] = set()
        for f in falta_x:
            total_a = _numero(_valor(f, "Imp. Total"))
            par = None
            for xf in candidatos_x.get((str(_valor(f, "Comprobante")), _clase_comprobante(_valor(f, "Tipo"))), []):
                total_x = _numero(_valor(xf, "Total"))
                if (
                    id(xf) not in usados_x
                    and total_a is not None and total_x is not None
                    and abs(abs(total_a) - abs(total_x)) <= TOLERANCIA_IMPORTE
                ):
                    par = xf
                    break
            if par is None:
                sobran_a.append(f)
            else:
                usados_x.add(id(par))
                cuit_distinto += [f, par]
        quedan_x = [xf for xf in falta_a if id(xf) not in usados_x]
        falta_x, falta_a = sobran_a, quedan_x
    else:
        # Ventas: el comprobante está en los dos lados (no es faltante) pero en
        # Xubio quedó a nombre de otro cliente.
        cuit_distinto = _cuits_distintos_ventas(df_arca, df_xubio, columnas_arca, columnas_xubio, col_cuit_arca)
    if cuit_distinto:
        errores.append(error(
            "cuit_distinto", "corregir",
            "CUIT distinto entre ARCA y Xubio",
            (
                "Mismo comprobante e importe; en Xubio quedó cargado con otro CUIT (otro proveedor)."
                if operacion == "Compras" else
                "Mismo comprobante; en Xubio quedó cargado a nombre de otro cliente (otra CUIT)."
            ),
            ["Corregir en Xubio"],
            cuit_distinto,
            a_corregir=len(cuit_distinto) // 2,
        ))

    # Mismo comprobante en los dos lados pero con otro total: el importe quedó mal
    # cargado en Xubio (o falta una percepción, un impuesto interno...).
    importe_distinto = _importes_distintos(
        operacion, df_arca, df_xubio, columnas_arca, columnas_xubio, col_cuit_arca, col_total_arca,
    )
    if importe_distinto:
        errores.append(error(
            "importe_distinto", "corregir",
            "Importe distinto entre ARCA y Xubio",
            "El mismo comprobante tiene otro total en Xubio. Revisá el importe, las percepciones y los impuestos internos.",
            ["Corregir en Xubio"],
            importe_distinto,
            hoja="Libro Iva",
            a_corregir=len(importe_distinto) // 2,
        ))

    # Facturas de ARCA cuyas columnas no suman el Imp. Total. No frena nada: el
    # cruce se hace con el Imp. Total igual; se avisa para que en Xubio la
    # diferencia quede cargada (no gravado o impuestos internos).
    no_cierran, bien_en_xubio = _totales_que_no_cierran(
        operacion, df_arca, df_xubio, columnas_arca, col_cuit_arca, col_total_arca,
    )
    if no_cierran:
        n = len(no_cierran)
        uno = n == 1
        detalle = (
            f"{'En 1 comprobante' if uno else f'En {n} comprobantes'} de ARCA el neto, el IVA, el no gravado, "
            f"el exento y los otros tributos no suman el Imp. Total. Suele ser un impuesto que ARCA no "
            f"discrimina (por ejemplo, en combustibles). El control se hizo con el Imp. Total."
        )
        detalle += " Consultá al cliente qué es la diferencia y revisá que en Xubio esté cargada (no gravado o impuestos internos)."
        if bien_en_xubio:
            detalle += (
                f" {'1 ya está' if bien_en_xubio == 1 else f'{bien_en_xubio} ya están'} en Xubio con el total completo."
            )
        # Siempre «A revisar»: aunque Xubio cierre, la parte sin discriminar hay
        # que consultarla (no se sabe a qué concepto corresponde).
        errores.append(error(
            "total_no_cierra", "corregir",
            "En ARCA las columnas no suman el total",
            detalle,
            ["Revisar la factura", "Consultar al cliente"],
            no_cierran,
            hoja="Mis Comprobantes",
            a_corregir=n,
        ))

    # Proveedores del exterior (CUIT genérico 50-/51-/55-): no están en ARCA.
    exterior = [f for f in falta_a if _solo_digitos(_valor(f, "CUIT"))[:2] in ("50", "51", "55")]
    del_exterior = {id(f) for f in exterior}
    falta_a = [f for f in falta_a if id(f) not in del_exterior]

    # Todo lo de ARCA sin par en Xubio va junto, como el bloque «Partidas de
    # ARCA no encontradas en el Libro IVA …» del Excel. Las liquidaciones
    # bancarias se marcan como normales (`motivo`).
    normales_arca = [{**f, "motivo": "liquidacion"} for f in liq_con_extracto]
    if falta_x or normales_arca:
        partes = []
        if falta_x:
            partes.append(
                f"{len(falta_x)} no {'está' if len(falta_x) == 1 else 'están'} en el libro: "
                "subilos a Xubio desde «Faltantes a Xubio»."
            )
        if liq_con_extracto:
            partes.append(
                f"{len(liq_con_extracto)} {'es liquidación bancaria' if len(liq_con_extracto) == 1 else 'son liquidaciones bancarias'}: "
                "en Xubio están dentro del extracto del banco."
            )
        errores.append(error(
            "faltante_en_xubio", "corregir" if falta_x else "info",
            f"Partidas de ARCA no encontradas en el {libro}",
            " ".join(partes),
            ["Subir desde Faltantes a Xubio"] if falta_x else ["Sin acción"],
            falta_x + normales_arca,
            hoja=f"Faltantes en Libro IVA {operacion}",
            a_corregir=len(falta_x),
        ))
    # Todo lo del Libro IVA sin par en ARCA va junto, como en la sección
    # «Partidas del Libro IVA … no encontradas en ARCA» del Excel. Los extractos,
    # tiques y proveedores del exterior se marcan como normales (`motivo`).
    normales = (
        [{**f, "motivo": "exterior"} for f in exterior]
        + [{**f, "motivo": "tique"} for f in tiques]
    )
    if falta_a or normales:
        partes = []
        if falta_a:
            partes.append(
                f"{len(falta_a)} a revisar: están en Xubio y ARCA no los informa en este período."
            )
        if tiques:
            partes.append(
                f"{len(tiques)} {'es tique o extracto bancario' if len(tiques) == 1 else 'son tiques o extractos bancarios'}: "
                "ARCA no los informa en Mis Comprobantes, es normal."
            )
        if exterior:
            partes.append(
                f"{len(exterior)} de {'proveedor' if len(exterior) == 1 else 'proveedores'} del exterior (CUIT genérico): "
                "ARCA no los informa, es normal."
            )
        errores.append(error(
            "faltante_en_arca", "corregir" if falta_a else "info",
            f"Partidas del {libro} no encontradas en ARCA",
            " ".join(partes),
            ["Revisar en Xubio", "Consultar al cliente"] if falta_a else ["Sin acción"],
            falta_a + normales,
            hoja="Mis Comprobantes",
            a_corregir=len(falta_a),
        ))

    # Duplicados reales: mismo CUIT, mismo número y mismo tipo (una factura y una
    # nota de crédito pueden compartir número). El mismo número de proveedores
    # distintos tampoco es un error.
    repetidos = []
    copias = 0
    if 'CUIT' in df_xubio.columns:
        cuits = df_xubio['CUIT'].map(_solo_digitos)
        claves = cuits + "|" + df_xubio['Comprobante'].astype(str) + "|" + df_xubio['Tipo'].map(_clase_comprobante)
        mask = claves.duplicated(keep=False) & (cuits != "")
        repetidos = filas(df_xubio[mask], columnas_xubio, "xubio")
        copias = int(claves[mask].duplicated(keep="first").sum())  # las que sobran
    if repetidos:
        errores.append(error(
            "duplicado", "corregir",
            f"Cargado dos veces en el {libro}",
            "Mismo comprobante, mismo tipo y mismo CUIT repetido en Xubio.",
            ["Eliminar en Xubio"],
            repetidos,
            hoja="Libro Iva",
            a_corregir=copias,
        ))

    return errores


# --- Errores marcados en el Excel --------------------------------------------
# Las filas con error van en negrita (sin colores nuevos: los del Excel de
# siempre quedan como estaban). Solo los que hay que corregir, no los informativos.
ERRORES_EN_NEGRITA = {
    "duplicado": "Duplicado",
    "letra_distinta": "Letra distinta",
    "tipo_distinto": "Tipo distinto",
    "cuit_distinto": "CUIT distinto",
    "importe_distinto": "Importe distinto",
    "total_no_cierra": "Columnas no suman el total",
    "faltante_en_xubio": "No está subido a Xubio",
    "faltante_en_arca": "Faltante en ARCA",
}


def _clave_fila(cuit, comprobante, tipo) -> tuple[str, str, str]:
    return (_solo_digitos(cuit), str(comprobante or "").strip(), _clase_comprobante(tipo))


def _claves_de_error(e: dict) -> tuple[set, set]:
    """Las filas (CUIT, comprobante, clase) de ARCA y de Xubio que tocan el error."""
    arca, xubio = set(), set()
    for item in e.get("comprobantes", []):
        if "fila_arca" in item:  # letra distinta
            fa = {c["columna"]: c["valor"] for c in item["fila_arca"]}
            fx = {c["columna"]: c["valor"] for c in item["fila_xubio"]}
            arca.add(_clave_fila(item["cuit"], item["comprobante_arca"], fa.get("Tipo")))
            xubio.add(_clave_fila(item["cuit"], item["comprobante_xubio"], fx.get("Tipo")))
            continue
        f = {c["columna"]: c["valor"] for c in item.get("fila", [])}
        if item.get("origen") == "arca":
            arca.add(_clave_fila(f.get("Nro. Doc."), f.get("Comprobante"), f.get("Tipo")))
        else:
            xubio.add(_clave_fila(f.get("CUIT"), f.get("Comprobante"), f.get("Tipo")))
    return arca, xubio


ERRORES_SEGUNDA_PASADA = ("letra_distinta", "tipo_distinto", "cuit_distinto")


def sin_segunda_pasada(errores: list[dict], df_faltantes_arca, df_faltantes_xubio, col_cuit_arca):
    """Saca de los faltantes los comprobantes que la 2ª pasada encontró en el otro
    lado con otra letra, tipo o CUIT: están cargados (mal), no faltan. Si quedaran,
    irían al CSV de «Subir a Xubio» y el comprobante terminaría duplicado."""
    claves_a, claves_x = set(), set()
    for e in errores:
        if e["tipo"] in ERRORES_SEGUNDA_PASADA:
            a, x = _claves_de_error(e)
            claves_a |= a
            claves_x |= x
    if not claves_a and not claves_x:
        return df_faltantes_arca, df_faltantes_xubio

    def fuera(df, col_cuit, col_comp, claves):
        if df.empty or not claves:
            return df
        tipo = df['Tipo'] if 'Tipo' in df.columns else pd.Series([""] * len(df), index=df.index)
        cuits = df[col_cuit] if col_cuit in df.columns else pd.Series([""] * len(df), index=df.index)
        quedan = [
            _clave_fila(c, comp, t) not in claves
            for c, comp, t in zip(cuits, df[col_comp], tipo)
        ]
        return df[quedan]

    return (
        fuera(df_faltantes_arca, col_cuit_arca, 'Clave_Py', claves_a),
        fuera(df_faltantes_xubio, 'CUIT', 'Comprobante', claves_x),
    )


def _columnas(ws) -> dict[str, int]:
    return {str(c.value).strip().lower(): c.column for c in ws[1] if c.value is not None}


def marcar_errores(wb, errores: list[dict], operacion: str) -> None:
    """Pinta de rojo claro y pone en negrita, en «Mis Comprobantes» y «Libro Iva»,
    las filas con error. Las columnas amarillas (totales y ajustes) quedan como están."""
    a_marcar = [e for e in errores if e["tipo"] in ERRORES_EN_NEGRITA and e.get("gravedad") != "info"]
    if not a_marcar:
        return
    claves = {
        e["tipo"]: _claves_de_error({**e, "comprobantes": [c for c in e["comprobantes"] if not c.get("motivo")]})
        for e in a_marcar
    }
    negrita = Font(bold=True)
    rojo_claro = PatternFill(start_color="FFCCCC", end_color="FFCCCC", fill_type="solid")

    def resaltar(ws, fila, hasta):
        for c in range(1, hasta + 1):
            celda = ws.cell(row=fila, column=c)
            celda.font = negrita
            if not str(celda.fill.start_color.rgb or "").endswith("FFFF00"):
                celda.fill = rojo_claro

    if "Mis Comprobantes" in wb.sheetnames:
        ws = wb["Mis Comprobantes"]
        col = _columnas(ws)
        c_tipo = col.get("tipo")
        c_pto = col.get("punto de venta")
        c_num = col.get("número desde")
        c_doc = next((v for k, v in col.items() if k.startswith("nro. doc")), None)
        hasta = col.get("total ajustado", ws.max_column)
        if c_tipo and c_pto and c_num and c_doc:
            for fila in range(2, ws.max_row + 1):
                if str(ws.cell(fila, 1).value or "").startswith("Partidas del Libro IVA"):
                    break  # desde acá son las filas de Xubio agregadas al final
                tipo = str(ws.cell(fila, c_tipo).value or "")
                pto, num = ws.cell(fila, c_pto).value, ws.cell(fila, c_num).value
                if not tipo or pto in (None, "") or num in (None, ""):
                    continue
                try:
                    comp = f"{tipo.strip()[-1:].upper()}-{int(float(pto)):05d}-{int(float(num)):08d}"
                except (TypeError, ValueError):
                    continue
                clave = _clave_fila(ws.cell(fila, c_doc).value, comp, tipo)
                if any(clave in arca for arca, _ in claves.values()):
                    resaltar(ws, fila, hasta)

    if "Libro Iva" in wb.sheetnames:
        ws = wb["Libro Iva"]
        col = _columnas(ws)
        c_tipo, c_comp, c_cuit = col.get("tipo"), col.get("comprobante"), col.get("cuit")
        c_est = col.get("estado conciliación")
        hasta = c_est or ws.max_column
        if c_tipo and c_comp and c_cuit:
            for fila in range(2, ws.max_row + 1):
                comp = ws.cell(fila, c_comp).value
                if not comp:
                    continue
                clave = _clave_fila(ws.cell(fila, c_cuit).value, comp, ws.cell(fila, c_tipo).value)
                for tipo_error, (_, xubio) in claves.items():
                    if clave in xubio:
                        resaltar(ws, fila, hasta)
                        if c_est and tipo_error in ("letra_distinta", "tipo_distinto", "duplicado", "cuit_distinto", "importe_distinto"):
                            ws.cell(fila, c_est).value = ERRORES_EN_NEGRITA[tipo_error]
                        break


def conciliar_exacto(wb) -> None:
    """Compras: cada fila del Libro IVA toma los importes de SU fila de ARCA.
    Los colores de siempre (rosa de duplicado, amarillo de totales) no se tocan.

    El VLOOKUP por número solo agarraba el primer comprobante con ese número:
    si dos proveedores (o una factura y su NC) comparten número, uno quedaba
    como «Duplicado» en 0 o tomaba el importe del otro. Acá se busca por CUIT +
    comprobante + tipo y se referencia la celda exacta de «Mis Comprobantes».
    """
    if "Mis Comprobantes" not in wb.sheetnames or "Libro Iva" not in wb.sheetnames:
        return
    wa, wx = wb["Mis Comprobantes"], wb["Libro Iva"]
    ca, cx = _columnas(wa), _columnas(wx)
    a_tipo, a_pto, a_num = ca.get("tipo"), ca.get("punto de venta"), ca.get("número desde")
    a_doc = next((v for k, v in ca.items() if k.startswith("nro. doc")), None)
    a_iva, a_tot = ca.get("iva ajustado"), ca.get("total ajustado")
    x_tipo, x_comp, x_cuit = cx.get("tipo"), cx.get("comprobante"), cx.get("cuit")
    x_viva, x_vtot = cx.get("total iva s/arca"), cx.get("total monto s/arca")
    x_diva, x_dtot, x_est = cx.get("diferencia iva"), cx.get("diferencia monto"), cx.get("estado conciliación")
    if not all((a_tipo, a_pto, a_num, a_doc, a_iva, a_tot, x_tipo, x_comp, x_cuit, x_viva, x_vtot, x_diva, x_dtot, x_est)):
        return

    filas_arca: dict[tuple, list[int]] = {}
    for r in range(2, wa.max_row + 1):
        if str(wa.cell(r, 1).value or "").startswith("Partidas del Libro IVA"):
            break
        tipo = str(wa.cell(r, a_tipo).value or "")
        pto, num = wa.cell(r, a_pto).value, wa.cell(r, a_num).value
        try:
            comp = f"{tipo.strip()[-1:].upper()}-{int(float(pto)):05d}-{int(float(num)):08d}"
        except (TypeError, ValueError):
            continue
        filas_arca.setdefault(_clave_fila(wa.cell(r, a_doc).value, comp, tipo), []).append(r)

    l_iva, l_tot = get_column_letter(a_iva), get_column_letter(a_tot)
    l_diva, l_dtot = get_column_letter(x_diva), get_column_letter(x_dtot)
    for r in range(2, wx.max_row + 1):
        if str(wx.cell(r, 1).value or "").startswith("Partidas de ARCA"):
            break
        comp = wx.cell(r, x_comp).value
        tipo = str(wx.cell(r, x_tipo).value or "")
        if not comp or "extracto" in tipo.lower():
            continue
        disponibles = filas_arca.get(_clave_fila(wx.cell(r, x_cuit).value, comp, tipo))
        if disponibles:
            fa = disponibles.pop(0)
            wx.cell(r, x_viva).value = f"='Mis Comprobantes'!{l_iva}{fa}"
            wx.cell(r, x_vtot).value = f"='Mis Comprobantes'!{l_tot}{fa}"
        else:
            wx.cell(r, x_viva).value = 0
            wx.cell(r, x_vtot).value = 0
        wx.cell(r, x_est).value = (
            f'=IF(AND({l_diva}{r}>=-0.02, {l_diva}{r}<=0.02, {l_dtot}{r}>=-0.02, {l_dtot}{r}<=0.02), "Ok", "Faltante en ARCA")'
        )


def _etiqueta_xubio(item: dict) -> str:
    f = {c["columna"]: c["valor"] for c in item.get("fila", [])}
    tipo = str(f.get("Tipo") or "").lower()
    if item.get("motivo") == "exterior":
        return "Proveedor del exterior: no figura en ARCA (normal)"
    if "extracto" in tipo:
        return "Extracto bancario: no figura en ARCA (normal)"
    if "tique" in tipo:
        return "Tique: no figura en ARCA (normal)"
    return "No está en ARCA: revisar la carga en Xubio"


ETIQUETA_ARCA = {
    "faltante_en_xubio": "No está subido a Xubio",
    "total_no_cierra": "Las columnas no suman el Imp. Total: revisar la factura",
    "liquidacion_bancaria": "Liquidación bancaria: en Xubio está como extracto",
    "letra_distinta": "Letra distinta: corregir en Xubio",
    "tipo_distinto": "Tipo distinto: corregir en Xubio",
}


def etiquetar_filas(wb, errores: list[dict]) -> None:
    """Escribe qué pasa con cada fila sin par, en palabras: en «Libro Iva» en
    ESTADO CONCILIACIÓN y en el bloque naranja de «Mis Comprobantes» en una
    columna «Motivo»."""
    de_arca: dict[tuple, str] = {}
    de_xubio: dict[tuple, str] = {}
    for e in errores:
        for item in e.get("comprobantes", []):
            if "fila_arca" in item:
                fa = {c["columna"]: c["valor"] for c in item["fila_arca"]}
                fx = {c["columna"]: c["valor"] for c in item["fila_xubio"]}
                de_arca[_clave_fila(item["cuit"], item["comprobante_arca"], fa.get("Tipo"))] = ETIQUETA_ARCA["letra_distinta"]
                de_xubio[_clave_fila(item["cuit"], item["comprobante_xubio"], fx.get("Tipo"))] = (
                    f"Letra distinta: en ARCA es {item['comprobante_arca']}"
                )
                continue
            f = {c["columna"]: c["valor"] for c in item.get("fila", [])}
            if item.get("origen") == "arca":
                motivo = {"liquidacion": "liquidacion_bancaria"}.get(item.get("motivo"))
                clave_tipo = motivo or e["tipo"]
                if clave_tipo in ETIQUETA_ARCA:
                    de_arca[_clave_fila(f.get("Nro. Doc."), f.get("Comprobante"), f.get("Tipo"))] = ETIQUETA_ARCA[clave_tipo]
            else:
                clave = _clave_fila(f.get("CUIT"), f.get("Comprobante"), f.get("Tipo"))
                if e["tipo"] == "tipo_distinto":
                    de_xubio[clave] = "Tipo distinto: corregir en Xubio"
                elif e["tipo"] == "duplicado":
                    de_xubio[clave] = "Duplicado: cargado dos veces en Xubio"
                elif e["tipo"] == "faltante_en_arca":
                    de_xubio[clave] = _etiqueta_xubio(item)
    if not (de_arca or de_xubio):
        return

    if "Libro Iva" in wb.sheetnames:
        ws = wb["Libro Iva"]
        col = _columnas(ws)
        c_tipo, c_comp, c_cuit, c_est = col.get("tipo"), col.get("comprobante"), col.get("cuit"), col.get("estado conciliación")
        if c_tipo and c_comp and c_cuit and c_est:
            en_bloque_arca = False
            for fila in range(2, ws.max_row + 1):
                if str(ws.cell(fila, 1).value or "").startswith("Partidas de ARCA"):
                    en_bloque_arca = True
                    continue
                comp = ws.cell(fila, c_comp).value
                if not comp:
                    continue
                clave = _clave_fila(ws.cell(fila, c_cuit).value, comp, ws.cell(fila, c_tipo).value)
                texto = (de_arca if en_bloque_arca else de_xubio).get(clave)
                if texto:
                    ws.cell(fila, c_est).value = texto

    if "Mis Comprobantes" in wb.sheetnames:
        ws = wb["Mis Comprobantes"]
        col = _columnas(ws)
        c_tipo = col.get("tipo")
        c_doc = next((v for k, v in col.items() if k.startswith("nro. doc")), None)
        c_motivo = col.get("total ajustado", ws.max_column) + 1
        titulo = None
        for fila in range(2, ws.max_row + 1):
            a = str(ws.cell(fila, 1).value or "")
            if a.startswith("Partidas del Libro IVA"):
                titulo = fila
                ws.cell(fila + 1, c_motivo, "Motivo").font = Font(bold=True)
                continue
            if titulo is None or fila <= titulo + 1 or not c_tipo or not c_doc:
                continue
            if not a or a.upper().startswith("TOTALES") or a.upper().startswith("DIFERENCIA"):
                continue
            clave = _clave_fila(ws.cell(fila, c_doc).value, a, ws.cell(fila, c_tipo).value)
            texto = de_xubio.get(clave)
            if texto:
                ws.cell(fila, c_motivo, texto)
        if titulo is not None:
            ws.column_dimensions[get_column_letter(c_motivo)].width = 52


def procesar_cruce(
    empresa,
    operacion,
    fecha_desde,
    fecha_hasta,
    directorio_guardado,
    ruta_arca_manual=None,
    ruta_xubio_manual=None,
    log: Callable[[str], None] = print,
    ruta_salida_manual=None,
    avisar_letras_distintas: Callable[[list[dict]], None] | None = None,
    avisar_errores: Callable[[list[dict]], None] | None = None,
    avisar_resumen: Callable[[dict], None] | None = None,
) -> str:
    """Genera el control de facturas y devuelve la ruta del Excel.

    Nunca frena por duplicados ni por letras distintas: los anota y sigue.
    """
    log(f"      [INFO] Iniciando cruce FO 04-01 para {empresa} ({operacion})")
    
    # 1. RUTAS DE LOS ARCHIVOS
    tipo_arca = "Emitidos" if operacion == "Ventas" else "Recibidos"
    mes_anio_desde = formatear_mes_anio(fecha_desde)
    mes_anio_hasta = formatear_mes_anio(fecha_hasta)
    f_desde_limpia = fecha_desde.replace("/", "-")
    f_hasta_limpia = fecha_hasta.replace("/", "-")
    
    cliente_limpio_arca = re.sub(r'[\\/*?:"<>|]', "", empresa)
    nombre_seguro_xubio = "".join(c for c in empresa if c.isalnum() or c in "._- ").strip()
    
    ruta_arca = ruta_arca_manual if ruta_arca_manual else os.path.join(directorio_guardado, f"Mis comprobantes - {tipo_arca} - {cliente_limpio_arca} - {mes_anio_desde} al {mes_anio_hasta}.xlsx")
    ruta_xubio = ruta_xubio_manual if ruta_xubio_manual else os.path.join(directorio_guardado, f"IVA {operacion.upper()} - {nombre_seguro_xubio} - {f_desde_limpia} al {f_hasta_limpia}.xlsx")
    
    # Cada auditoría del mismo período es un control nuevo: no pisa los anteriores.
    ruta_control, numero_control, previos = ruta_siguiente_control(
        directorio_guardado, operacion, fecha_desde, empresa
    )
    # Al re-chequear con el Libro IVA corregido se reescribe el mismo AUX del control.
    ruta_salida = str(ruta_salida_manual) if ruta_salida_manual else str(ruta_control)

    if not os.path.exists(ruta_arca):
        raise ArchivoNoEncontrado(
            f"No se encontró el archivo de ARCA esperado:\n{ruta_arca}",
            detalle={"cliente": empresa, "paso": "auditoria", "ruta": ruta_arca},
        )
    if not os.path.exists(ruta_xubio):
        raise ArchivoNoEncontrado(
            f"No se encontró el archivo de Xubio esperado:\n{ruta_xubio}",
            detalle={"cliente": empresa, "paso": "auditoria", "ruta": ruta_xubio},
        )

    if previos:
        anterior = previos[-1]
        log(
            f"      [INFO] {operacion}: {ordinal(numero_control)} control "
            f"(el {ordinal(anterior.numero)} fue el "
            f"{anterior.modificado.strftime('%d/%m/%Y %H:%M')})"
        )
    else:
        log(f"      [INFO] {operacion}: 1er control del período")

    # =========================================================
    # LIMPIEZA DE NÚMEROS Y FECHAS
    # =========================================================
    def format_to_dd_mm_yyyy(x):
        if pd.isna(x): return ''
        s = str(x).strip()
        if s == '' or s.lower() == 'nan' or s.lower() == 'nat' or 'total' in s.lower(): return ''
        s = s.split(' ')[0] 
        m1 = re.match(r'^(\d{4})[-/](\d{2})[-/](\d{2})$', s)
        if m1: return f"{m1.group(3)}/{m1.group(2)}/{m1.group(1)}"
        m2 = re.match(r'^(\d{2})[-/](\d{2})[-/](\d{4})$', s)
        if m2: return f"{m2.group(1)}/{m2.group(2)}/{m2.group(3)}"
        if s.isdigit() and int(s) > 30000:
            try: return pd.to_datetime(int(s), unit='D', origin='1899-12-30').strftime('%d/%m/%Y')
            except: pass
        return s

    def parse_num(val):
        """ Limpiador universal de números: soporta floats, strings de Excel o de CSV. """
        if pd.isna(val) or val == '' or str(val).strip() == '-': return 0.0
        if isinstance(val, (int, float)): return float(val)
        v = str(val).replace('$', '').replace(' ', '').strip()
        if v == '': return 0.0
        is_neg = v.startswith('-') or (v.startswith('(') and v.endswith(')'))
        v = re.sub(r'[^\d,\.]', '', v)
        if ',' in v and '.' in v:
            v = v.replace('.', '').replace(',', '.')
        elif ',' in v:
            v = v.replace(',', '.')
        try:
            num = float(v)
            return -num if is_neg else num
        except:
            return 0.0

    # 1. CARGA Y LIMPIEZA DE ARCA
    df_arca = pd.read_csv(ruta_arca, skiprows=1) if ruta_arca.endswith('.csv') else pd.read_excel(ruta_arca, skiprows=1)
    df_arca.columns = df_arca.columns.str.strip()
    
    if 'Tipo' in df_arca.columns: df_arca = df_arca.dropna(subset=['Tipo'])
    else: df_arca = df_arca.dropna(how='all')

    df_arca.insert(0, 'Clave_Conciliacion', '')
    def extraer_letra(t):
        texto = str(t).upper()
        
        # 1. Buscamos inteligentemente las letras estándar (A, B, C, E, M) que estén solas como "palabra"
        match = re.search(r'\b([ABCEM])\b', texto)
        if match:
            return match.group(1)
            
        # 2. Si es un comprobante muy raro que no tiene letra aislada, usamos el método viejo (al revés)
        match_fallback = re.search(r'([A-Z])', texto[::-1])
        return match_fallback.group(1) if match_fallback else ''
    
    df_arca_temp = df_arca.copy()
    letra_tmp = df_arca_temp['Tipo'].apply(extraer_letra) if 'Tipo' in df_arca_temp.columns else pd.Series(['']*len(df_arca_temp))
    pto_tmp = df_arca_temp['Punto de Venta'].fillna(0).astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(5) if 'Punto de Venta' in df_arca_temp.columns else pd.Series(['00000']*len(df_arca_temp))
    num_tmp = df_arca_temp['Número Desde'].fillna(0).astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(8) if 'Número Desde' in df_arca_temp.columns else pd.Series(['00000000']*len(df_arca_temp))
    
    df_arca_temp['Clave_Py'] = letra_tmp + "-" + pto_tmp + "-" + num_tmp
    df_arca['Clave_Conciliacion'] = df_arca_temp['Clave_Py']
    df_arca_temp = df_arca_temp[df_arca_temp['Clave_Py'].astype(str).str.strip() != '-00000-00000000']
    
    arca_map = {
        'fecha': next((c for c in df_arca.columns if 'fecha' in str(c).lower()), None),
        'tipo': next((c for c in df_arca.columns if 'tipo' in str(c).lower() and 'cambio' not in str(c).lower()), None),
        'pto': next((c for c in df_arca.columns if 'punto de venta' in str(c).lower()), None),
        'num': next((c for c in df_arca.columns if 'número desde' in str(c).lower()), None),
        # Por título: «Nro. Doc. Emisor» en Recibidos, «Nro. Doc. Receptor» en Emitidos.
        'cuit': next((c for c in df_arca.columns if 'nro. doc' in str(c).lower()), df_arca.columns[8] if len(df_arca.columns) > 8 else None),
        'prov': next((c for c in df_arca.columns if 'denominaci' in str(c).lower()), None),
        'neto': next((c for c in df_arca.columns if 'neto gravado total' in str(c).lower() or ('neto' in str(c).lower() and 'total' in str(c).lower())), 
                     next((c for c in df_arca.columns if 'neto' in str(c).lower() and 'gravado' in str(c).lower() and '0%' not in str(c).lower() and 'no' not in str(c).lower()), None)),
        'exe': next((c for c in df_arca.columns if 'exent' in str(c).lower() or 'no gravado' in str(c).lower()), None),
        'iva': next((c for c in df_arca.columns if 'total iva' in str(c).lower() or 'iva' == str(c).lower().strip()), None),
        'trib': next((c for c in df_arca.columns if 'otros tributos' in str(c).lower() or 'tributos' in str(c).lower()), None),
        'tot': next((c for c in df_arca.columns if 'imp. total' in str(c).lower()),
                    next((c for c in df_arca.columns if 'total' in str(c).lower() and 'iva' not in str(c).lower() and 'neto' not in str(c).lower()), None)),
        'tc': next((c for c in df_arca.columns if 'tipo cambio' in str(c).lower()), None)
    }

    if arca_map['fecha']:
        df_arca[arca_map['fecha']] = df_arca[arca_map['fecha']].apply(format_to_dd_mm_yyyy)
        df_arca_temp[arca_map['fecha']] = df_arca[arca_map['fecha']]

    usa_cuit = operacion == "Compras"
    col_doc_arca = next(
        (c for c in df_arca_temp.columns if 'nro. doc' in str(c).lower()), arca_map['cuit']
    )
    ident_a = _clave_identidad(df_arca_temp, col_doc_arca, 'Clave_Py', usa_cuit)
    dups_a = set(df_arca_temp['Clave_Py'][ident_a.duplicated(keep=False)])
    df_arca_temp['Clave_Unica'] = df_arca_temp['Clave_Py'] + "_" + df_arca_temp.groupby('Clave_Py').cumcount().astype(str)

    def normalize_comp_standard(x):
        s = str(x).strip().upper()
        match = re.search(r'([A-Z])[-\s]*(\d+)[-\s]*(\d+)', s)
        if match: return f"{match.group(1)}-{match.group(2).zfill(5)}-{match.group(3).zfill(8)}"
        return s

    # 2. CARGA INTELIGENTE DE LIBRO IVA
    df_xubio_raw = pd.read_csv(ruta_xubio, header=None) if ruta_xubio.endswith('.csv') else pd.read_excel(ruta_xubio, header=None)
    
    nombre_xubio = os.path.basename(ruta_xubio)
    if df_xubio_raw.empty:
        df_xubio = pd.DataFrame(columns=COLUMNAS_LIBRO_IVA)
    else:
        header_idx = fila_de_titulos_libro_iva(df_xubio_raw)
        if header_idx is None:
            raise ArchivoIlegible(
                f"No se encontró la fila de títulos (Fecha, Comprobante…) en el Libro IVA «{nombre_xubio}». "
                "Volvé a bajarlo de Xubio.",
                detalle={"cliente": empresa, "paso": "auditoria", "ruta": ruta_xubio},
            )
        df_xubio = pd.read_csv(ruta_xubio, skiprows=header_idx) if ruta_xubio.endswith('.csv') else pd.read_excel(ruta_xubio, skiprows=header_idx)
        df_xubio = columnas_libro_iva(df_xubio, nombre_xubio)
    nuevas_cols = list(COLUMNAS_LIBRO_IVA)

    df_xubio = df_xubio.dropna(subset=['Tipo'])
    df_xubio['Comprobante'] = df_xubio['Comprobante'].apply(normalize_comp_standard)
    df_xubio['Condición IVA'] = df_xubio['Condición IVA'].astype(str).replace(r'^\s*RI\s*$', 'Responsable Inscripto', regex=True)

    for col in nuevas_cols[6:22]:
        df_xubio[col] = df_xubio[col].apply(parse_num)

    c_fec_x_clean = next((c for c in df_xubio.columns if 'fecha' in str(c).lower()), None)
    if c_fec_x_clean: 
        df_xubio = df_xubio.dropna(subset=[c_fec_x_clean])
        df_xubio = df_xubio[df_xubio[c_fec_x_clean].astype(str).str.strip() != '']
        df_xubio = df_xubio[~df_xubio[c_fec_x_clean].astype(str).str.upper().str.contains('TOTAL', na=False)]
        df_xubio[c_fec_x_clean] = df_xubio[c_fec_x_clean].apply(format_to_dd_mm_yyyy)

    df_xubio = df_xubio[df_xubio['Comprobante'].astype(str).str.strip() != '']
    df_xubio = df_xubio[~df_xubio['Comprobante'].astype(str).str.upper().str.contains('NAN', na=False)]
    df_xubio = df_xubio[~df_xubio['Comprobante'].astype(str).str.upper().str.contains('NONE', na=False)]
    df_xubio = df_xubio[~df_xubio['Comprobante'].astype(str).str.upper().str.contains('TOTAL', na=False)]

    ident_x = _clave_identidad(df_xubio, 'CUIT', 'Comprobante', usa_cuit)
    no_extracto = ~df_xubio['Tipo'].astype(str).str.lower().str.contains('extracto', na=False)
    dups_x = set(df_xubio['Comprobante'][ident_x.duplicated(keep=False) & no_extracto])

    all_dups = dups_a.union(dups_x)

    # Misma factura (mismo CUIT, punto de venta y número) con otra letra en
    # Xubio: casi siempre un error de carga en el Libro IVA. No frena el control.
    letras_distintas = detectar_letras_distintas(
        df_arca_temp, df_xubio, col_doc_arca, arca_map['prov'],
        arca_map['fecha'],
        next((c for c in df_arca_temp.columns if 'imp. total' in str(c).lower()), arca_map['tot']),
    )
    if letras_distintas:
        log(f"      [AVISO] {texto_letras_distintas(empresa, operacion, letras_distintas)}")
        if avisar_letras_distintas is not None:
            avisar_letras_distintas(letras_distintas)

    # Los duplicados no frenan el control: se marcan en el Excel y se anotan.
    if all_dups:
        log(
            f"      [AVISO] {operacion}: {len(dups_a)} comprobante(s) duplicado(s) en ARCA "
            f"y {len(dups_x)} en Xubio."
        )
    df_xubio['Clave_Unica'] = df_xubio['Comprobante'] + "_" + df_xubio.groupby('Comprobante').cumcount().astype(str)

    # 3. IDENTIFICACIÓN DE FALTANTES
    # Una sola comparación para la ventana de errores, el Excel y el CSV de Xubio:
    # (CUIT en Compras +) comprobante + clase (factura / NC / ND).
    df_faltantes_arca, df_faltantes_xubio = _faltantes_por_cuit(df_arca_temp, df_xubio, col_doc_arca, usa_cuit)
    df_faltantes_arca, df_faltantes_xubio = df_faltantes_arca.copy(), df_faltantes_xubio.copy()

    # Ventana de errores de la web: qué está mal y cómo solucionarlo. Se arma
    # antes de convertir los USD para mostrar los importes como en la planilla.
    # También se usa para pintar en el Excel cada fila con error.
    errores_diag = diagnosticar(
        operacion, df_arca_temp, df_xubio, df_faltantes_arca, df_faltantes_xubio,
        letras_distintas, col_doc_arca, arca_map['prov'], arca_map['fecha'],
        next((c for c in df_arca_temp.columns if 'imp. total' in str(c).lower()), arca_map['tot']),
    )
    if avisar_errores is not None:
        avisar_errores(errores_diag)
    # 2ª pasada (mismo comprobante con otra letra, tipo o CUIT): ya existe en
    # Xubio mal cargado. Se corrige allá; subirlo desde «Faltantes» lo duplicaría.
    df_faltantes_arca, df_faltantes_xubio = sin_segunda_pasada(
        errores_diag, df_faltantes_arca, df_faltantes_xubio, col_doc_arca,
    )
    if avisar_resumen is not None:
        # Cuántos comprobantes de ARCA tienen su par en Xubio (los demás son los
        # de la sección «Partidas de ARCA no encontradas…»).
        a_corregir = sum(1 for e in errores_diag if e["gravedad"] in ("grave", "corregir"))
        total_arca = int(len(df_arca_temp))
        avisar_resumen({
            "operacion": operacion,
            "arca": total_arca,
            "xubio": int(len(df_xubio)),
            "coinciden": max(total_arca - int(len(df_faltantes_arca)), 0),
            "a_corregir": a_corregir,
        })

    # La hoja «Faltantes» es la que se convierte al CSV de Xubio: va como la da
    # ARCA (moneda original + tipo de cambio). En pesos solo se usa para el Libro
    # IVA del control y para la hoja «USD en pesos».
    df_faltantes_para_xubio = df_faltantes_arca.copy()
    df_faltantes_usd_pesos = pd.DataFrame()
    col_tc_faltantes = [c for c in df_faltantes_arca.columns if 'tipo cambio' in str(c).lower()]
    if col_tc_faltantes:
        tc_col = col_tc_faltantes[0]
        tc_values = pd.to_numeric(df_faltantes_arca[tc_col], errors='coerce').fillna(1)
        mask_usd = tc_values > 1
        if mask_usd.any():
            money_cols_names = [
                c for c in df_faltantes_arca.columns
                if any(kw in str(c).lower() for kw in ['imp.', 'iva', 'total', 'gravado', 'exenta', 'exento', 'tributo', 'monto', 'neto'])
                and not any(no in str(c).lower() for no in ['cambio', 'condici', 'moneda', 'tipo doc', 'nro. doc'])
            ]
            for c in money_cols_names:
                # Columna entera a número antes de asignar: con pandas 3 una
                # columna de texto no acepta floats por .loc.
                numeros = pd.to_numeric(df_faltantes_arca[c], errors='coerce')
                df_faltantes_arca[c] = numeros.where(~mask_usd, numeros.fillna(0) * tc_values)
            df_faltantes_usd_pesos = df_faltantes_arca[mask_usd].drop(columns=['Clave_Py', 'Clave_Unica', 'Clave_Conciliacion'], errors='ignore').copy()

    # --- LISTA INTELIGENTE DE TASAS ---
    tasas = [
        {'a_keys': ['21%'], 'x_n': 'Neto gravado 21%', 'x_i': 'IVA 21%'},
        {'a_keys': ['10,5%', '10.5%'], 'x_n': 'Neto gravado 10.5%', 'x_i': 'IVA 10.5%'},
        {'a_keys': ['27%'], 'x_n': 'Neto gravado 27%', 'x_i': 'IVA 27%'},
        {'a_keys': ['2,5%', '2.5%'], 'x_n': 'Neto gravado 2.5%', 'x_i': 'IVA 2.5%'},
        {'a_keys': [' 5%', '05%'], 'x_n': 'Neto gravado 5%', 'x_i': 'IVA 5%'}
    ]

    # «Neto No Gravado» y «Op. Exentas» van juntas a «No gravado exento» de Xubio.
    cols_exento_arca = [c for c in df_arca.columns if 'exent' in str(c).lower() or 'no gravado' in str(c).lower()]

    # 4. XUBIO -> ARCA (Inyectar)
    def inyectar_hacia_arca(row):
        nueva_fila = {c: np.nan for c in df_arca.columns}
        tipo_xubio = str(row.get('Tipo_Formateado', row.get('Tipo', ''))).lower()
        
        es_nota_credito = bool(re.search(r'\bnc\b', tipo_xubio) or (('crédito' in tipo_xubio or 'credito' in tipo_xubio) and 'factura' not in tipo_xubio))
        es_nota_debito = bool(re.search(r'\bnd\b', tipo_xubio) or 'débito' in tipo_xubio or 'debito' in tipo_xubio)
        signo_nc = -1 if es_nota_credito else 1
        
        nueva_fila['Clave_Conciliacion'] = row.get('Comprobante', '')
        if arca_map['fecha']: nueva_fila[arca_map['fecha']] = row.get('Fecha', '')
        
        if arca_map['tipo']: 
            if es_nota_credito: nueva_fila[arca_map['tipo']] = 'Nota de Crédito'
            elif es_nota_debito: nueva_fila[arca_map['tipo']] = 'Nota de Débito'
            elif re.search(r'\bfc\b', tipo_xubio) or 'factura' in tipo_xubio: nueva_fila[arca_map['tipo']] = 'Factura'
            else: nueva_fila[arca_map['tipo']] = row.get('Tipo', '')
        
        comp_str = str(row.get('Comprobante', ''))
        if '-' in comp_str:
            parts = comp_str.split('-')
            if len(parts) == 3:
                if arca_map['pto']: nueva_fila[arca_map['pto']] = parts[1]
                if arca_map['num']: nueva_fila[arca_map['num']] = parts[2]
        
        if arca_map['prov']: 
            pk = 'Proveedor' if 'Proveedor' in row else ('Cliente' if 'Cliente' in row else 'Denominación Emisor')
            nueva_fila[arca_map['prov']] = row.get(pk, '')
            
        if arca_map['cuit']: 
            dk = 'CUIT' if 'CUIT' in row else ('Documento' if 'Documento' in row else 'Nro. Doc. Emisor')
            nueva_fila[arca_map['cuit']] = row.get(dk, '')
            
        if arca_map['tot']: nueva_fila[arca_map['tot']] = abs(parse_num(row.get('Total'))) * signo_nc
        
        if arca_map['neto']:
            netos = [parse_num(row.get(c)) for c in row.keys() if 'neto' in str(c).lower()]
            nueva_fila[arca_map['neto']] = abs(sum(netos)) * signo_nc
                
        if arca_map['exe']:
            ek = next((k for k in row.keys() if 'exent' in str(k).lower() or 'no gravado' in str(k).lower()), 'Exento')
            nueva_fila[arca_map['exe']] = abs(parse_num(row.get(ek))) * signo_nc
                
        if arca_map['iva']:
            ivas = [parse_num(row.get(c)) for c in row.keys() if 'iva' in str(c).lower() and '%' in str(c)]
            nueva_fila[arca_map['iva']] = abs(sum(ivas)) * signo_nc
            
        if arca_map['trib']:
            nt = ['perc. iva', 'ret. iva', 'perc. iibb', 'impuestos internos', 'otras perc', 'retención iva', 'percepción iva', 'percepción iibb']
            tribs = [parse_num(row.get(c)) for c in row.keys() if str(c).lower().strip() in nt]
            nueva_fila[arca_map['trib']] = abs(sum(tribs)) * signo_nc
            
        for t in tasas:
            n_val = parse_num(row.get(t['x_n']))
            i_val = parse_num(row.get(t['x_i']))
            
            c_na = next((c for c in df_arca.columns if 'neto' in str(c).lower() and 'total' not in str(c).lower() and any(k in str(c).lower() for k in t['a_keys'])), None)
            c_ia = next((c for c in df_arca.columns if 'iva' in str(c).lower() and 'neto' not in str(c).lower() and 'total' not in str(c).lower() and any(k in str(c).lower() for k in t['a_keys'])), None)
            
            if c_na: nueva_fila[c_na] = abs(n_val) * signo_nc
            if c_ia: nueva_fila[c_ia] = abs(i_val) * signo_nc

        return nueva_fila

    importados_a_list = [inyectar_hacia_arca(row) for _, row in df_faltantes_xubio.iterrows()]
    df_importados_a = pd.DataFrame(importados_a_list) if importados_a_list else pd.DataFrame(columns=df_arca.columns)
    df_importados_a = df_importados_a.dropna(how='all')

    # 5. ARCA -> XUBIO (Inyectar)
    def inyectar_hacia_xubio(row):
        nueva_fila = {c: np.nan for c in df_xubio.columns}
        tipo_arca = str(row.get(arca_map['tipo'], '')).lower()
        
        # Filtro inteligente: Es Nota de Crédito SOLO si dice crédito y NO dice factura
        es_nota_credito_arca = ('crédito' in tipo_arca or 'credito' in tipo_arca) and 'factura' not in tipo_arca
        signo = -1 if es_nota_credito_arca else 1
        
        for c in df_xubio.columns:
            cl = str(c).lower()
            if 'comprobante' in cl: nueva_fila[c] = row.get('Clave_Py', '')
            if 'fecha' in cl:
                v_fec = row.get(arca_map['fecha'], '') if arca_map['fecha'] else row.get('Fecha', '')
                nueva_fila[c] = v_fec if pd.notna(v_fec) else ''
            if 'tipo' in cl and 'cambio' not in cl:
                if es_nota_credito_arca: nueva_fila[c] = 'Nota Crédito'
                elif 'débito' in tipo_arca or 'debito' in tipo_arca: nueva_fila[c] = 'Nota Débito'
                elif 'factura' in tipo_arca: nueva_fila[c] = 'Factura'
                else: 
                    v_tipo = row.get(arca_map['tipo'], '') if arca_map['tipo'] else ''
                    nueva_fila[c] = v_tipo if pd.notna(v_tipo) else ''
            if 'proveedor' in cl or 'cliente' in cl or 'denominaci' in cl or 'razón' in cl or 'razon' in cl:
                v_prov = row.get(arca_map['prov'], '') if arca_map['prov'] else row.get('Denominación Emisor', '')
                nueva_fila[c] = v_prov if pd.notna(v_prov) else ''
            if 'cuit' in cl or 'documento' in cl:
                v_cuit = row.get(arca_map['cuit']) if arca_map['cuit'] else row.get('Nro. Doc. Emisor', '')
                nueva_fila[c] = v_cuit if pd.notna(v_cuit) else ''
        
        c_tot_x = next((c for c in df_xubio.columns if 'total' in str(c).lower() and 'iva' not in str(c).lower()), None)
        if c_tot_x:
            c_tot_a = next((c for c in df_arca.columns if 'imp. total' in str(c).lower()), 'Imp. Total')
            nueva_fila[c_tot_x] = parse_num(row.get(c_tot_a)) * signo
            
        c_neto_x_total = next((c for c in df_xubio.columns if 'neto' in str(c).lower() and '%' not in str(c).lower() and '21' not in str(c) and '10' not in str(c) and '27' not in str(c)), None)
        if c_neto_x_total: nueva_fila[c_neto_x_total] = parse_num(row.get(arca_map['neto'])) * signo

        c_exe = next((c for c in df_xubio.columns if 'exent' in str(c).lower() or 'no gravado' in str(c).lower()), None)
        if c_exe: nueva_fila[c_exe] = sum(parse_num(row.get(c)) for c in cols_exento_arca) * signo
            
        c_trib = next((c for c in df_xubio.columns if 'percepción iva' in str(c).lower() or 'percepcion iva' in str(c).lower()), None)
        if c_trib: nueva_fila[c_trib] = parse_num(row.get(arca_map['trib'])) * signo
                
        for t in tasas:
            c_na = next((c for c in df_arca.columns if 'neto' in str(c).lower() and 'total' not in str(c).lower() and any(k in str(c).lower() for k in t['a_keys'])), None)
            c_ia = next((c for c in df_arca.columns if 'iva' in str(c).lower() and 'neto' not in str(c).lower() and 'total' not in str(c).lower() and any(k in str(c).lower() for k in t['a_keys'])), None)
            
            n_val = parse_num(row.get(c_na)) if c_na else 0.0
            i_val = parse_num(row.get(c_ia)) if c_ia else 0.0
            
            if t['x_n'] in df_xubio.columns: nueva_fila[t['x_n']] = n_val * signo
            if t['x_i'] in df_xubio.columns: nueva_fila[t['x_i']] = i_val * signo
                
        return nueva_fila

    importados_x_list = [inyectar_hacia_xubio(row) for _, row in df_faltantes_arca.iterrows()]
    df_importados_x = pd.DataFrame(importados_x_list) if importados_x_list else pd.DataFrame(columns=df_xubio.columns)
    df_importados_x = df_importados_x.dropna(how='all')

    df_faltantes_arca = df_faltantes_arca.drop(columns=['Clave_Py', 'Clave_Unica', 'Clave_Conciliacion'], errors='ignore')

    original_max_row_arca = len(df_arca) + 1

    if not df_importados_a.empty:
        df_vacia = pd.DataFrame([[np.nan]*len(df_arca.columns)], columns=df_arca.columns)
        df_vacia['Clave_Conciliacion'] = "---"
        df_titulo = pd.DataFrame([[np.nan]*len(df_arca.columns)], columns=df_arca.columns)
        df_titulo['Clave_Conciliacion'] = "---TITULO---"
        df_encabezados = pd.DataFrame([[np.nan]*len(df_arca.columns)], columns=df_arca.columns)
        df_encabezados['Clave_Conciliacion'] = "---ENCABEZADOS---"
        df_arca = pd.concat([df_arca, df_vacia, df_titulo, df_encabezados, df_importados_a], ignore_index=True)
    df_arca['Clave_Conciliacion'] = df_arca['Clave_Conciliacion'].fillna("")

    if not df_importados_x.empty:
        col_0_x = df_xubio.columns[0]
        df_vacia_x = pd.DataFrame([[np.nan]*len(df_xubio.columns)], columns=df_xubio.columns)
        df_vacia_x[col_0_x] = "---"
        df_titulo_x = pd.DataFrame([[np.nan]*len(df_xubio.columns)], columns=df_xubio.columns)
        df_titulo_x[col_0_x] = "---TITULO2---"
        df_encabezados_x = pd.DataFrame([[np.nan]*len(df_xubio.columns)], columns=df_xubio.columns)
        df_encabezados_x[col_0_x] = "---ENCABEZADOS2---"
        df_xubio = pd.concat([df_xubio, df_vacia_x, df_titulo_x, df_encabezados_x, df_importados_x], ignore_index=True)
        df_xubio[col_0_x] = df_xubio[col_0_x].fillna("")

    # 6. ESCRITURA EN EXCEL
    wb = Workbook()
    bold_font = Font(bold=True)
    right_align = Alignment(horizontal='right')
    orange_fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
    yellow_fill = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
    light_red_fill = PatternFill(start_color="FFCCCC", end_color="FFCCCC", fill_type="solid")
    injected_fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")

    def get_col_letter_by_name(ws, name):
        for col in range(1, ws.max_column + 1):
            val = str(ws.cell(1, col).value).lower().strip()
            if name.lower() in val and len(val) <= len(name) + 2: return get_column_letter(col)
        return None
    
    def get_money_cols(ws):
        cols = []
        for c in range(1, ws.max_column + 1):
            val = ws.cell(1, c).value
            if val:
                header = str(val).lower()
                if any(kw in header for kw in ['imp.', 'iva', 'total', 'gravado', 'exenta', 'exento', 'tributo', 'ajustado', 'diferencia', 'monto', 'neto']):
                    cols.append(c)
        return cols

    # --- HOJA ARCA ---
    ws_arca = wb.active
    ws_arca.title = "Mis Comprobantes"
    for r in dataframe_to_rows(df_arca, index=False, header=True): ws_arca.append(r)

    col_tipo_arca = get_col_letter_by_name(ws_arca, 'tipo')
    col_pto_arca = get_col_letter_by_name(ws_arca, 'punto de venta')
    col_num_arca = get_col_letter_by_name(ws_arca, 'número desde')
    col_iva_arca = get_col_letter_by_name(ws_arca, 'total iva') or get_col_letter_by_name(ws_arca, 'iva')
    col_tot_arca = get_col_letter_by_name(ws_arca, 'imp. total')
    col_tc_arca = get_col_letter_by_name(ws_arca, 'tipo cambio')

    idx_iva_ajust = ws_arca.max_column + 1
    idx_tot_ajust = ws_arca.max_column + 2
    ws_arca.cell(1, idx_iva_ajust, "IVA Ajustado").fill = yellow_fill
    ws_arca.cell(1, idx_tot_ajust, "Total Ajustado").fill = yellow_fill
    letra_iva_ajust_arca = get_column_letter(idx_iva_ajust)
    letra_tot_ajust_arca = get_column_letter(idx_tot_ajust)

    max_row_arca = ws_arca.max_row
    n_col_arca = ws_arca.max_column  # max_column recorre toda la hoja: se calcula una vez
    fila_vacia_arca = None
    fila_titulo_arca = None

    for row in range(2, max_row_arca + 1):
        val_a = str(ws_arca[f"A{row}"].value or "").strip()
        
        if val_a == "---":
            for c in range(1, n_col_arca + 1): ws_arca.cell(row, c).value = ""
            fila_vacia_arca = row
            continue 
        
        if val_a == "---TITULO---":
            for c in range(1, n_col_arca + 1): ws_arca.cell(row, c).value = ""
            ws_arca[f"A{row}"].value = f"Partidas del Libro IVA {operacion} no encontradas en ARCA"
            ws_arca[f"A{row}"].font = bold_font
            fila_titulo_arca = row
            continue
            
        if val_a == "---ENCABEZADOS---":
            for c in range(1, n_col_arca + 1):
                let_c = get_column_letter(c)
                ws_arca.cell(row, c).value = f"={let_c}1"
                ws_arca.cell(row, c).font = bold_font
                if c != idx_iva_ajust and c != idx_tot_ajust: ws_arca.cell(row=row, column=c).fill = injected_fill
            ws_arca[f"{letra_iva_ajust_arca}{row}"].fill = yellow_fill
            ws_arca[f"{letra_tot_ajust_arca}{row}"].fill = yellow_fill
            continue

        if fila_titulo_arca and row > fila_titulo_arca:
            for c in range(1, n_col_arca + 1):
                if c != idx_iva_ajust and c != idx_tot_ajust: ws_arca.cell(row=row, column=c).fill = injected_fill
            if col_tot_arca:
                col_start_sum = "Z" if operacion == "Compras" else "X"
                col_end_sum = "AD" if operacion == "Compras" else "AB"
                ws_arca[f"{col_tot_arca}{row}"].value = f"=SUM({col_start_sum}{row}:{col_end_sum}{row})"

        if val_a != "" and val_a in all_dups:
            for c in range(1, n_col_arca + 1):
                if c != idx_iva_ajust and c != idx_tot_ajust: ws_arca.cell(row=row, column=c).fill = light_red_fill

        ws_arca[f"{letra_iva_ajust_arca}{row}"].fill = yellow_fill
        ws_arca[f"{letra_tot_ajust_arca}{row}"].fill = yellow_fill

        if val_a == "":
            if col_tipo_arca and col_pto_arca and col_num_arca:
                ws_arca[f"A{row}"].value = f'=RIGHT({col_tipo_arca}{row}, 1)&"-"&TEXT({col_pto_arca}{row}, "00000")&"-"&TEXT({col_num_arca}{row}, "00000000")'
        
        tc_mult = f" * IF(AND(ISNUMBER({col_tc_arca}{row}), {col_tc_arca}{row}>1), {col_tc_arca}{row}, 1)" if col_tc_arca else ""
        signo = f'IF(AND(OR(ISNUMBER(SEARCH("Crédito", {col_tipo_arca}{row})), ISNUMBER(SEARCH("Credito", {col_tipo_arca}{row}))), ISERR(SEARCH("Factura", {col_tipo_arca}{row}))), -1, 1)'
        
        if col_iva_arca: ws_arca[f"{letra_iva_ajust_arca}{row}"].value = f'=IF(ISNUMBER({col_iva_arca}{row}), ABS({col_iva_arca}{row}) * {signo}{tc_mult}, 0)'
        if col_tot_arca: ws_arca[f"{letra_tot_ajust_arca}{row}"].value = f'=IF(ISNUMBER({col_tot_arca}{row}), ABS({col_tot_arca}{row}) * {signo}{tc_mult}, 0)'

    # --- HOJA XUBIO ---
    ws_xubio = wb.create_sheet("Libro Iva")
    for r in dataframe_to_rows(df_xubio, index=False, header=True): ws_xubio.append(r)

    col_tipo_x = get_col_letter_by_name(ws_xubio, 'tipo')
    col_comp_x = get_col_letter_by_name(ws_xubio, 'comprobante')
    letra_tot_x = get_col_letter_by_name(ws_xubio, 'total')
    
    iva_cols_x = [get_column_letter(c) for c in range(1, ws_xubio.max_column + 1) if 'iva' in str(ws_xubio.cell(1, c).value).lower() and '%' in str(ws_xubio.cell(1, c).value)]

    idx_s_iva = ws_xubio.max_column + 1
    idx_s_tot = ws_xubio.max_column + 2
    idx_v_iva = ws_xubio.max_column + 3
    idx_v_tot = ws_xubio.max_column + 4
    idx_d_iva = ws_xubio.max_column + 5
    idx_d_tot = ws_xubio.max_column + 6
    idx_est = ws_xubio.max_column + 7

    titulos = ["TOTAL IVA S/LIBRO", "TOTAL MONTO S/LIBRO", "TOTAL IVA S/ARCA", "TOTAL MONTO S/ARCA", "DIFERENCIA IVA", "DIFERENCIA MONTO", "ESTADO CONCILIACIÓN"]
    for i, tit in enumerate(titulos): ws_xubio.cell(1, idx_s_iva + i, tit).fill = yellow_fill 

    letra_s_iva = get_column_letter(idx_s_iva)
    letra_s_tot = get_column_letter(idx_s_tot)
    letra_v_iva = get_column_letter(idx_v_iva)
    letra_v_tot = get_column_letter(idx_v_tot)
    letra_d_iva = get_column_letter(idx_d_iva)
    letra_d_tot = get_column_letter(idx_d_tot)
    letra_est = get_column_letter(idx_est)

    max_row_xubio = ws_xubio.max_row
    n_col_xubio = ws_xubio.max_column
    fila_vacia_xubio = None
    fila_titulo_xubio = None

    for row in range(2, max_row_xubio + 1):
        val_a = str(ws_xubio[f"A{row}"].value or "").strip() 
        val_c = str(ws_xubio[f"{col_comp_x}{row}"].value or "").strip() if col_comp_x else ""
        tipo_val = str(ws_xubio[f"{col_tipo_x}{row}"].value or "").lower() if col_tipo_x else ""
        
        is_dup = (val_c != "") and (val_c in all_dups) and ("extracto" not in tipo_val)
        
        if val_a == "---":
            for c in range(1, n_col_xubio + 1): ws_xubio.cell(row, c).value = ""
            fila_vacia_xubio = row
            continue
        
        if val_a == "---TITULO2---":
            for c in range(1, n_col_xubio + 1): ws_xubio.cell(row, c).value = ""
            ws_xubio[f"A{row}"].value = f"Partidas de ARCA no encontradas en el Libro IVA {operacion}"
            ws_xubio[f"A{row}"].font = bold_font
            fila_titulo_xubio = row
            continue
            
        if val_a == "---ENCABEZADOS2---":
            for c in range(1, n_col_xubio + 1):
                let_c = get_column_letter(c)
                ws_xubio.cell(row, c).value = f"={let_c}1"
                ws_xubio.cell(row, c).font = bold_font
                if c < idx_s_iva: ws_xubio.cell(row=row, column=c).fill = injected_fill
                else: ws_xubio.cell(row=row, column=c).fill = yellow_fill
            continue

        if fila_titulo_xubio and row > fila_titulo_xubio:
            for c in range(1, idx_s_iva): ws_xubio.cell(row=row, column=c).fill = injected_fill
            for c in range(idx_s_iva, idx_est + 1): ws_xubio.cell(row=row, column=c).fill = yellow_fill
                
            # Conservamos los totales del lado del libro para poder ver la plata faltante
            suma_iva = f"=SUM({','.join([f'{c}{row}' for c in iva_cols_x])})" if iva_cols_x else "=0"
            ws_xubio[f"{letra_s_iva}{row}"].value = suma_iva
            ws_xubio[f"{letra_s_tot}{row}"].value = f"={letra_tot_x}{row}" if letra_tot_x else "0"
            
            # Limpiamos las columnas de la foto (ARCA y Diferencias) para que no repitan info ni den cero
            ws_xubio[f"{letra_v_iva}{row}"].value = ""
            ws_xubio[f"{letra_v_tot}{row}"].value = ""
            ws_xubio[f"{letra_d_iva}{row}"].value = ""
            ws_xubio[f"{letra_d_tot}{row}"].value = ""
            ws_xubio[f"{letra_est}{row}"].value = "Faltante en XUBIO"
            continue

        if is_dup:
            for c in range(1, idx_s_iva): ws_xubio.cell(row=row, column=c).fill = light_red_fill
        elif col_tipo_x and "extracto" in tipo_val:
            for c in range(1, idx_s_iva): ws_xubio.cell(row, c).fill = orange_fill
        
        for c in range(idx_s_iva, idx_est + 1): ws_xubio.cell(row, c).fill = yellow_fill

        suma_iva = f"=SUM({','.join([f'{c}{row}' for c in iva_cols_x])})" if iva_cols_x else "=0"
        ws_xubio[f"{letra_s_iva}{row}"].value = suma_iva
        ws_xubio[f"{letra_s_tot}{row}"].value = f"={letra_tot_x}{row}" if letra_tot_x else "0"
        
        if is_dup:
            ws_xubio[f"{letra_v_iva}{row}"].value = 0
            ws_xubio[f"{letra_v_tot}{row}"].value = 0
            ws_xubio[f"{letra_d_iva}{row}"].value = f'={letra_s_iva}{row} - {letra_v_iva}{row}'
            ws_xubio[f"{letra_d_tot}{row}"].value = f'={letra_s_tot}{row} - {letra_v_tot}{row}'
            ws_xubio[f"{letra_est}{row}"].value = "Duplicado"
        else:
            if col_comp_x:
                ws_xubio[f"{letra_v_iva}{row}"].value = f'=IFERROR(VLOOKUP({col_comp_x}{row}, \'Mis Comprobantes\'!$A$2:${letra_tot_ajust_arca}${original_max_row_arca}, {idx_iva_ajust}, 0), 0)'
                ws_xubio[f"{letra_v_tot}{row}"].value = f'=IFERROR(VLOOKUP({col_comp_x}{row}, \'Mis Comprobantes\'!$A$2:${letra_tot_ajust_arca}${original_max_row_arca}, {idx_tot_ajust}, 0), 0)'
            
            ws_xubio[f"{letra_d_iva}{row}"].value = f'={letra_s_iva}{row} - {letra_v_iva}{row}'
            ws_xubio[f"{letra_d_tot}{row}"].value = f'={letra_s_tot}{row} - {letra_v_tot}{row}'
            ws_xubio[f"{letra_est}{row}"].value = f'=IF(AND({letra_d_iva}{row}>=-0.02, {letra_d_iva}{row}<=0.02, {letra_d_tot}{row}>=-0.02, {letra_d_tot}{row}<=0.02), "Ok", "Faltante en ARCA")'

    regla_revisar = FormulaRule(formula=[f'${letra_est}2="Faltante en ARCA"'], font=Font(bold=True))
    ws_xubio.conditional_formatting.add(f"A2:{letra_est}{max_row_xubio}", regla_revisar)

    # --- TOTALES FINALES ---
    t_a1, t_a2, t_a3 = max_row_arca + 2, max_row_arca + 3, max_row_arca + 4
    t_x1, t_x2, t_x3 = max_row_xubio + 2, max_row_xubio + 3, max_row_xubio + 4

    col_label_arca = get_column_letter(max(1, idx_iva_ajust - 1))
    col_label_xubio = get_column_letter(max(1, idx_s_iva - 1))
    ws_xubio.column_dimensions[col_label_xubio].hidden = False

    ws_arca[f"{col_label_arca}{t_a1}"] = "TOTALES ARCA"
    ws_arca[f"{col_label_arca}{t_a2}"] = f"TOTALES LIBRO IVA {operacion.upper()}"
    ws_arca[f"{col_label_arca}{t_a3}"] = "DIFERENCIA"
    
    ws_xubio[f"{col_label_xubio}{t_x1}"] = f"TOTALES LIBRO IVA {operacion.upper()}"
    ws_xubio[f"{col_label_xubio}{t_x2}"] = "TOTALES ARCA"
    ws_xubio[f"{col_label_xubio}{t_x3}"] = "DIFERENCIA"

    for row in [t_a1, t_a2, t_a3]: 
        ws_arca[f"{col_label_arca}{row}"].font = bold_font
        ws_arca[f"{col_label_arca}{row}"].alignment = right_align
    for row in [t_x1, t_x2, t_x3]: 
        ws_xubio[f"{col_label_xubio}{row}"].font = bold_font
        ws_xubio[f"{col_label_xubio}{row}"].alignment = right_align

    ws_arca[f"{letra_iva_ajust_arca}{t_a1}"].value = f"=SUM({letra_iva_ajust_arca}2:{letra_iva_ajust_arca}{max_row_arca})"
    ws_arca[f"{letra_tot_ajust_arca}{t_a1}"].value = f"=SUM({letra_tot_ajust_arca}2:{letra_tot_ajust_arca}{max_row_arca})"

    if letra_tot_x: ws_xubio[f"{letra_tot_x}{t_x1}"].value = f"=SUM({letra_tot_x}2:{letra_tot_x}{max_row_xubio})"
    for c_idx in range(idx_s_iva, idx_est):
        let = get_column_letter(c_idx)
        ws_xubio[f"{let}{t_x1}"].value = f"=SUM({let}2:{let}{max_row_xubio})"

    if letra_tot_ajust_arca and letra_tot_x:
        ws_arca[f"{letra_tot_ajust_arca}{t_a2}"].value = f"='Libro Iva'!{letra_tot_x}{t_x1}"
        ws_arca[f"{letra_tot_ajust_arca}{t_a3}"].value = f"={letra_tot_ajust_arca}{t_a1} - {letra_tot_ajust_arca}{t_a2}"
        ws_xubio[f"{letra_s_tot}{t_x2}"].value = f"='Mis Comprobantes'!{letra_tot_ajust_arca}{t_a1}"
        ws_xubio[f"{letra_s_tot}{t_x3}"].value = f"={letra_s_tot}{t_x1} - {letra_s_tot}{t_x2}"
    
    if letra_iva_ajust_arca and letra_s_iva:
        ws_arca[f"{letra_iva_ajust_arca}{t_a2}"].value = f"='Libro Iva'!{letra_s_iva}{t_x1}"
        ws_arca[f"{letra_iva_ajust_arca}{t_a3}"].value = f"={letra_iva_ajust_arca}{t_a1} - {letra_iva_ajust_arca}{t_a2}"
        ws_xubio[f"{letra_s_iva}{t_x2}"].value = f"='Mis Comprobantes'!{letra_iva_ajust_arca}{t_a1}"
        ws_xubio[f"{letra_s_iva}{t_x3}"].value = f"={letra_s_iva}{t_x1} - {letra_s_iva}{t_x2}"

    # --- HOJA FALTANTES (OPCIONAL EN ARCA) ---
    ws_faltantes = wb.create_sheet(f"Faltantes en Libro IVA {operacion}")
    df_faltantes_para_xubio = df_faltantes_para_xubio.drop(columns=['Clave_Py', 'Clave_Unica', 'Clave_Conciliacion'], errors='ignore')
    for r in dataframe_to_rows(df_faltantes_para_xubio, index=False, header=True): ws_faltantes.append(r)

    # Cuánto son en pesos las de otra moneda: hoja aparte (no se sube a Xubio;
    # el nombre no dice «faltante» a propósito).
    ws_usd = None
    if not df_faltantes_usd_pesos.empty:
        ws_usd = wb.create_sheet("USD en pesos")
        for r in dataframe_to_rows(df_faltantes_usd_pesos, index=False, header=True): ws_usd.append(r)

    # --- ESTILOS DE MONEDA Y TAMAÑO DE COLUMNAS ---
    money_cols_arca = get_money_cols(ws_arca)
    money_cols_xubio = get_money_cols(ws_xubio)

    def aplicar_moneda(ws, max_data_row, max_total_row, cols_monto, rows_to_skip=None, fila_tit_xubio=None, idx_v_iva=None):
        if rows_to_skip is None: rows_to_skip = []
        for r in range(2, ws.max_row + 1):
            if r in rows_to_skip or r == max_data_row + 1: continue 
            val1 = str(ws.cell(r, 1).value or "").strip()
            if val1 in ["Facturas Originales en USD", f"Partidas de ARCA no encontradas en el Libro IVA {operacion}", f"Partidas del Libro IVA {operacion} no encontradas en ARCA"]: continue
            if isinstance(val1, str) and val1.startswith('=A1'): continue

            for c in cols_monto:
                celda = ws.cell(r, c)
                
                # Para filas inyectadas en Xubio, dejamos verdaderamente en blanco las de ARCA y Diferencias
                if ws.title == "Libro Iva" and fila_tit_xubio and r > fila_tit_xubio and idx_v_iva and c >= idx_v_iva and c < idx_v_iva + 4:
                    celda.value = ""
                    continue
                    
                if not isinstance(celda.value, str) or not celda.value.startswith('='):
                    if celda.value is None or celda.value == "" or (isinstance(celda.value, float) and np.isnan(celda.value)):
                        if r > max_data_row and r <= max_total_row: continue
                        celda.value = 0
                celda.number_format = '#,##0.00;[Red]-#,##0.00;"-"'

    filas_a_saltar = [r for r in [fila_vacia_arca, fila_titulo_arca] if r is not None]
    filas_a_saltar_xubio = [r for r in [fila_vacia_xubio, fila_titulo_xubio] if r is not None]
    
    aplicar_moneda(ws_arca, max_row_arca, t_a3, money_cols_arca, filas_a_saltar)
    aplicar_moneda(ws_xubio, max_row_xubio, t_x3, money_cols_xubio, filas_a_saltar_xubio, fila_titulo_xubio, idx_v_iva)
    aplicar_moneda(ws_faltantes, ws_faltantes.max_row, ws_faltantes.max_row, get_money_cols(ws_faltantes))
    if ws_usd is not None:
        aplicar_moneda(ws_usd, ws_usd.max_row, ws_usd.max_row, get_money_cols(ws_usd))

    for col_to_hide in ['D', 'E', 'F', 'G', 'H', 'L', 'K']: ws_arca.column_dimensions[col_to_hide].hidden = True
    for col_to_hide in ['E', 'S', 'T', 'U']: ws_xubio.column_dimensions[col_to_hide].hidden = True

    ws_arca.freeze_panes = "B2"
    ws_xubio.freeze_panes = "B2"
    ws_faltantes.freeze_panes = "B2"

    for ws in [ws_arca, ws_xubio, ws_faltantes]:
        ws.sheet_view.zoomScale = 80
        for col in range(1, ws.max_column + 1):
            letter = get_column_letter(col)
            header_val = str(ws.cell(1, col).value or "").lower()
            if 'denominaci' in header_val or 'proveedor' in header_val: ws.column_dimensions[letter].width = 40
            else:
                max_length = 0
                for row in range(1, ws.max_row + 1):
                    cell_val = ws.cell(row=row, column=col).value
                    if cell_val is not None:
                        str_val = str(cell_val)
                        if str_val in [f"Partidas del Libro IVA {operacion} no encontradas en ARCA", "Facturas Originales en USD", f"Partidas de ARCA no encontradas en el Libro IVA {operacion}"]: continue
                        length = max(len(header_val), 12) if str_val.startswith('=') else len(str_val)
                        if length > max_length: max_length = length
                adjusted_width = max_length + 2
                if adjusted_width < 10: adjusted_width = 10
                if adjusted_width > 50: adjusted_width = 50
                ws.column_dimensions[letter].width = adjusted_width

    if operacion == "Compras":
        conciliar_exacto(wb)
    marcar_errores(wb, errores_diag, operacion)
    etiquetar_filas(wb, errores_diag)

    guardar_excel(wb, ruta_salida)
    log(f"      [OK] Control de facturas generado: {ruta_salida}")
    return ruta_salida
