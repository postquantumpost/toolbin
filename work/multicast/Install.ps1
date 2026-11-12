
# powershell -ExecutionPolicy Bypass -File .\Install.ps1
# create shortcut:
# pythonw.exe "C:\runhere\pi_multicast.py"
# Press Win + R, type shell:startup, and press Enter. This opens the current user's Startup folder.

$ErrorActionPreference = 'Stop'

$source = Join-Path $PSScriptRoot 'pi_multicast.py'
$destinationDirectory = 'C:\runhere'
$destination = Join-Path $destinationDirectory 'pi_multicast.py'

if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Program not found: $source"
}

New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null
Copy-Item -LiteralPath $source -Destination $destination -Force

Write-Host "Installed pi_multicast.py to $destination"
