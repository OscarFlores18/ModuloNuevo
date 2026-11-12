# Backend - Control de Facturas

Motor de automatización migrado de la app de escritorio a un servicio HTTP.
No tiene GUI: expone una API y ejecuta los bots en un único hilo de trabajo.

```
backend/
├── app/
│   ├── api/
│   │   ├── deps.py              # repositorios y JobManager desde app.state
│   │   ├── router.py
│   │   └── routes/
│   │       ├── clientes.py      # credenciales del operador de Xubio
│   │       ├── health.py
│   │       ├── jobs.py          # crear/consultar/cancelar/descargar
│   │       └── web.py           # contrato del frontend (clientes, ajustes, ejecuciones)
│   ├── auditor/
│   │   └── cruce_fo.py          # cruce ARCA <-> Xubio (FO 04-01)
│   ├── bots/
│   │   ├── arca.py              # "Mis Comprobantes" + sesión con keep-alive
│   │   ├── sesiones.py          # GestorSesiones: una sesión por credencial y job
│   │   ├── xubio.py             # Libro IVA Ventas/Compras
│   │   └── xubio_common.py      # login y cambio de empresa
│   ├── core/
│   │   ├── config.py            # rutas y CORS por variables de entorno
│   │   ├── errors.py            # errores tipados
│   │   ├── logging.py           # logging + JobLog
│   │   ├── models.py            # Pydantic: JobRequest, Job, BotResult...
│   │   ├── paths.py             # <base>/<Cliente>/<MMAAAA>/Control de Facturas
│   │   ├── storage.py           # config.json / xubio_credenciales.json
│   │   └── usuarios.py          # identidad local (X-Usuario-*)
│   ├── services/
│   │   ├── control_facturas.py  # orquestación (equivalente a _run_scrappy)
│   │   ├── ejecuciones.py       # ejecuciones web (pasos, pausa, archivos)
│   │   └── jobs.py              # cola + worker + cancelación
│   └── main.py                  # FastAPI + lifespan
├── tests/                       # pytest (incluye Excel sintético del auditor)
├── data/                        # config.json (no versionado)
├── logs/                        # backend.log
├── salidas/                     # salida por defecto de los jobs
├── requirements.txt
└── requirements-dev.txt
```

## Ejecutar

Desde la raíz del repositorio:

```powershell
python -m pip install -r backend/requirements-dev.txt
python -m uvicorn backend.app.main:app --port 8000
```

- API: http://127.0.0.1:8000
- Swagger: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/health

No se usa `uvicorn[standard]` a propósito: en Python 3.15 `pyyaml` todavía no
tiene wheel y obliga a compilar. `uvicorn` base alcanza (sin `--reload`).

## Configuración por entorno

| Variable | Default | Uso |
|---|---|---|
| `MODULONUEVO_DATA_DIR` | `backend/data` | `config.json` y `xubio_credenciales.json` |
| `MODULONUEVO_OUTPUT_DIR` | `backend/salidas` | carpeta raíz por defecto de los jobs |
| `MODULONUEVO_LOG_DIR` | `backend/logs` | `backend.log` |
| `MODULONUEVO_CORS` | `http://localhost:4200,http://127.0.0.1:4200` | orígenes permitidos (Angular) |
| `MODULONUEVO_FRONT_DIST` | `frontend/dist/control-fct-frontend/browser` | build de Angular que sirve el backend |
| `MODULONUEVO_PAUSA_S` | `15` | pausa entre clientes |
| `MODULONUEVO_ARCA_PING_MIN` | `9` | cada cuántos minutos se consulta ARCA (keep-alive) |
| `MODULONUEVO_ARCA_RENOVAR_MIN` | `50` | a los cuántos minutos se renueva el login de ARCA |

El formato de `config.json` es **idéntico** al de la app de escritorio, así que
ambas pueden convivir durante la migración. Si el archivo todavía tiene la
clave legacy `xubio_global`, se migra sola a `xubio_credenciales.json` la
primera vez que se lee.

