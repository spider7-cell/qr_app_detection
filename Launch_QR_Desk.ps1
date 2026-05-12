$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$url = "http://127.0.0.1:8000"
$health = "$url/api/health"
$log = Join-Path $project "qrdesk_server.log"
$err = Join-Path $project "qrdesk_server.err.log"

function Test-QrDeskReady {
    try {
        Invoke-WebRequest -Uri $health -UseBasicParsing -TimeoutSec 1 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Show-QrDeskError($message) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show($message, "QR Desk") | Out-Null
}

if (Test-QrDeskReady) {
    Start-Process $url
    exit 0
}

$desktopExe = Join-Path $project "dist\QR Desk\QR Desk.exe"
$sourceLauncher = Join-Path $project "qrdesk_desktop.py"

if (Test-Path $sourceLauncher) {
    $python = Get-Command py -ErrorAction SilentlyContinue
    if ($python) {
        Start-Process -FilePath $python.Source -ArgumentList @('-3', 'qrdesk_desktop.py') -WorkingDirectory $project -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError $err
    } else {
        $python = Get-Command python -ErrorAction SilentlyContinue
        if (-not $python) {
            Show-QrDeskError 'Python is not installed or not available in PATH.'
            exit 1
        }
        Start-Process -FilePath $python.Source -ArgumentList @('qrdesk_desktop.py') -WorkingDirectory $project -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError $err
    }
} elseif (Test-Path $desktopExe) {
    Start-Process -FilePath $desktopExe -WorkingDirectory (Split-Path -Parent $desktopExe)
} else {
    Show-QrDeskError 'QR Desk launcher files were not found.'
    exit 1
}

for ($i = 0; $i -lt 120; $i++) {
    if (Test-QrDeskReady) {
        exit 0
    }
    Start-Sleep -Milliseconds 250
}

Show-QrDeskError "QR Desk did not start. The browser was not opened because the local server is not ready.`n`nCheck:`n$log`n$err"
exit 1

