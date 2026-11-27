"""Paso 3, validar: las cuentas (neto + IVA + tributos = total, IVA por alícuota,
ítems) y los datos obligatorios. Solo avisa lo que no cierra o falta.
"""

from __future__ import annotations
import difflib
import io
import itertools
import logging
import re
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, field_validator
from ..modelo import TIPOS, cuit_valido, fecha as fecha_iso, importe, nombre_tipo, norm
from ..texto_factura import _tipo as tipo_del_texto
from ..texto_factura import cuits as cuits_del_texto
from .datos import ALICUOTAS, ETIQUETAS_ENCABEZADO, ETIQUETAS_TOTALES, Item, Lectura, TOLERANCIA, TOTALES, Tributo, Verificacion
from .normalizar import _alicuota_de, _cierra, _fmt, _fmt_alicuota, _variantes, _variantes_importe, cuits_cambiando_0_y_8, totales_efectivos

logger = logging.getLogger(__name__)


def _cierra_bien(lectura: Lectura) -> bool:
    """La lectura no necesita otra: el total cierra y el CUIT del emisor es válido."""
    if lectura.tipo != "comprobante":
        return False
    return _cierra(totales_efectivos(lectura)) and cuit_valido(lectura.encabezado.get("emisor_cuit", ""))


def cuits_parecidos(cuit: str, texto: str = "") -> list[str]:
    """CUIT válidos que difieren en un dígito que el OCR suele confundir. Primero
    los que se parecen a otros números del texto (Ingresos Brutos suele ser el
    mismo CUIT, y cada lectura se equivoca en un dígito distinto)."""
    candidatos = []
    for _, c in _variantes(cuit):
        if cuit_valido(c) and c not in candidatos:
            candidatos.append(c)
    # Más de un dígito mal: en tiques térmicos casi siempre son 0 leídos como 8 (o al revés).
    candidatos += [c for c in cuits_cambiando_0_y_8(cuit) if c not in candidatos]
    otros = [n for n in re.findall(r"(?<!\d)\d{11}(?!\d)", re.sub(r"(?<=\d)[-.](?=\d)", "", texto)) if n != cuit]

    def apoyo(c: str) -> int:
        return sum(1 for n in otros if sum(a != b for a, b in zip(c, n)) <= 1)

    return sorted(candidatos, key=lambda c: -apoyo(c))


def _mejor_parecido(cuit: str, texto: str) -> str | None:
    """El parecido que se puede sugerir: el único, o el único con apoyo en el texto."""
    candidatos = cuits_parecidos(cuit, texto)
    if len(candidatos) == 1:
        return candidatos[0]
    otros = [n for n in re.findall(r"(?<!\d)\d{11}(?!\d)", re.sub(r"(?<=\d)[-.](?=\d)", "", texto)) if n != cuit]
    apoyos = [sum(1 for n in otros if sum(a != b for a, b in zip(c, n)) <= 1) for c in candidatos]
    if apoyos and apoyos[0] > 0 and apoyos.count(apoyos[0]) == 1:
        return candidatos[0]
    # Sin otro desempate, el único que sale cambiando 0 y 8 (el error típico de las ticketeadoras).
    if len(ceros_y_ochos := cuits_cambiando_0_y_8(cuit)) == 1:
        return ceros_y_ochos[0]
    return None


