# Instalador de Control de Facturas

`ControlFCT-Setup.exe` instala **Control de Facturas** en Windows como cualquier
programa: doble clic, siguiente y listo. Queda un ícono en el Escritorio y en el
menú Inicio.

| | |
|---|---|
| Versión | 1.0.0 |
| Tamaño | 67,5 MB |
| SHA-256 | `3FDCCDE6F02C4605F8916CA1BC41A39E9A086E3708AE8A987B2A6B3FEEEB9CDC` |

---

## Qué necesita la PC

- **Windows 10 u 11 de 64 bits.** No funciona en Windows 7/8, Mac ni Linux.
- **Google Chrome instalado.** Los bots entran a ARCA y Xubio con Chrome. Si no
  está, la app lo avisa arriba en rojo. Se baja de <https://www.google.com/chrome>.
- **Internet**, para que los bots entren a ARCA y Xubio.

No hace falta instalar Python, Node ni nada más, ni tener permisos de administrador.

---

## Cómo instalarlo

1. Bajá `ControlFCT-Setup.exe`: en GitHub, abrí el archivo y tocá **Download raw
   file** (la flecha hacia abajo).
2. Hacé doble clic en el archivo.
3. Si aparece **«Windows protegió su PC»**: tocá **Más información** →
   **Ejecutar de todas formas**. Sale porque el programa no tiene firma digital
   paga; no es un virus.
4. Dejá tildado **«Crear un ícono en el Escritorio (atajo Ctrl+Alt+F)»** si lo
   querés en el Escritorio, y tocá **Siguiente** → **Instalar**.
5. Al terminar, dejá tildado **«Abrir Control de Facturas»** y tocá **Finalizar**.

> La **primera vez** que se abre puede tardar entre 30 segundos y un minuto,
> mientras Windows revisa los archivos nuevos. Después abre en unos segundos.

---

## Cómo se usa

- **Abrir:** doble clic en el ícono **Control de Facturas** del Escritorio o del
  menú Inicio, o el atajo **Ctrl+Alt+F** desde cualquier lado.
- **Si ya está abierta**, volver a abrirla no crea otra ventana: trae al frente
  la que ya está.
- **Cerrar:** con la X de la ventana. Se cierra todo, también los bots que
  estuvieran corriendo.

La primera vez la app arranca vacía:

1. Entrá a **Clientes**. La primera vez te pide **elegir una contraseña** (mínimo
   8 caracteres) que protege esa sección. Se pide **una sola vez en cada
   computadora**: después entra directo, aunque cierres la app. Se vuelve a pedir
   solo si tocás **Bloquear** o si cambia la contraseña.
2. Cargá los clientes con sus datos de ARCA.
3. En **Nuevo control**, cargá tu usuario y contraseña de Xubio.

---

## Dónde quedan las cosas

| Qué | Dónde |
|---|---|
| El programa | `C:\Users\<tu usuario>\AppData\Local\Programs\ControlFCT` |
| Clientes, contraseñas (cifradas) e historial | `Documentos\ControlFCT\data` |
| Excel que bajan los bots y los controles | `Documentos\ControlFCT\salidas` |
| Registro técnico (por si hay que revisar un error) | `Documentos\ControlFCT\logs` |

---

## Actualizar a una versión nueva

Bajá el `ControlFCT-Setup.exe` nuevo y ejecutalo igual que la primera vez: se
instala encima de la versión anterior. Si la app está abierta, el instalador
pide cerrarla. **Los clientes y los controles no se pierden.**

## Desinstalar

**Configuración → Aplicaciones → Aplicaciones instaladas → Control de Facturas
→ Desinstalar.** Se borra el programa y los íconos. La carpeta
`Documentos\ControlFCT` (clientes y controles) **queda**: si no la querés más,
borrala a mano.

---

## Problemas comunes

| Problema | Qué hacer |
|---|---|
| «Windows protegió su PC» | **Más información** → **Ejecutar de todas formas**. |
| El antivirus lo bloquea o lo borra | Es un falso positivo común en programas sin firma. Agregá una excepción para la carpeta del programa o avisá para revisarlo. |
| Aviso rojo «Falta Google Chrome» | Instalá Chrome y volvé a abrir la app. |
| La ventana queda en «Abriendo…» mucho tiempo | Esperá hasta un minuto la primera vez. Si no abre, cerrala y abrila de nuevo; si sigue, mandá el archivo `Documentos\ControlFCT\logs\backend.log`. |
| Ctrl+Alt+F no hace nada | El atajo solo existe si se tildó el ícono del Escritorio al instalar. Reinstalá con esa casilla tildada. |

---

## Para quien arma el instalador

El instalador se genera desde el código con:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_app.ps1
```

Necesita Python, Node e [Inno Setup 6](https://jrsoftware.org/isdl.php) en la PC
donde se arma (no en la de quien lo usa). Sale en `dist\ControlFCT-Setup.exe`;
copialo a esta carpeta para publicarlo. Más detalle en el
[README principal](../README.md#app-de-windows-un-solo-ícono).
