# Control de Facturas · ARCA vs. Xubio

Controla las facturas de compras y ventas: baja **Mis Comprobantes** de ARCA y los
**Libros IVA** de Xubio, y los cruza en un Excel de control (`AUX - Control de facturas …`).

Es el mismo recorrido de la app de escritorio (`main_control.py`), ahora como módulo web
de ContadorWeb, con la misma línea visual que el módulo de Retenciones.

- **backend/** — API en FastAPI. Corre los bots de siempre en un hilo y expone el estado.
- **frontend/** — Angular 22 + Tailwind CSS.
- **bot_arca.py, bot_xubio.py, bot_xubio_common.py, auditor_fo.py** — los bots, en la raíz.
  Los usan **las dos** apps (escritorio y web): un arreglo en un bot vale para ambas.
- **config.json / xubio_credenciales.json** — clientes y cuenta de Xubio, compartidos con la
  app de escritorio. Tienen claves: **no van a git** (están en `.gitignore`).

## Requisitos

| Herramienta | Para qué |
|---|---|
| [uv](https://docs.astral.sh/uv/) | Python y dependencias del backend (Python 3.14) |
| Node.js 22+ y npm 11 | Frontend |
| Google Chrome | Los bots usan el Chrome instalado |

## Instalación

```bash
cd backend
uv sync
```

```bash
cd frontend
npm ci
```

## Levantar en desarrollo

Backend (http://localhost:8002, docs en http://localhost:8002/docs):

```bash
uv run --directory backend uvicorn app.main:app --port 8002 --reload --reload-dir app
```

Frontend (http://localhost:4210):

```bash
npm --prefix frontend start
```

Usa los puertos 8002/4210 para poder tenerlo abierto junto al módulo de Retenciones (8001/4200).

## Pantallas

1. **Nuevo control** — en cuatro pasos numerados:
   1. *Clientes*: «Disponibles» a la izquierda (buscador por nombre o CUIT), «A procesar» a la
      derecha, en el orden en que se van a procesar (se puede reordenar).
   2. *Período*: mes completo (por defecto el mes anterior) u otro rango de fechas.
   3. *Qué controlar*: Ventas / Compras y los pasos ARCA → Xubio → Auditoría.
   4. *Cuenta de Xubio y carpeta de salida* (en la computadora del servidor).

   A la derecha, un resumen con lo que va a pasar y por qué todavía no se puede ejecutar.
2. **Ejecuciones** — el progreso en vivo: cada cliente con sus pasos (en curso, listo, con
   avisos, error), los Excel generados para bajar, y el registro de los bots. Entre cliente y
   cliente aparece la pausa con cuenta regresiva: *Continuar ahora*, *No preguntar más* o
   *Cancelar todo* (sin respuesta, sigue sola). Abajo, las últimas ejecuciones.
3. **Clientes** — alta, edición y baja (solo supervisores). La clave fiscal nunca llega al
   navegador: al editar, dejarla vacía mantiene la guardada.

## Usuarios (niveles de ContadorWeb)

Igual que Retenciones: sin ingreso propio. ContadorWeb abre `/?pase=<pase firmado>` con
`CONTROL_FCT_MODULO_SECRETO` (el mismo valor en los dos servicios, mínimo 32 caracteres).

| Nivel en ContadorWeb | Rol |
|---|---|
| 200 / 100, Configuración o todos los módulos | **supervisor** (además edita clientes) |
| Módulo `Impuestos/Control de facturas - Supervisión` | **supervisor** |
| Módulo `Impuestos/Control de facturas` | **usuario** (ejecuta controles) |

Sin el secreto (**modo local**) aparece el selector «Usuario de prueba» en el menú.

## Configuración

Opcional: copiar `backend/.env.example` a `backend/.env`. Lo principal:

| Variable | Default | |
|---|---|---|
| `CARPETA_SALIDA` | `~/Downloads` | Carpeta por defecto: `<carpeta>/<Cliente>/<mmaaaa>/Control de Facturas` |
| `PAUSA_ENTRE_CLIENTES_S` | `15` | Segundos de la pausa entre clientes |
| `CONFIG_CLIENTES` / `CREDENCIALES_XUBIO` | los `.json` de la raíz | Para usar otros archivos (por ejemplo, de prueba) |
| `XUBIO_USUARIO` / `XUBIO_CLAVE` | vacío | Cuenta de Xubio del servidor |
| `CONTROL_FCT_MODULO_SECRETO` | vacío | Secreto compartido con ContadorWeb |

En el `.env` usá barras normales en las rutas (`C:/Users/...`).

## Tests

```bash
cd backend
uv run pytest
```

Los tests usan bots simulados: no abren Chrome ni tocan el `config.json` real.

## Limitaciones

- Una ejecución por vez (los bots abren Chrome visible en el servidor; el doble factor de
  ARCA y el captcha de Xubio se completan en esa ventana).
- Cancelar no corta el paso en curso: termina ese paso y se frena.
- El historial de ejecuciones vive en memoria (últimas 20); los Excel quedan en disco.
