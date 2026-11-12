# Frontend (pendiente)

Acá va la aplicación **Angular** que reemplazará a `main_control.py`.

Todavía no está implementada: la migración avanza primero por el backend.
El contrato que consumirá está documentado en [`../backend/README.md`](../backend/README.md)
y se puede explorar en vivo con Swagger (`http://127.0.0.1:8000/docs`).

## Flujo previsto

1. `GET /api/clientes` para el panel de disponibles.
2. `GET/PUT /api/xubio/credenciales` para el usuario de Xubio.
3. `POST /api/jobs` con clientes, fechas, operaciones y pasos.
4. Polling de `GET /api/jobs/{id}` y `GET /api/jobs/{id}/logs?desde=N`.
5. `GET /api/jobs/{id}/files` y descarga por archivo.
6. `POST /api/jobs/{id}/cancel`.

El backend ya permite CORS desde `http://localhost:4200`.
