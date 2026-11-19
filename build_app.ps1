<#
.SYNOPSIS
  Arma Control de Facturas como app de escritorio de Windows (dist\ControlFCT\ControlFCT.exe).

.DESCRIPTION
  1. Compila Angular en modo producción (la API queda en /api, relativa).
  2. Instala/verifica pywebview y PyInstaller en el entorno de Python (.venv).
  3. Empaqueta desktop.py con PyInstaller (--onedir, --noconsole) incluyendo el build de Angular.

  Los datos del programa empaquetado viven en Documentos\ControlFCT (no en la carpeta del .exe).

.PARAMETER CopiarDatos
  Copia backend\data (clientes, credenciales cifradas, claves e historial) a
  Documentos\ControlFCT\data, solo si allá todavía no hay nada. Sin esto, la app
  empaquetada arranca sin clientes.

.EXAMPLE
  .\build_app.ps1
  .\build_app.ps1 -CopiarDatos
#>
param(
    [switch]$CopiarDatos
)

$ErrorActionPreference = 'Stop'
$raiz = $PSScriptRoot
Set-Location $raiz

function Paso($texto) { Write-Host "`n==> $texto" -ForegroundColor Cyan }

# --- Python ---------------------------------------------------------------------
$python = Join-Path $raiz '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Paso 'Creando el entorno de Python (.venv)'
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear .venv (¿está instalado Python?).' }
}

# --- 1. Angular -----------------------------------------------------------------
Paso '1/3 Compilando Angular (producción)'
Push-Location (Join-Path $raiz 'frontend')
try {
    if (-not (Test-Path 'node_modules')) {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw 'npm ci falló.' }
    }
    npx ng build --configuration production
    if ($LASTEXITCODE -ne 0) { throw 'ng build falló.' }
} finally {
    Pop-Location
}
$front = Join-Path $raiz 'frontend\dist\control-fct-frontend\browser'
if (-not (Test-Path (Join-Path $front 'index.html'))) { throw "No está el build de Angular en $front" }

# --- 2. Dependencias de escritorio ----------------------------------------------
Paso '2/3 Verificando pywebview y PyInstaller'
& $python -m pip install --disable-pip-version-check -q -r (Join-Path $raiz 'backend\requirements-desktop.txt')
if ($LASTEXITCODE -ne 0) { throw 'pip install falló.' }

# --- 3. PyInstaller -------------------------------------------------------------
Paso '3/3 Empaquetando con PyInstaller'
$argumentos = @(
    '-m', 'PyInstaller', 'desktop.py',
    '--noconfirm', '--clean',
    '--onedir',
    '--noconsole',
    '--name', 'ControlFCT',
    # El build de Angular queda en <_MEIPASS>\frontend (ver backend/app/core/config.py).
    '--add-data', "$front;frontend",
    # Rutas y servicios del backend que se importan por nombre.
    '--collect-submodules', 'backend',
    '--collect-submodules', 'uvicorn',
    # Lo que usan los diálogos «Guardar como» del backend.
    '--hidden-import', 'tkinter.filedialog'
)
$icono = Join-Path $raiz 'frontend\public\favicon.ico'
if (Test-Path $icono) { $argumentos += @('--icon', $icono) }

& $python @argumentos
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller falló.' }

$exe = Join-Path $raiz 'dist\ControlFCT\ControlFCT.exe'
if (-not (Test-Path $exe)) { throw "No se generó $exe" }

# --- Datos (opcional) -----------------------------------------------------------
# La misma carpeta que usa la app (config.py: ~\Documents\ControlFCT).
$destinoDatos = Join-Path $HOME 'Documents\ControlFCT\data'
if ($CopiarDatos) {
    $origenDatos = Join-Path $raiz 'backend\data'
    if ((Test-Path $destinoDatos) -and (Get-ChildItem $destinoDatos -Force | Select-Object -First 1)) {
        Write-Host "Ya hay datos en ${destinoDatos}: no se copian (para no pisarlos)." -ForegroundColor Yellow
    } elseif (Test-Path $origenDatos) {
        New-Item -ItemType Directory -Force $destinoDatos | Out-Null
        Copy-Item -Path (Join-Path $origenDatos '*') -Destination $destinoDatos -Recurse
        Write-Host "Datos copiados a $destinoDatos" -ForegroundColor Green
    }
}

Write-Host "`nListo: $exe" -ForegroundColor Green
Write-Host 'Para instalar en otra PC copiá la carpeta dist\ControlFCT entera (no solo el .exe).'
Write-Host "Los datos de la app quedan en $(Split-Path $destinoDatos)."
