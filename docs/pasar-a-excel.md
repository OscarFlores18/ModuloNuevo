# Pasar a Excel

Pestaña **Pasar a Excel** (`/pasar-a-excel`): cualquier archivo (foto, captura, PDF digital o escaneado, CSV, XLS, XLSX) se pasa a un `.xlsx`. **Antes de armarlo**, la pantalla muestra lo que se leyó al lado del archivo original, marca lo que puede estar mal y propone la corrección cuando las cuentas la dan.

Es independiente de «Subir a Xubio»: reutiliza el OCR (`extraccion/ocr.py`) y algunas funciones de `extraccion/modelo.py` y `texto_factura.py`, pero no toca ese flujo.

## Cómo funciona

```
 Pantalla (Angular)                      Backend (FastAPI)
 ─────────────────                       ─────────────────
 1. Subir archivos ──── POST /api/a-excel/leer ────▶ a_excel.leer()
                                                       ├─ planilla/CSV → hojas (números y fechas como tales)
                                                       ├─ PDF digital  → texto del PDF
                                                       └─ foto/escaneo → OCR en 3 pasadas (PASADAS)
                                                     → ¿parece comprobante? encabezado + ítems + totales
                                                                        si no: tabla con el texto
                   ◀──── LecturaExcel (con verificaciones) ───
 2. Verificar/corregir
    cada cambio ─────── POST /api/a-excel/verificar ─▶ a_excel.verificar()
                   ◀──── verificaciones ───
 3. Generar Excel ───── POST /api/a-excel/generar ───▶ a_excel.generar() → .xlsx (base64)
```

## Dónde está cada cosa

| Qué | Archivo |
|---|---|
| Leer, verificar y armar el Excel (todo el backend) | `backend/app/extraccion/a_excel.py` |
| Pasada de OCR con otro tamaño y modo | `backend/app/extraccion/ocr.py` → `texto_de_imagen_variante` |
| Endpoints `/a-excel/leer`, `/verificar`, `/generar` | `backend/app/api/routes/web.py` (buscar «Pasar a Excel») |
| Pruebas del backend | `backend/tests/test_a_excel.py` |
| OCR real de un tique (para probar sin Tesseract) | `backend/tests/datos/tique_ocr.txt` |
| Pantalla | `frontend/src/app/features/a-excel/a-excel.ts` |
| Lógica de la pantalla (números, «Usar», campos) | `frontend/src/app/features/a-excel/logica.ts` (+ `logica.spec.ts`) |
| Tipos (`LecturaExcel`, `Verificacion`, `ItemLeido`, `HojaLeida`) | `frontend/src/app/core/models.ts` (al final) |
| Llamadas a la API | `frontend/src/app/core/services/api.service.ts` → `leerAExcel`, `verificarAExcel`, `generarAExcel` |
| Ruta y pestaña | `frontend/src/app/app.routes.ts`, `frontend/src/app/app.html` |

## Lo que devuelve la lectura (`Lectura` / `LecturaExcel`)

- `tipo`: `comprobante` (factura, tique, nota de crédito…) o `tabla` (planillas y cualquier texto que no sea un comprobante).
- `metodo`: `ocr`, `pdf` o `planilla`.
- `encabezado`: `emisor`, `emisor_cuit`, `receptor`, `receptor_cuit`, `tipo`, `numero` (`00103-00000524`), `fecha` (`DD/MM/AAAA`), `cae`.
- `items`: `codigo`, `descripcion`, `cantidad`, `precio`, `alicuota`, `importe`.
- `totales`: `neto_gravado`, `no_gravado`, `exento`, `iva`, `otros_tributos`, `total`.
- `hojas`: `[{nombre, filas}]` (solo para `tabla`).
- `texto`: lo leído, línea por línea (va a la hoja «Texto leído»).
- `dudas`: campo → los valores distintos que dio cada pasada del OCR.
- `verificaciones`: `[{campo, nivel: ok|aviso|error, mensaje, sugerencia}]`.

