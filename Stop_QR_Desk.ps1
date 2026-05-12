$ErrorActionPreference = "SilentlyContinue"
$project = "C:\qr_app_detection"
$stopped = 0

# Ask the running web app to shut down when possible. If auth blocks this, continue with local process cleanup.
try {
    Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/admin/shutdown" -Method POST -UseBasicParsing -TimeoutSec 1 | Out-Null
    Start-Sleep -Milliseconds 700
} catch {}

Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -and (
            ($_.CommandLine -like "*run_qrdesk.py*" -and $_.CommandLine -like "*$project*") -or
            ($_.CommandLine -like "*qrdesk_desktop.py*" -and $_.CommandLine -like "*$project*") -or
            ($_.CommandLine -like "*$project*dist*QR Desk*QR Desk.exe*")
        )
    } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force
        $stopped += 1
    }

if ($stopped -eq 0) {
    Write-Host "QR Desk was not running."
} else {
    Write-Host "QR Desk stopped ($stopped process(es))."
}
Start-Sleep -Seconds 1
