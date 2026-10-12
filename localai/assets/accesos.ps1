# Crea (o actualiza) los accesos directos de Querencia con su icono en la carpeta del proyecto.
# Los .bat no pueden llevar icono propio; el acceso directo si (y la ventana de consola lo hereda).
# Uso: powershell -NoProfile -ExecutionPolicy Bypass -File localai\assets\accesos.ps1 [-Escritorio]
param([switch]$Escritorio)

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$ico = Join-Path $PSScriptRoot 'querencia.ico'
$ws = New-Object -ComObject WScript.Shell

function Nuevo([string]$carpeta, [string]$nombre, [string]$destino, [string]$desc) {
    $lnk = $ws.CreateShortcut((Join-Path $carpeta "$nombre.lnk"))
    $lnk.TargetPath = Join-Path $root $destino
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = "$ico,0"
    $lnk.Description = $desc
    $lnk.Save()
}

$carpetas = @($root)
if ($Escritorio) { $carpetas += [Environment]::GetFolderPath('Desktop') }
foreach ($c in $carpetas) {
    Nuevo $c 'Querencia' 'querencia.cmd' 'Querencia - tu IA, en casa'
    Nuevo $c 'Instalar Querencia' 'instalar-querencia.bat' 'Asistente de instalacion de Querencia'
}