## API

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | estado del backend y del worker |
| `GET/POST` | `/api/clientes` | listar / crear (la clave fiscal nunca se devuelve) |
| `GET/PUT/DELETE` | `/api/clientes/{nombre}` | detalle / editar / eliminar (conserva claves extra: AGIP, ARBA...) |
| `POST` | `/api/clientes/{nombre}/renombrar` | renombrar |
| `GET` | `/api/ajustes` | carpeta de salida, pausa y credencial de Xubio |
| `DELETE` | `/api/ajustes/xubio` | olvidar la credencial de Xubio |
| `GET/POST` | `/api/ejecuciones` | historial / iniciar un control (contrato del frontend) |
| `GET` | `/api/ejecuciones/activa` | ejecución en curso (o `null`) |
| `GET` | `/api/ejecuciones/{id}` | detalle, pasos, archivos y log (`?log_desde=N`) |
| `POST` | `/api/ejecuciones/{id}/respuesta` | responder la pausa |
| `POST` | `/api/ejecuciones/{id}/cancelar` | cancelar |
| `GET` | `/api/ejecuciones/{id}/clientes/{i}/archivos/{nombre}` | descargar un archivo |
| `GET` | `/api/yo` | identidad local (`X-Usuario-*`) |
| `POST` | `/api/sesion/entrar` | canje del pase de ContadorWeb |
| `GET` | `/api/xubio/credenciales` | usuario, `recordar` y `tiene_pass` (nunca la clave) |
| `PUT` | `/api/xubio/credenciales` | guardar usuario/clave |
| `POST` | `/api/jobs` | encolar un control de facturas |
| `GET` | `/api/jobs` | listar jobs (resumen) |
| `GET` | `/api/jobs/{id}` | detalle con logs y archivos |
| `GET` | `/api/jobs/{id}/logs?desde=N` | logs incrementales |
| `GET` | `/api/jobs/{id}/files` | archivos generados |
| `GET` | `/api/jobs/{id}/files/{nombre}` | descargar un archivo |
| `POST` | `/api/jobs/{id}/cancel` | cancelación cooperativa |
| `DELETE` | `/api/jobs/{id}` | borrar un job terminado |

### Crear un job

```json
{
  "clientes": ["Cliente Ejemplo SRL"],
  "fecha_desde": "01/03/2025",
  "fecha_hasta": "31/03/2025",
  "ventas": true,
  "compras": false,
  "pasos": ["arca", "xubio", "auditoria"],
  "carpeta_salida": null,
  "xubio_user": "usuario@ejemplo.com",
  "xubio_pass": "opcional-si-ya-esta-guardada"
}
```

- `pasos` acepta `arca`, `xubio`, `auditoria`. Si se omite, corren los tres.
- `carpeta_salida` es opcional; si no se manda, se usa `MODULONUEVO_OUTPUT_DIR`.
- `xubio_pass` nunca se devuelve en las respuestas.

### Estados del job

`queued` → `running` → `succeeded` | `partial` | `failed` | `cancelled`

- `partial`: se generaron archivos pero algún paso falló (por ejemplo, Xubio
  Ventas falló y Compras salió bien). Los errores quedan en `errores`.
- `failed`: no se pudo completar (credenciales, login, error interno).
- La cancelación se atiende **entre clientes y entre pasos**: no se puede
  cortar un `click` de Playwright a mitad de camino.
- Los jobs viven en memoria: al reiniciar el backend se pierde el historial
  (los archivos generados quedan en disco).

## Contrato de los bots

Cada bot:

1. Recibe `log: Callable[[str], None]` (nunca escribe en `stdout` ni en una GUI).
2. Devuelve un `BotResult` (`archivos`, `sin_datos`, `errores`, `detalle`);
   nunca `None`.
3. Falla con errores tipados de `core/errors.py`:
   `ConfigInvalida`, `LoginError`, `EmpresaNoEncontrada`, `TimeoutDescarga`,
   `ArchivoNoEncontrado`, `SinDatos`, `Cancelado`.
4. Cierra el navegador en un `finally`.
5. No usa emojis en logs.

`BotResult` y `JobError` son el contrato estable para el frontend Angular.

## Tests

```powershell
python -m pytest
```

Cubren rutas, storage, modelos, API (con un job simulado) y el auditor con
Excel sintéticos de ARCA y Xubio. Los tests **no** abren Chrome.

## Pendientes para producción

- **Autenticación**: hoy la API no tiene login. Pensada para correr en
  `127.0.0.1`; si se publica, hay que agregar auth y HTTPS.
- **Persistencia de jobs**: el historial está en memoria. Se puede volcar a
  SQLite/JSON sin tocar la API.
- **`--reload`**: requiere `uvicorn[standard]`, que en Python 3.15 intenta
  compilar `pyyaml`. Mientras tanto, reiniciar el proceso a mano.
- **Selectores**: siguen dentro de cada bot. Si ARCA/Xubio cambian el HTML,
  conviene moverlos a un módulo de selectores por bot.
