"""Leer comprobantes de cualquier archivo (Excel, CSV, PDF, fotos) y dejarlos
normalizados para revisarlos y subirlos a Xubio.

El traspaso a Xubio es el mismo de «Convertir AUX»: cada comprobante revisado se
pasa a una fila con las columnas de ARCA, de ahí al CSV de «Mis Comprobantes»
(`auditor.csv_arca`) y ese .zip lo carga `services.importacion_xubio`.

- `modelo`: el comprobante normalizado, tipos de ARCA y validaciones.
- `planillas`: XLSX, XLS y CSV (Mis Comprobantes de ARCA o planillas propias).
- `qr_afip`: el QR de las facturas electrónicas (datos exactos, sin OCR).
- `texto_factura`: los datos a partir del texto de una factura (PDF u OCR).
- `ocr`: Tesseract local.
- `documentos`: PDF (digital o escaneado) e imágenes.
- `extraer` / `preparar`: lo que usan los endpoints.
"""