def _verificar_cuit(campo: str, valor: str, obligatorio: bool, texto: str = "") -> Verificacion | None:
    etiqueta = ETIQUETAS_ENCABEZADO[campo]
    digitos = re.sub(r"\D", "", valor or "")
    if not digitos:
        if obligatorio:
            return Verificacion(campo=campo, nivel="aviso", mensaje=f"No se encontró el {etiqueta}.", titulo=f"Falta el {etiqueta}")
        return None
    if len(digitos) != 11:
        return Verificacion(campo=campo, nivel="error", mensaje=f"El {etiqueta} «{valor}» no tiene 11 dígitos.",
                            titulo=f"{etiqueta} incompleto", datos=[valor])
    if not cuit_valido(digitos):
        mejor = _mejor_parecido(digitos, texto)
        mensaje = f"El {etiqueta} {digitos} no es válido (el dígito verificador no da): seguro está mal leído."
        if mejor:
            mensaje += f" Lo más probable es {mejor}: compará con la imagen."
        elif parecidos := cuits_parecidos(digitos, texto):
            mensaje += " Puede ser " + " o ".join(parecidos[:3]) + "."
        return Verificacion(campo=campo, nivel="error", mensaje=mensaje, sugerencia=mejor, titulo=f"{etiqueta} mal leído",
                            datos=[digitos], opciones=[] if mejor else cuits_parecidos(digitos, texto)[:3])
    return Verificacion(campo=campo, nivel="ok", mensaje=f"{etiqueta}: el dígito verificador da bien.", titulo=etiqueta)


def corregir_totales(t: dict[str, float | None], suma_items: float | None = None) -> dict[str, float] | None:
    """Los importes que, cambiando uno o dos dígitos que el OCR confunde (6 y 8,
    0 y 8, 3 y 8...), hacen cerrar neto + IVA + otros = total con el IVA en una
    alícuota del neto. Solo si hay una sola corrección así (o la que coincide con
    lo que suma el detalle). {campo: valor corregido} o None."""
    presentes = [k for k in TOTALES if t.get(k) is not None]
    if "total" not in presentes:
        return None

    def bien(x: dict[str, float | None]) -> bool:
        if not _cierra(x):
            return False
        neto, iva = x.get("neto_gravado"), x.get("iva")
        return not iva or (neto is not None and _alicuota_de(neto, iva) is not None)

    soluciones: list[dict[str, float]] = []
    for cuantos in (1, 2):
        for campos in itertools.combinations(presentes, cuantos):
            # Un importe en 0 no se «corrige» (0,00 → 8,00 no es un error de lectura).
            if any(not t[c] for c in campos):
                continue
            opciones = [_variantes_importe(t[c]) for c in campos]
            for valores in itertools.product(*opciones):
                x = {**t, **dict(zip(campos, valores))}
                if bien(x):
                    soluciones.append(dict(zip(campos, valores)))
        if soluciones:
            break
    if not soluciones:
        return None
    if len(soluciones) == 1:
        return soluciones[0]
    if suma_items:
        # El detalle desempata: la que deja el neto igual a lo que suman los ítems.
        cerca = [s for s in soluciones if abs(s.get("neto_gravado", t.get("neto_gravado") or 0) - suma_items) <= 1]
        if len(cerca) == 1:
            return cerca[0]
    return None


