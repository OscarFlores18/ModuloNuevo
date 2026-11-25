; Instalador de Control de Facturas (Inno Setup 6: https://jrsoftware.org/isinfo.php).
; Lo compila build_app.ps1 si encuentra ISCC.exe; a mano:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\ControlFCT.iss
; Sale dist\ControlFCT-Setup.exe: un solo archivo para llevar a otra PC.
;
; Hace lo mismo que instalar_app.ps1, pero con doble clic y con desinstalador:
; - por usuario, sin pedir administrador (AppData\Local\Programs\ControlFCT),
; - ícono en el Escritorio con atajo Ctrl+Alt+F y en el menú Inicio,
; - los datos (Documentos\ControlFCT) no se tocan al instalar ni al desinstalar.

#define Nombre "Control de Facturas"
; build_app.ps1 pasa /DVersion= con el número del archivo VERSION.
#ifndef Version
  #define Version "dev"
#endif
#define Exe "ControlFCT.exe"

[Setup]
; AppId fijo: con él una versión nueva se instala encima de la vieja.
AppId={{6F1C2A4E-3B7D-4E9A-9C51-8D2F0A7B6E13}
AppName={#Nombre}
AppVersion={#Version}
DefaultDirName={localappdata}\Programs\ControlFCT
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=ControlFCT-Setup
SetupIconFile=..\assets\icono.ico
UninstallDisplayIcon={app}\{#Exe}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; Si la app está abierta, el instalador pide cerrarla.
CloseApplications=yes
WizardStyle=modern

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "escritorio"; Description: "Crear un ícono en el Escritorio (atajo Ctrl+Alt+F)"; GroupDescription: "Accesos directos:"

[Files]
Source: "..\dist\ControlFCT\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Instalación limpia: que no queden archivos de una versión vieja.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#Nombre}"; Filename: "{app}\{#Exe}"
Name: "{autodesktop}\{#Nombre}"; Filename: "{app}\{#Exe}"; HotKey: ctrl+alt+f; Tasks: escritorio

[Run]
Filename: "{app}\{#Exe}"; Description: "Abrir {#Nombre}"; Flags: nowait postinstall skipifsilent