## La verificación (lo importante)

`a_excel.verificar()` revisa, y si puede, sugiere:

| Qué revisa | Cómo | Sugerencia |
|---|---|---|
| CUIT emisor/receptor | dígito verificador (módulo 11) | el CUIT válido que difiere en un dígito que el OCR confunde, apoyado en otros números del texto (Ing. Brutos suele ser el mismo CUIT) |
| Totales | neto + no gravado + exento + IVA + otros = total | el neto que falta (si con el IVA da una alícuota) o el IVA; si no, cambia 1 o 2 dígitos confundibles (6↔8, 0↔8, 3↔8…) hasta que cierre y el IVA dé una alícuota (`corregir_totales`) |
| IVA | que sea 21 / 10,5 / 27 / 5 / 2,5 % del neto | — |
| Detalle | que los ítems sumen el neto (o el total) | avisa ítems iguales con distinto importe |
| Fecha | válida, no futura | — |
| Número y tipo | que estén y tengan forma | — |
| Lecturas distintas | lo que cada pasada del OCR leyó distinto | — (se borra la duda cuando la persona edita el campo) |
| Tablas | filas, columnas, columnas numéricas con algún texto | — |

**Para agregar una verificación**: sumala en `verificar()` (o en `_verificar_totales` / `_verificar_items` / `_verificar_tabla`) devolviendo `Verificacion(campo=..., nivel=..., mensaje=..., sugerencia=...)`. Si `campo` es uno del encabezado o de los totales, la pantalla pinta ese input y el botón «Usar» ya sabe dónde poner la sugerencia (`logica.ts` → `usarSugerencia`). Agregá una prueba en `test_a_excel.py`.

## Cómo probar

```
env -u MODULONUEVO_CLIENTES_CLAVE .venv/Scripts/python.exe -m pytest backend/tests/test_a_excel.py -q -p no:warnings
cd frontend && npx ng test --watch=false
```

A mano: `.claude/launch.json` tiene `backend` (puerto 8000) y `frontend` (puerto 4210). Abrí `http://localhost:4210/pasar-a-excel` y subí la foto de un tique o una factura. En la app de escritorio el OCR usa el Tesseract que mete `build_app.ps1`.

Ejemplo real (la foto del tique de FGC): el OCR lee el neto `63.974,75` y el total `106.417,98` (confunde el 8 con el 6) y un CUIT con un dígito mal. La verificación propone `83.974,75`, `108.417,98` y `33714877769`. Con «Usar» en los tres queda todo verde.

## Qué falta / ideas para seguir

- [ ] **Idioma español del OCR.** Hoy Tesseract tiene solo `eng.traineddata` (tanto en esta PC como en el instalador). Con `spa.traineddata` mejoran los acentos y la Ñ. `installer/preparar_tesseract.py` ya lo copia si está instalado.
- [ ] **Varios comprobantes en un archivo.** Un PDF con N facturas distintas sale como uno solo (lo de `extraccion/documentos.py` separa por página; se podría reutilizar).
- [ ] **Editar las tablas** (CSV/Excel/texto) en la pantalla: hoy solo se ven (60 filas) y se pasan tal cual.
- [ ] **Un solo Excel con varios archivos**: hoy cada archivo genera su `.xlsx`. Se podría agregar una hoja «Resumen» con una fila por comprobante.
- [ ] **Más formatos de detalle.** `_items()` entiende el tique (descripción arriba, números abajo) y la factura de una línea; otros sistemas pueden necesitar su caso.
- [ ] **Mandar a Xubio** lo verificado: convertir `LecturaExcel` → `Comprobante` (`extraccion/modelo.py`) y usar `/api/comprobantes/preparar`.
- [ ] Rotación automática de fotos torcidas (Tesseract `osd`), recorte del fondo de la foto.