def _verificar_totales(t: dict[str, float | None], suma_items: float | None = None, varias_alicuotas: bool = False) -> list[Verificacion]:
    v: list[Verificacion] = []
    total = t.get("total")
    if total is None:
        return [Verificacion(campo="total", nivel="error", mensaje="No se encontró el total del comprobante.", titulo="Falta el total")]
    partes = {k: t.get(k) for k in TOTALES if k != "total"}
    suma = round(sum(x or 0 for x in partes.values()), 2)
    neto, iva = t.get("neto_gravado"), t.get("iva")
    if abs(suma - total) <= TOLERANCIA:
        v.append(Verificacion(campo="total", nivel="ok", mensaje=f"Neto + IVA + otros tributos = total ({_fmt(total)}).", titulo="El total cierra"))
    else:
        diferencia = round(total - suma, 2)
        sugerida = False
        # Primero, dígitos que el lector confunde (es lo que más pasa en fotos).
        if correccion := corregir_totales(t, suma_items):
            for campo, valor in correccion.items():
                v.append(Verificacion(
                    campo=campo, nivel="error",
                    mensaje=(f"Los importes no suman el total. {ETIQUETAS_TOTALES[campo]}: se leyó {_fmt(t.get(campo))} y "
                             f"seguro es {_fmt(valor)} (el lector confunde dígitos como 6 y 8); con ese valor "
                             "neto + IVA + otros tributos da el total y el IVA da justo una alícuota."),
                    sugerencia=valor, titulo=f"{ETIQUETAS_TOTALES[campo]} mal leído",
                    datos=[f"Leído {_fmt(t.get(campo))}", f"Debería ser {_fmt(valor)}"],
                ))
            sugerida = True
        # ¿Qué importe, corregido, hace cerrar la cuenta? El neto, si con el IVA da una alícuota.
        if iva and not sugerida and not varias_alicuotas:
            neto_calc = round(total - (suma - (neto or 0)), 2)
            if neto_calc > 0 and _alicuota_de(neto_calc, iva):
                v.append(Verificacion(
                    campo="neto_gravado", nivel="error",
                    mensaje=(f"Los importes no suman el total (falta {_fmt(diferencia)}). Con el total, el IVA y los otros "
                             f"tributos, el neto gravado tiene que ser {_fmt(neto_calc)} (y el IVA da justo el "
                             f"{_alicuota_de(neto_calc, iva):g}%). Se leyó {_fmt(neto)}."),
                    sugerencia=neto_calc, titulo="El neto no cierra con el total",
                    datos=[f"Leído {_fmt(neto)}", f"Debería ser {_fmt(neto_calc)}"],
                ))
                sugerida = True
        if not sugerida and neto and not varias_alicuotas:
            iva_calc = round(total - (suma - (iva or 0)), 2)
            if iva_calc > 0 and _alicuota_de(neto, iva_calc):
                v.append(Verificacion(
                    campo="iva", nivel="error",
                    mensaje=(f"Los importes no suman el total. Con el total y el neto, el IVA tiene que ser "
                             f"{_fmt(iva_calc)} ({_alicuota_de(neto, iva_calc):g}% del neto). Se leyó {_fmt(iva)}."),
                    sugerencia=iva_calc, titulo="El IVA no cierra con el total",
                    datos=[f"Leído {_fmt(iva)}", f"Debería ser {_fmt(iva_calc)}"],
                ))
                sugerida = True
        if not sugerida:
            v.append(Verificacion(
                campo="total", nivel="error",
                mensaje=f"Neto + IVA + otros tributos da {_fmt(suma)} y el total leído es {_fmt(total)} (diferencia {_fmt(diferencia)}). Revisá los importes.",
                titulo="Los importes no suman el total",
                datos=[f"Suma {_fmt(suma)}", f"Total {_fmt(total)}", f"Dif. {_fmt(diferencia)}"],
            ))
    if neto and iva and not varias_alicuotas and not any(x.nivel == "error" for x in v):
        if (a := _alicuota_de(neto, iva)) is not None:
            v.append(Verificacion(campo="iva", nivel="ok", mensaje=f"El IVA es el {a:g}% del neto gravado.", titulo=f"IVA {a:g}%"))
        elif iva > neto * 0.27 + TOLERANCIA:
            v.append(Verificacion(campo="iva", nivel="aviso", mensaje=f"El IVA ({_fmt(iva)}) es más del 27% del neto: revisalo.", titulo="IVA demasiado alto", datos=[_fmt(iva)]))
        else:
            v.append(Verificacion(campo="iva", nivel="aviso", mensaje="El IVA no es una sola alícuota del neto (puede tener varias): revisalo.", titulo="IVA con varias alícuotas"))
    return v


def _tolerancia_iva(iva: float) -> float:
    return max(TOLERANCIA, abs(iva) * 0.0005)


