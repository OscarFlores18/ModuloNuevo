"""Imputación de compras a los productos/servicios de Xubio.

- ``xubio_catalog_parser``: el XML del catálogo de Xubio → lista de `ProductoXubio`.
- ``almacen``: catálogos y reglas por CUIT guardados en JSON (``DATA_DIR``).
- ``clasificador_xubio``: memoria por CUIT + Gemini para elegir el PRODUCTOID.
"""
