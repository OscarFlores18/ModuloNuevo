<#
.SYNOPSIS
  Deja Control de Facturas como un solo ícono: copia la app a
  %LOCALAPPDATA%\Programs\ControlFCT y crea accesos directos con atajo de teclado.

.DESCRIPTION
  - La carpeta de la app (926 archivos) queda fuera de la vista, en AppData.
  - Crea un ícono en el Escritorio y en el menú Inicio. El del Escritorio lleva
    el atajo de teclado (Ctrl+Alt+F por defecto): abre la app desde cualquier
    lado y, si ya está abierta, la trae al frente.
  - No pide permisos de administrador ni toca el registro: es solo copiar
    archivos. Los datos (clientes, controles) siguen en Documentos\ControlFCT y
    no se tocan, ni al reinstalar ni al desinstalar.

.PARAMETER Origen
  Carpeta con ControlFCT.exe. Por defecto, dist\ControlFCT al lado de este
  script o, si no está, una carpeta ControlFCT al lado de este script.

.PARAMETER Atajo
  Atajo de teclado del ícono del Escritorio. Vacío ('') para no poner ninguno.

.PARAMETER Desinstalar
  Borra la carpeta de la app y los accesos directos (los datos quedan).

.EXAMPLE
  .\instalar_app.ps1
  .\instalar_app.ps1 -Atajo 'Ctrl+Alt+K'
  .\instalar_app.ps1 -Desinstalar
#>
param(
    [string]$Origen,
    [string]$Atajo = 'Ctrl+Alt+F',
    [switch]$Desinstalar
)

$ErrorActionPreference = 'Stop'
$nombre = 'Control de Facturas'
$destino = Join-Path $env:LOCALAPPDATA 'Programs\ControlFCT'
$shell = New-Object -ComObject WScript.Shell
$lnkEscritorio = Join-Path ([Environment]::GetFolderPath('Desktop')) "$nombre.lnk"
$lnkInicio = Join-Path ([Environment]::GetFolderPath('Programs')) "$nombre.lnk"

if (Get-Process ControlFCT -ErrorAction SilentlyContinue) {
    throw 'Control de Facturas está abierto: cerralo y volvé a correr este script.'
}

function Borrar-Accesos {
    foreach ($l in @($lnkEscritorio, $lnkInicio)) {
        if (Test-Path $l) { Remove-Item $l -Force }
    }
}

if ($Desinstalar) {
    Borrar-Accesos
    if (Test-Path $destino) { Remove-Item $destino -Recurse -Force }
    Write-Host "Desinstalado. Los datos siguen en $HOME\Documents\ControlFCT." -ForegroundColor Green
    return
}

if (-not $Origen) {
    $Origen = Join-Path $PSScriptRoot 'dist\ControlFCT'
    if (-not (Test-Path $Origen)) { $Origen = Join-Path $PSScriptRoot 'ControlFCT' }
}
$exeOrigen = Join-Path $Origen 'ControlFCT.exe'
if (-not (Test-Path $exeOrigen)) { throw "No está ControlFCT.exe en $Origen (¿corriste build_app.ps1?)." }

# Copia limpia: una versión vieja no puede dejar archivos sueltos mezclados.
if (Test-Path $destino) { Remove-Item $destino -Recurse -Force }
New-Item -ItemType Directory -Force (Split-Path $destino) | Out-Null
Copy-Item -Path $Origen -Destination $destino -Recurse
$exe = Join-Path $destino 'ControlFCT.exe'

# Si la carpeta vino de otra PC (zip bajado, mail), Windows la marca como «de
# internet» y SmartScreen avisa al abrir. Se quita la marca de lo copiado.
Get-ChildItem $destino -Recurse -File | Unblock-File

function Crear-Acceso($ruta, $tecla) {
    $l = $shell.CreateShortcut($ruta)
    $l.TargetPath = $exe
    $l.WorkingDirectory = $destino
    $l.IconLocation = "$exe,0"
    $l.Description = 'Control de Facturas: ARCA vs. Xubio'
    if ($tecla) { $l.Hotkey = $tecla }
    $l.Save()
}

Borrar-Accesos
# El atajo va en uno solo: Windows no admite el mismo en dos accesos directos.
Crear-Acceso $lnkEscritorio $Atajo
Crear-Acceso $lnkInicio ''

Write-Host "`nListo: $exe" -ForegroundColor Green
Write-Host "Ícono en el Escritorio y en el menú Inicio («$nombre»)."
if ($Atajo) { Write-Host "Atajo de teclado: $Atajo (abre la app o la trae al frente)." }
Write-Host "Los datos quedan en $HOME\Documents\ControlFCT."