def iva_de_items(items: list[Item], t: dict[str, float | None]) -> tuple[dict[float, float], bool]:
    """El IVA que dan los ítems en cada alícuota. Si el detalle suma el neto, los
    importes son sin IVA; si suma el total (tiques, factura B), lo incluyen.
    ({alícuota: IVA}, si los importes incluyen IVA)."""
    suma = sum(x.importe or 0 for x in items)
    neto, total = t.get("neto_gravado"), t.get("total")
    con_iva = total is not None and (neto is None or abs(suma - total) < abs(suma - neto))
    por_alicuota: dict[float, float] = {}
    for x in items:
        if x.alicuota is None or x.importe is None:
            continue
        a = float(x.alicuota)
        iva = x.importe - x.importe / (1 + a / 100) if con_iva else x.importe * a / 100
        por_alicuota[a] = por_alicuota.get(a, 0) + iva
    return {a: round(v, 2) for a, v in por_alicuota.items()}, con_iva


def _verificar_desglose(lectura: Lectura, t: dict[str, float | None]) -> list[Verificacion]:
    filas = lectura.iva_alicuotas
    if not filas:
        return []
    v: list[Verificacion] = []
    bien = True
    for i, f in enumerate(filas):
        campo = f"iva_{i}"
        nombre = f"IVA {_fmt_alicuota(f.alicuota)}" if f.alicuota is not None else f"IVA (fila {i + 1})"
        if f.alicuota is None:
            v.append(Verificacion(campo=campo, nivel="aviso", mensaje=f"{nombre}: falta la alícuota."))
            bien = False
            continue
        if f.alicuota not in ALICUOTAS and f.alicuota != 0:
            v.append(Verificacion(campo=campo, nivel="aviso", mensaje=f"{nombre}: no es una alícuota de IVA habitual (21, 10,5, 27, 5 o 2,5)."))
            bien = False
        if f.base is not None and f.importe is not None:
            calculado = round(f.base * f.alicuota / 100, 2)
            if abs(calculado - f.importe) > _tolerancia_iva(f.importe):
                v.append(Verificacion(
                    campo=campo, nivel="error",
                    mensaje=f"{nombre}: el {_fmt_alicuota(f.alicuota)} de {_fmt(f.base)} es {_fmt(calculado)} y se leyó {_fmt(f.importe)}.",
                    sugerencia=calculado,
                ))
                bien = False
    if len({f.alicuota for f in filas}) < len(filas):
        v.append(Verificacion(campo="iva", nivel="aviso", mensaje="Hay dos filas de IVA con la misma alícuota: juntalas."))
        bien = False
    neto = t.get("neto_gravado")
    bases = round(sum(f.base or 0 for f in filas), 2)
    if neto is not None and any(f.base is not None for f in filas) and abs(bases - neto) > TOLERANCIA:
        v.append(Verificacion(
            campo="iva", nivel="error",
            mensaje=f"Las bases imponibles del IVA suman {_fmt(bases)} y el neto gravado es {_fmt(neto)} (diferencia {_fmt(round(neto - bases, 2))}).",
        ))
        bien = False
    # Lo que dan los ítems en cada alícuota.
    if lectura.items and all(x.alicuota is not None and x.importe is not None for x in lectura.items):
        por_alicuota, _ = iva_de_items(lectura.items, t)
        tolerancia_items = max(TOLERANCIA, 0.01 * len(lectura.items))
        declaradas = {f.alicuota for f in filas}
        for i, f in enumerate(filas):
            if f.alicuota is None or f.importe is None:
                continue
            de_items = por_alicuota.get(f.alicuota, 0.0)
            if abs(de_items - f.importe) > tolerancia_items:
                v.append(Verificacion(
                    campo=f"iva_{i}", nivel="aviso",
                    mensaje=f"IVA {_fmt_alicuota(f.alicuota)}: los ítems de esa alícuota dan {_fmt(de_items)} de IVA y el comprobante dice {_fmt(f.importe)}.",
                ))
                bien = False
        for a, iva in por_alicuota.items():
            if a not in declaradas and abs(iva) > TOLERANCIA:
                v.append(Verificacion(
                    campo="iva", nivel="aviso",
                    mensaje=f"Hay ítems al {_fmt_alicuota(a)} ({_fmt(iva)} de IVA) y esa alícuota no está en el desglose del IVA.",
                ))
                bien = False
    if bien:
        texto = " · ".join(f"{_fmt_alicuota(f.alicuota)}: {_fmt(f.importe)}" for f in filas)
        v.append(Verificacion(campo="iva", nivel="ok", mensaje=f"El IVA de cada alícuota da con su base ({texto})."))
    return v


