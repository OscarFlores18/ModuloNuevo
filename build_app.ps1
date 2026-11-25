<#
.SYNOPSIS
  Arma Control de Facturas como app de escritorio de Windows (dist\ControlFCT\ControlFCT.exe).

.DESCRIPTION
  1. Compila Angular en modo producción (la API queda en /api, relativa).
  2. Instala/verifica pywebview y PyInstaller en el entorno de Python (.venv).
  3. Empaqueta desktop.py con PyInstaller (--onedir, --noconsole) incluyendo el build de Angular.
  4. Si está Inno Setup 6, arma además dist\ControlFCT-Setup.exe (un solo archivo
     para instalar en otra PC, ver installer\ControlFCT.iss).

  Los datos del programa empaquetado viven en Documentos\ControlFCT (no en la carpeta del .exe).

.PARAMETER Instalar
  Al terminar, la instala en esta PC como un solo ícono (Escritorio y menú
  Inicio, atajo Ctrl+Alt+F): corre instalar_app.ps1.

.PARAMETER CopiarDatos
  Copia backend\data (clientes, credenciales cifradas, claves e historial) a
  Documentos\ControlFCT\data, solo si allá todavía no hay nada. Sin esto, la app
  empaquetada arranca sin clientes.

.EXAMPLE
  .\build_app.ps1
  .\build_app.ps1 -CopiarDatos
#>
param(
    [switch]$CopiarDatos,
    [switch]$Instalar
)

$ErrorActionPreference = 'Stop'
$raiz = $PSScriptRoot
Set-Location $raiz

# Con la app abierta Windows no deja reemplazar sus archivos (dist\ControlFCT).
if (Get-Process ControlFCT -ErrorAction SilentlyContinue) {
    throw 'Control de Facturas está abierto: cerralo y volvé a correr este script.'
}

function Paso($texto) { Write-Host "`n==> $texto" -ForegroundColor Cyan }

# --- Python ---------------------------------------------------------------------
$python = Join-Path $raiz '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Paso 'Creando el entorno de Python (.venv)'
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear .venv (¿está instalado Python?).' }
}

# --- Versión ----------------------------------------------------------------------
# El número sale de VERSION; la fecha y hora del armado van adentro del .exe para
# distinguir dos instaladores del mismo número (se ve en el pie de la app).
$version = (Get-Content (Join-Path $raiz 'VERSION') -Raw).Trim()
$compilado = Get-Date -Format 'dd/MM/yyyy HH:mm'
$archivoCompilacion = Join-Path $raiz 'backend\app\_compilacion.py'
[IO.File]::WriteAllText($archivoCompilacion, "VERSION = '$version'`nCOMPILADO = '$compilado'`n", (New-Object Text.UTF8Encoding $false))
Write-Host "Versión $version (armada el $compilado)"

# --- 1. Angular -----------------------------------------------------------------
Paso '1/4 Compilando Angular (producción)'
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
Paso '2/4 Verificando pywebview y PyInstaller'
& $python -m pip install --disable-pip-version-check -q -r (Join-Path $raiz 'backend\requirements-desktop.txt')
if ($LASTEXITCODE -ne 0) { throw 'pip install falló.' }

# --- 3. PyInstaller -------------------------------------------------------------
Paso '3/4 Empaquetando con PyInstaller'
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
# Lectura de PDF (pypdfium2 trae su propia DLL de PDFium).
$argumentos += @('--collect-all', 'pypdfium2', '--collect-all', 'pypdfium2_raw')

# OCR de fotos y PDF escaneados: Tesseract va adentro de la app (<_internal>\tesseract,
# ver backend/app/extraccion/ocr.py). Se toma de vendor\tesseract o del Tesseract
# instalado en esta PC (instalador de UB Mannheim). installer\preparar_tesseract.py
# copia solo el .exe, las DLL que usa y los idiomas español e inglés, sin la
# depuración de MinGW: ~26 MB en vez de ~240 (el instalador tiene que quedar
# debajo de los 100 MB por archivo de GitHub).
$origenTess = @(
    (Join-Path $raiz 'vendor\tesseract'),
    (Join-Path $env:ProgramFiles 'Tesseract-OCR'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Tesseract-OCR')
) | Where-Object { $_ -and (Test-Path (Join-Path $_ 'tesseract.exe')) } | Select-Object -First 1
if ($origenTess) {
    $tess = Join-Path $raiz 'build\tesseract'
    & $python (Join-Path $raiz 'installer\preparar_tesseract.py') $origenTess $tess
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo preparar Tesseract.' }
    $argumentos += @('--add-data', "$tess;tesseract")
    Write-Host "Tesseract incluido desde $origenTess"
} else {
    Write-Host 'Sin Tesseract (vendor\tesseract ni instalado): de fotos y PDF escaneados solo se va a leer el QR de ARCA.' -ForegroundColor Yellow
}

# Ícono del .exe, del acceso directo y de la ventana (assets\icono.ico).
$icono = Join-Path $raiz 'assets\icono.ico'
if (Test-Path $icono) { $argumentos += @('--icon', $icono) }

& $python @argumentos
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller falló.' }
# Ya quedó adentro del .exe: afuera, la app corriendo desde el código vuelve a ser «sin fecha».
Remove-Item $archivoCompilacion -Force -ErrorAction SilentlyContinue

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

# --- 4. Instalador (opcional) ---------------------------------------------------
# Inno Setup 6 es gratis (https://jrsoftware.org/isdl.php). Sin él se saltea este paso.
$iscc = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
$setup = Join-Path $raiz 'dist\ControlFCT-Setup.exe'
# Uno viejo de otro build no puede pasar por el de esta versión.
if (Test-Path $setup) { Remove-Item $setup -Force }
if ($iscc) {
    Paso '4/4 Armando el instalador (Inno Setup)'
    & $iscc /Q "/DVersion=$version" (Join-Path $raiz 'installer\ControlFCT.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup falló.' }
} else {
    Write-Host "`nSin Inno Setup 6: no se arma ControlFCT-Setup.exe (opcional)." -ForegroundColor Yellow
}

Write-Host "`nListo: $exe" -ForegroundColor Green
if (Test-Path $setup) {
    Write-Host "Instalador para otra PC: $setup (un solo archivo, sin administrador)."
} else {
    Write-Host 'Para otra PC: copiá la carpeta dist\ControlFCT entera e instalar_app.ps1, y corré el script allá.'
}
Write-Host "Los datos de la app quedan en $(Split-Path $destinoDatos)."

if ($Instalar) {
    Paso 'Instalando en esta PC (ícono + atajo)'
    & (Join-Path $raiz 'instalar_app.ps1') -Origen (Join-Path $raiz 'dist\ControlFCT')
}
