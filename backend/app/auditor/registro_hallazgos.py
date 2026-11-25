"""«Descargar hallazgos»: los avisos de un control en un .xlsx, como registro.

La ventana de hallazgos arma cada aviso tal como lo muestra (insignia, título, qué
hacer, detalle y su tabla con las mismas columnas) y acá se escribe uno debajo del
otro, sin colores ni estilos: solo el formato de los importes.
"""

from __future__ import annotations

import io
from datetime import datetime

FORMATO_IMPORTE = "#,##0.00"

# Fondos claros, como en la ventana de hallazgos.
AZUL = "DBEAFE"        # «Hallazgo N»
GRIS = "F1F5F9"        # encabezado de cada tabla
AMARILLO = "FEF3C7"    # «A revisar», «Qué hacer», la diferencia
VERDE = "DCFCE7"       # en Xubio con el mismo total
ROJO = "FEE2E2"        # «Error», «Grave», en Xubio distinto o que no está
CELESTE = "E0F2FE"     # «Info»
INSIGNIA = {"a revisar": AMARILLO, "error": ROJO, "grave": ROJO, "info": CELESTE}


def excel_hallazgos(bloques: list[dict], periodo: str = "") -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import PatternFill
    from openpyxl.utils import get_column_letter

    def pintar(fila: int, color: str, desde: int = 1, hasta: int = 1) -> None:
        for col in range(desde, hasta + 1):
            ws.cell(fila, col).fill = PatternFill("solid", fgColor=color)

    wb = Workbook()
    ws = wb.active
    ws.title = "Hallazgos"
    ws.append([f"Hallazgos del control{f' · Período {periodo}' if periodo else ''}"])
    ws.append([f"Descargado el {datetime.now():%d/%m/%Y %H:%M}"])
    anchos: dict[int, int] = {}

    def medir(fila: list) -> None:
        for i, v in enumerate(fila, start=1):
            if v is not None and not isinstance(v, str):
                largo = 14
            else:
                largo = len(str(v or "")) + 2
            anchos[i] = max(anchos.get(i, 10), min(largo, 40))

    cliente_actual = None
    for numero, b in enumerate(bloques, start=1):
        if b.get("cliente") != cliente_actual:
            cliente_actual = b.get("cliente")
            ws.append([])
            ws.append([])
            ws.append([cliente_actual])
        # Separados: dos renglones vacíos y «Hallazgo N» arriba de cada uno.
        ws.append([])
        ws.append([])
        columnas = b.get("columnas") or []
        ancho = max(len(columnas), 3)
        ws.append([f"Hallazgo {numero}"])
        pintar(ws.max_row, AZUL, 1, ancho)
        cabeza = [b.get("insignia") or "", b.get("titulo") or ""]
        if b.get("operacion"):
            cabeza.append(b["operacion"])
        ws.append(cabeza)
        color = INSIGNIA.get(str(b.get("insignia") or "").lower())
        if color:
            pintar(ws.max_row, color)
        if b.get("que_hacer"):
            ws.append(["Qué hacer", b["que_hacer"]])
            pintar(ws.max_row, AMARILLO, 1, ancho)
        if b.get("detalle"):
            ws.append(["", b["detalle"]])
        if columnas:
            ws.append(columnas)
            pintar(ws.max_row, GRIS, 1, len(columnas))
            medir(columnas)
            i_dif = columnas.index("Diferencia") + 1 if "Diferencia" in columnas else None
            i_xubio = columnas.index("En Xubio") + 1 if "En Xubio" in columnas else None
            i_total = columnas.index("Imp. Total") + 1 if "Imp. Total" in columnas else None
            # La grilla de «no suman» ya marca diferencia y Xubio: ahí no se pinta el comprobante.
            i_comp = columnas.index("Comprobante") + 1 if "Comprobante" in columnas and not i_dif else None
            i_obs = columnas.index("Observación") + 1 if "Observación" in columnas else None
            for fila in b.get("filas") or []:
                ws.append(fila)
                r = ws.max_row
                medir(fila)
                for celda in ws[r]:
                    if isinstance(celda.value, (int, float)) and not isinstance(celda.value, bool):
                        celda.number_format = FORMATO_IMPORTE
                # Como en la ventana: el comprobante marcado según el hallazgo (salvo los que tienen observación).
                if i_comp and color in (AMARILLO, ROJO) and not (i_obs and ws.cell(r, i_obs).value):
                    pintar(r, color, i_comp, i_comp)
                if i_dif:
                    pintar(r, AMARILLO, i_dif, i_dif)
                if i_xubio:
                    xubio, total = ws.cell(r, i_xubio).value, ws.cell(r, i_total).value if i_total else None
                    igual = isinstance(xubio, (int, float)) and isinstance(total, (int, float)) and abs(abs(xubio) - abs(total)) <= 1
                    pintar(r, VERDE if igual else ROJO, i_xubio, i_xubio)

    for i, ancho in anchos.items():
        ws.column_dimensions[get_column_letter(i)].width = ancho
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
