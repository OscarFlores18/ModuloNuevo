# Control de Facturas

Automatización de la descarga de comprobantes desde **ARCA (ex AFIP)** y
**Xubio**, y cruce de ambos Libros IVA para detectar partidas faltantes.

El proyecto está en **migración de app de escritorio a sitio web**:

- `backend/` — motor (bots + auditoría) expuesto como API HTTP con **FastAPI**.
  Es la parte activa del proyecto.
- `frontend/` — Angular 22 (mismo front que el módulo de ContadorWeb). Consume
  `/api/clientes`, `/api/ajustes` y `/api/ejecuciones`.
- `main_control.py` — app de escritorio original (Tkinter/customtkinter).
  Se conserva como referencia y reutiliza el mismo motor del backend.

> Guía técnica del backend: [`backend/README.md`](backend/README.md)

---

## Qué hace

Para cada cliente seleccionado ejecuta tres pasos encadenados:

1. **ARCA** — entra a `auth.afip.gob.ar`, abre "Mis Comprobantes" y descarga el
   Excel de **Emitidos** (ventas) y/o **Recibidos** (compras).
2. **Xubio** — entra por `connect.visma.com`, cambia a la empresa del cliente y
   descarga el **Libro IVA Ventas** y/o **Compras**.
3. **Auditoría** — cruza los dos Excel y genera un libro de trabajo con:
   - hoja **Mis Comprobantes** (ARCA) con IVA/Total ajustados,
   - hoja **Libro Iva** (Xubio) con totales, diferencias y conciliación,
   - hoja **Faltantes en Libro IVA**,
   - fórmulas `VLOOKUP`, detección de duplicados, notas de crédito con signo,
     conversión de comprobantes en USD por tipo de cambio y formato condicional.

La clave de conciliación es `LETRA-PTOVTA-NUMERO` (ej. `A-00012-00000123`).

---

## Requisitos

| Requisito | Detalle |
|---|---|
| Sistema | Windows 10/11 |
| Python | 3.11 o superior (verificado con **3.15.2**) |
| Google Chrome | Instalado. Los bots abren Chrome real (`channel="chrome"`), no el Chromium de Playwright |
| Tcl/Tk | Solo para la app de escritorio (`main_control.py`) |

Dependencias:

- backend: `fastapi`, `uvicorn`, `pydantic`, `playwright`, `openpyxl`, `pandas`, `numpy`
- escritorio: además `customtkinter`

---

## Instalación

```powershell
cd C:\Users\Admin\Desktop\proyectos\ModuloNuevo

# 1. Entorno virtual (recomendado)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

> Si PowerShell bloquea la activación:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

```powershell
# 2. Backend (activo)
pip install -r backend/requirements-dev.txt

# 3. App de escritorio (legacy, opcional)
pip install -r requirements.txt
```

No hace falta `python -m playwright install chromium`: los bots usan el Chrome
del sistema. Solo si querés el Chromium empaquetado de Playwright.

---

## Ejecutar el backend

Desde la raíz del repositorio:

```powershell
python -m uvicorn backend.app.main:app --port 8000
```

- API: <http://127.0.0.1:8000>
- Swagger: <http://127.0.0.1:8000/docs>
- Health: <http://127.0.0.1:8000/health>

El backend usa **un único hilo de trabajo** porque los scrapers abren Chrome
visible y no son seguros en paralelo. Los jobs se encolan y se cancelan de
forma cooperativa entre pasos.

### Variables de entorno

| Variable | Default | Uso |
|---|---|---|
| `MODULONUEVO_DATA_DIR` | `backend/data` | `config.json` y `xubio_credenciales.json` |
| `MODULONUEVO_OUTPUT_DIR` | `backend/salidas` | carpeta raíz de las salidas |
| `MODULONUEVO_LOG_DIR` | `backend/logs` | `backend.log` |
| `MODULONUEVO_FRONT_DIST` | `frontend/dist/control-fct-frontend/browser` | build de Angular que sirve el backend |
| `MODULONUEVO_CORS` | `http://localhost:4200,...` | orígenes permitidos (Angular) |
| `MODULONUEVO_PAUSA_S` | `15` | pausa entre clientes en la web |
| `MODULONUEVO_ARCA_PING_MIN` | `9` | cada cuántos minutos se consulta ARCA para no perder la sesión |
| `MODULONUEVO_ARCA_RENOVAR_MIN` | `50` | a los cuántos minutos se renueva el login de ARCA |

### API (resumen)

Contrato del frontend web:

