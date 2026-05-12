$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "Installing desktop packaging dependencies..."
python -m pip install -r requirements_desktop.txt

Write-Host "Building QR Desk.exe..."
$appArgs = @(
  "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
  "--name", "QR Desk",
  "--icon", "assets\qrdesk_icon.ico",
  "--add-data", "qrdesk_index.html;.",
  "--add-data", "qrdesk_app.css;.",
  "--add-data", "qrdesk_app.js;.",
  "--add-data", "images;images",
  "--add-data", "assets;assets",
  "--collect-all", "pyzbar",
  "--hidden-import", "pystray",
  "--hidden-import", "PIL._tkinter_finder",
  "qrdesk_desktop.py"
)
& python @appArgs

Write-Host "Preparing installer payload..."
if (Test-Path installer_payload) { Remove-Item installer_payload -Recurse -Force }
New-Item -ItemType Directory -Force -Path "installer_payload\QR Desk" | Out-Null
Copy-Item "dist\QR Desk\*" "installer_payload\QR Desk" -Recurse -Force

Write-Host "Building QR Desk Setup.exe..."
$setupArgs = @(
  "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
  "--name", "QR Desk Setup",
  "--icon", "assets\qrdesk_icon.ico",
  "--add-data", "installer_payload\QR Desk;QR Desk",
  "qrdesk_installer.py"
)
& python @setupArgs

Write-Host "Done: dist\QR Desk\QR Desk.exe"
Write-Host "Done: dist\QR Desk Setup.exe"