def _verificar_tributos(tributos: list[Tributo]) -> list[Verificacion]:
    v: list[Verificacion] = []
    for i, x in enumerate(tributos):
        nombre = x.descripcion or f"fila {i + 1}"
        if x.importe is None:
            v.append(Verificacion(campo=f"tributo_{i}", nivel="aviso", mensaje=f"Otros tributos, {nombre}: falta el importe."))
        elif not x.descripcion:
            v.append(Verificacion(campo=f"tributo_{i}", nivel="aviso", mensaje=f"Otros tributos, fila {i + 1}: falta qué tributo es."))
        elif x.base and abs(x.importe) > abs(x.base):
            v.append(Verificacion(
                campo=f"tributo_{i}", nivel="aviso",
                mensaje=f"Otros tributos, {nombre}: el importe ({_fmt(x.importe)}) es mayor que la base ({_fmt(x.base)}): revisalo.",
            ))
    if tributos and not v:
        texto = " · ".join(f"{x.descripcion}: {_fmt(x.importe)}" for x in tributos)
        v.append(Verificacion(campo="otros_tributos", nivel="ok", mensaje=f"Otros tributos: {texto}."))
    return v


def _verificar_items(items: list[Item], t: dict[str, float | None]) -> list[Verificacion]:
    corregidos = [(n + 1, x) for n, x in enumerate(items) if x.leido and x.leido != x.descripcion]
    aviso = [Verificacion(
        campo="descripcion", nivel="aviso",
        mensaje="Estas descripciones se leyeron mal y las corregí solo (en naranja en el detalle): "
                + "; ".join(f"ítem {n}: «{x.leido}» → «{x.descripcion}»" for n, x in corregidos) + ". Revisalas.",
        titulo=f"Autocorregí {len(corregidos)} {'descripción' if len(corregidos) == 1 else 'descripciones'}",
        datos=[f"#{n} {x.descripcion}" for n, x in corregidos[:4]],
    )] if corregidos else []
    for n, x in enumerate(items):
        if x.importe_leido is not None and x.importe is not None and x.importe_leido != x.importe:
            aviso.append(Verificacion(
                campo="items", nivel="aviso",
                mensaje=(f"El importe del ítem {n + 1} se leyó {_fmt(x.importe_leido)} y lo corregí a {_fmt(x.importe)}: "
                         "con ese valor el detalle suma el neto (en naranja en el detalle). Compará con la imagen."),
                titulo=f"Corregí el importe del ítem {n + 1}",
                datos=[f"Leído {_fmt(x.importe_leido)}", f"Ahora {_fmt(x.importe)}"],
            ))
    return aviso + _verificar_items_sin_correccion(items, t)