| Método | Ruta | Descripción |
|---|---|---|
| `GET/POST` | `/api/clientes` | listar / crear (la clave fiscal nunca se devuelve) |
| `GET/PUT/DELETE` | `/api/clientes/{nombre}` | detalle / editar / borrar |
| `POST` | `/api/clientes/{nombre}/renombrar` | renombrar cliente |
| `GET` | `/api/ajustes` | carpeta de salida, pausa y credencial de Xubio |
| `DELETE` | `/api/ajustes/xubio` | olvidar la credencial guardada de Xubio |
| `GET/POST` | `/api/ejecuciones` | historial / iniciar un control |
| `GET` | `/api/ejecuciones/activa` | la ejecución en curso (o `null`) |
| `GET` | `/api/ejecuciones/{id}` | detalle, pasos, archivos y log (`?log_desde=N`) |
| `POST` | `/api/ejecuciones/{id}/respuesta` | responder la pausa (`continuar`, `cancelar`, `no_preguntar`) |
| `POST` | `/api/ejecuciones/{id}/cancelar` | cancelar |
| `GET` | `/api/ejecuciones/{id}/clientes/{i}/archivos/{nombre}` | descargar un resultado |
| `GET` | `/api/yo` | identidad del usuario (encabezados `X-Usuario-*`) |
| `POST` | `/api/sesion/entrar` | canje del pase de ContadorWeb (modo local) |

Rutas de uso programático (se mantienen):

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | estado del backend |
| `GET/PUT` | `/api/xubio/credenciales` | credenciales del operador |
| `POST` | `/api/jobs` | encolar control de facturas |
| `GET` | `/api/jobs[/{id}]` | listar / detallar jobs |
| `GET` | `/api/jobs/{id}/logs` | logs (soporta `?desde=N`) |
| `GET` | `/api/jobs/{id}/files[/{nombre}]` | listar / descargar resultados |
| `POST` | `/api/jobs/{id}/cancel` | cancelar |
| `DELETE` | `/api/jobs/{id}` | borrar job terminado |

Ejemplo:

```powershell
$body = @{
  clientes      = @("Cliente Ejemplo SRL")
  fecha_desde   = "01/03/2025"
  fecha_hasta   = "31/03/2025"
  ventas        = $true
  compras       = $false
  pasos         = @("arca", "xubio", "auditoria")
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/jobs `
  -ContentType "application/json" -Body $body
```

Estados: `queued` → `running` → `succeeded` | `partial` | `failed` | `cancelled`.
`partial` significa que se generaron archivos pero algún paso falló; el detalle
queda en `errores`.

---

## Ejecutar la web (Angular + backend)

En desarrollo, dos terminales:

```powershell
# 1. API
python -m uvicorn backend.app.main:app --port 8000

# 2. Frontend (http://localhost:4210)
cd frontend
npm ci
npm start
```

El front en desarrollo apunta a `http://localhost:8000/api`
(`src/environments/environment.ts`).

En producción el backend sirve el build del front en el mismo dominio:

```powershell
cd frontend
npm run build
cd ..
python -m uvicorn backend.app.main:app --port 8000
```

Abrí <http://127.0.0.1:8000>: la API queda en `/api` y el `dist` de Angular se
sirve desde la raíz (`MODULONUEVO_FRONT_DIST`).

### Cómo corre un control

- Un solo control a la vez. Los bots abren Chrome visible en la máquina del
  servidor (doble factor de ARCA, captcha de Xubio).
- **Un login por credencial**: la sesión de ARCA se comparte entre clientes y
  entre Emitidos/Recibidos; la de Xubio se comparte entre clientes (solo cambia
  de empresa). Antes se hacía login por cada paso.
- ARCA cierra la sesión por inactividad (~15 min). Antes de cada operación se
  consulta el portal con las cookies de la sesión (`context.request.get`) y, si
  venció, se vuelve a entrar sin cerrar el navegador y se reintenta el paso.
- Entre clientes hay una pausa (`MODULONUEVO_PAUSA_S`) con cuenta regresiva:
  «Continuar», «Cancelar todo» o «No preguntar más».

---

## Ejecutar la app de escritorio (legacy)

```powershell
.\.venv\Scripts\Activate.ps1
python main_control.py
```

La GUI importa el motor desde `backend/app/`, así que no hay lógica duplicada.
Su flujo es el mismo que el de la API, con diálogos y selección de carpetas.

---

## Configuración

El formato es **idéntico** en la app de escritorio y en el backend, para que
ambas puedan convivir durante la migración.

