# Control de Facturas: build de Angular + backend FastAPI en una sola imagen.
# El backend sirve el dist del frontend en la raíz y la API queda en /api.

FROM node:22-slim AS frontend
WORKDIR /front
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

# Chromium y sus dependencias del sistema para los bots en modo headless.
RUN playwright install --with-deps chromium

COPY . .
COPY --from=frontend /front/dist/control-fct-frontend ./frontend/dist/control-fct-frontend

# En Render el disco es efímero salvo que se monte uno en /data.
ENV MODULONUEVO_DATA_DIR=/data \
    MODULONUEVO_OUTPUT_DIR=/data/salidas \
    MODULONUEVO_LOG_DIR=/data/logs \
    MODULONUEVO_FRONT_DIST=/app/frontend/dist/control-fct-frontend/browser \
    MODULONUEVO_HEADLESS=1 \
    MODULONUEVO_CHROME_CHANNEL=""
RUN mkdir -p /data/salidas /data/logs

EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