def _verificar_items_sin_correccion(items: list[Item], t: dict[str, float | None]) -> list[Verificacion]:
    if not items:
        return [Verificacion(campo="items", nivel="aviso", mensaje="No se pudo leer el detalle: el Excel va a tener solo el encabezado y los totales.", titulo="Sin detalle")]
    v: list[Verificacion] = []
    sin_importe = [i + 1 for i, x in enumerate(items) if x.importe is None]
    if sin_importe:
        v.append(Verificacion(campo="items", nivel="aviso", mensaje=f"Ítems sin importe: {', '.join(map(str, sin_importe))}.", titulo="Ítems sin importe", datos=[f"#{n}" for n in sin_importe[:6]]))
    sin_desc = [i + 1 for i, x in enumerate(items) if not x.descripcion.strip()]
    if sin_desc:
        v.append(Verificacion(campo="items", nivel="aviso", mensaje=f"Ítems sin descripción: {', '.join(map(str, sin_desc))}.", titulo="Ítems sin descripción", datos=[f"#{n}" for n in sin_desc[:6]]))
    # Mismo producto, cantidad y precio pero otro importe: uno de los dos está mal leído.
    for i, a in enumerate(items):
        for j in range(i + 1, len(items)):
            b = items[j]
            if (a.cantidad, a.precio, a.alicuota) == (b.cantidad, b.precio, b.alicuota) and a.precio and a.importe != b.importe                     and a.importe and b.importe and abs(a.importe - b.importe) < 0.02 * max(a.importe, b.importe):
                v.append(Verificacion(
                    campo="items", nivel="aviso",
                    mensaje=(f"Los ítems {i + 1} y {j + 1} tienen la misma cantidad y precio pero distinto importe "
                             f"({_fmt(a.importe)} y {_fmt(b.importe)}): uno puede estar mal leído."),
                    titulo=f"Ítems {i + 1} y {j + 1}: mismo precio, otro importe",
                    datos=[f"#{i + 1} {_fmt(a.importe)}", f"#{j + 1} {_fmt(b.importe)}"],
                ))
    suma = round(sum(x.importe or 0 for x in items), 2)
    # El detalle suma el neto (factura A) o el total (factura B/C, tiques).
    for campo in ("neto_gravado", "total"):
        objetivo = t.get(campo)
        if objetivo is None:
            continue
        dif = round(objetivo - suma, 2)
        if abs(dif) <= max(TOLERANCIA, 0.01 * len(items) / 2):
            extra = f" (diferencia de redondeo {_fmt(dif)})" if dif else ""
            v.append(Verificacion(campo="items", nivel="ok", mensaje=f"Los {len(items)} ítems suman el {ETIQUETAS_TOTALES[campo].lower()}: {_fmt(suma)}{extra}.", titulo=f"Los {len(items)} ítems suman bien"))
            return v
    objetivo = t.get("neto_gravado") if t.get("neto_gravado") is not None else t.get("total")
    if objetivo is not None:
        v.append(Verificacion(
            campo="items", nivel="error",
            mensaje=f"Los {len(items)} ítems suman {_fmt(suma)} y el neto gravado es {_fmt(objetivo)} (diferencia {_fmt(round(objetivo - suma, 2))}): algún importe está mal leído o falta un ítem.",
            titulo="Los ítems no suman el neto",
            datos=[f"Ítems {_fmt(suma)}", f"Neto {_fmt(objetivo)}", f"Dif. {_fmt(round(objetivo - suma, 2))}"],
        ))
    return v


def _verificar_fecha(valor: str) -> Verificacion | None:
    if not valor:
        return Verificacion(campo="fecha", nivel="aviso", mensaje="No se encontró la fecha.", titulo="Falta la fecha")
    try:
        d = datetime.strptime(valor, "%d/%m/%Y").date()
    except ValueError:
        return Verificacion(campo="fecha", nivel="error", mensaje=f"La fecha «{valor}» no es válida (tiene que ser DD/MM/AAAA).", titulo="Fecha inválida", datos=[valor])
    if d > date.today():
        return Verificacion(campo="fecha", nivel="aviso", mensaje=f"La fecha {valor} es posterior a hoy.", titulo="Fecha futura", datos=[valor])
    if d.year < 2000:
        return Verificacion(campo="fecha", nivel="aviso", mensaje=f"La fecha {valor} es muy vieja: revisala.", titulo="Fecha muy vieja", datos=[valor])
    return Verificacion(campo="fecha", nivel="ok", mensaje=f"Fecha {valor}.", titulo=f"Fecha {valor}")