### `config.json`

```json
{
  "clientes": {
    "Cliente Ejemplo SRL": {
      "arca": {
        "cuit_login": "20111111112",
        "clave": "clave_fiscal",
        "cuit_representado": "20222222223"
      },
      "xubio": {
        "url": "/NXV/ruta/interna/de/xubio"
      }
    }
  }
}
```

- `cuit_login`: CUIT con el que se entra a ARCA.
- `cuit_representado`: CUIT del cliente que se administra.
- `xubio.url`: se conserva por compatibilidad; el cambio de empresa se hace
  **por nombre**.
- Claves heredadas (`agip`, `arba`, `categoria`, `jurisdicciones`) se preservan.

### `xubio_credenciales.json`

Credenciales del operador (no del cliente):

```json
{
  "user": "usuario@ejemplo.com",
  "pass": "********",
  "recordar": true
}
```

> Ambos archivos guardan credenciales **en texto plano**. No los subas al repo
> (ya están en `.gitignore`).

---

## Salida

```
<base>/
└── <Cliente>/
    └── <MMAAAA>/
        └── Control de Facturas/
            ├── Mis comprobantes - Emitidos - <Cliente> - <MM-AA> al <MM-AA>.xlsx
            ├── Mis comprobantes - Recibidos - <Cliente> - <MM-AA> al <MM-AA>.xlsx
            ├── IVA VENTAS - <Cliente> - <DD-MM-AAAA> al <DD-MM-AAAA>.xlsx
            ├── IVA COMPRAS - <Cliente> - <DD-MM-AAAA> al <DD-MM-AAAA>.xlsx
            └── AUX - Control de facturas <Ventas|Compras> con AFIP - <MM-AA> - <Cliente>.xlsx
```

- En el backend, `<base>` es `MODULONUEVO_OUTPUT_DIR` o `carpeta_salida` del job.
- En la app de escritorio, es la carpeta elegida en la ventana.
- Los logs del backend van a `MODULONUEVO_LOG_DIR/backend.log`; la GUI sigue
  escribiendo `debug_log.txt` en su carpeta de salida.

---

## Tests

```powershell
python -m pytest
```

Los tests no abren Chrome: cubren rutas, storage, modelos, API (con un job
simulado), el contrato web (con sesiones simuladas) y el auditor con Excel
sintéticos de ARCA y Xubio.

---

## Problemas comunes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `No se encontró la empresa 'X'...` | El nombre en `config.json` no coincide con Xubio | Corregir el nombre del cliente |
| `No se encontró el archivo de ARCA/Xubio esperado` | Falló el paso anterior (login, descarga o sin datos) | Revisar `backend/logs/backend.log` o los logs del job |
| ARCA dice "congestionado" y segmenta fechas | AFIP limita el rango de consulta | Es automático; si falla a 15 días, reintentar más tarde |
| El bot queda "colgado" en ARCA | Diálogo nativo del navegador | Ya se cierra solo (`manejar_dialogo`) |
| `ModuleNotFoundError: fastapi` | Faltan dependencias del backend | `pip install -r backend/requirements-dev.txt` |
| La ventana no abre | Falta Tcl/Tk | Reinstalar Python marcando "tcl/tk and IDLE" |
| Chrome no encontrado | No está instalado en la ruta estándar | Instalar Chrome |

---

## Estructura

```
ModuloNuevo/
├── backend/
│   ├── app/
│   │   ├── api/            # endpoints HTTP
│   │   ├── auditor/        # cruce FO 04-01
│   │   ├── bots/           # ARCA, Xubio, login compartido
│   │   ├── core/           # config, modelos, errores, storage, logging
│   │   ├── services/       # orquestación + cola de jobs
│   │   └── main.py         # FastAPI
│   ├── tests/
│   ├── requirements.txt
│   └── README.md
├── frontend/               # Angular 22 (web)
├── main_control.py         # GUI legacy (reutiliza backend/app)
├── requirements.txt        # dependencias de la GUI
└── README.md
```

### Contrato de los bots

- Reciben `log: Callable[[str], None]`; no escriben en consola ni en la GUI.
- Devuelven `BotResult` (`archivos`, `sin_datos`, `errores`, `detalle`).
- Fallan con errores tipados (`LoginError`, `EmpresaNoEncontrada`,
  `TimeoutDescarga`, `ArchivoNoEncontrado`, `Cancelado`...).
- Cierran el navegador en un `finally`.
- Sin emojis en logs.