def _verificar_tabla(lectura: Lectura) -> list[Verificacion]:
    v: list[Verificacion] = []
    if not lectura.hojas or not any(h.filas for h in lectura.hojas):
        return [Verificacion(campo="hojas", nivel="error", mensaje="No se leyó ningún dato.")]
    for h in lectura.hojas:
        if not h.filas:
            v.append(Verificacion(campo="hojas", nivel="aviso", mensaje=f"La hoja «{h.nombre}» está vacía."))
            continue
        ancho = max(len(f) for f in h.filas)
        v.append(Verificacion(campo="hojas", nivel="ok", mensaje=f"«{h.nombre}»: {len(h.filas)} filas y {ancho} columnas."))
        # Columnas con números y texto mezclados (fuera del encabezado): suele ser un número mal escrito.
        for col in range(ancho):
            valores = [f[col] for f in h.filas[1:] if col < len(f) and f[col] is not None]
            nums = [x for x in valores if isinstance(x, (int, float))]
            textos = [x for x in valores if isinstance(x, str)]
            if len(valores) >= 5 and len(nums) >= 0.8 * len(valores) and textos:
                v.append(Verificacion(
                    campo="hojas", nivel="aviso",
                    mensaje=f"«{h.nombre}», columna {col + 1}: casi todos son números pero {len(textos)} no ({', '.join(map(str, textos[:3]))}).",
                ))
    if lectura.metodo in ("ocr", "ia"):
        como = "con OCR" if lectura.metodo == "ocr" else "con IA (Gemini)"
        v.append(Verificacion(campo="hojas", nivel="aviso", mensaje=f"Leído {como}: compará el texto con la imagen antes de armar el Excel."))
    return v


def verificar(lectura: Lectura) -> list[Verificacion]:
    if lectura.tipo == "tabla":
        return _verificar_tabla(lectura)
    e = lectura.encabezado
    t = totales_efectivos(lectura)
    v: list[Verificacion] = []
    for campo, obligatorio in (("emisor_cuit", True), ("receptor_cuit", False)):
        if (x := _verificar_cuit(campo, e.get(campo, ""), obligatorio, lectura.texto)) is not None:
            v.append(x)
    if not e.get("numero"):
        v.append(Verificacion(campo="numero", nivel="aviso", mensaje="No se encontró el número de comprobante.", titulo="Falta el número"))
    elif not re.fullmatch(r"\d{4,5}-\d{8}", e["numero"]):
        v.append(Verificacion(campo="numero", nivel="aviso", mensaje=f"El número «{e['numero']}» no tiene la forma 00001-00000001.", titulo="Número raro", datos=[e["numero"]]))
    if not e.get("tipo"):
        v.append(Verificacion(campo="tipo", nivel="aviso", mensaje="No se reconoció el tipo de comprobante.", titulo="Falta el tipo"))
    if (x := _verificar_fecha(e.get("fecha", ""))) is not None:
        v.append(x)
    v += _verificar_totales(t, round(sum(x.importe or 0 for x in lectura.items), 2) if lectura.items else None, len(lectura.iva_alicuotas) > 1)
    v += _verificar_desglose(lectura, t)
    v += _verificar_tributos(lectura.tributos)
    v += _verificar_items(lectura.items, t)
    # Lo que cada pasada leyó distinto y sigue sin verificarse por otro lado.
    con_error = {x.campo for x in v if x.nivel != "ok"}
    verificados = {x.campo for x in v if x.nivel == "ok"}
    for campo, valores in lectura.dudas.items():
        if campo in con_error or (campo in verificados and campo.endswith("_cuit")) or (campo in TOTALES and _cierra(t)):
            continue
        etiqueta = ETIQUETAS_ENCABEZADO.get(campo) or ETIQUETAS_TOTALES.get(campo, campo)
        v.append(Verificacion(campo=campo, nivel="aviso", mensaje=f"{etiqueta}: cada lectura dio algo distinto ({' / '.join(valores[:3])}). Compará con la imagen.",
                             titulo=f"{etiqueta}: ¿cuál es?", opciones=valores[:3]))
    # Primero lo que hay que arreglar.
    orden = {"error": 0, "aviso": 1, "ok": 2}
    return sorted(v, key=lambda x: orden[x.nivel])
