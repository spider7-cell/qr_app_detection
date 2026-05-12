$ErrorActionPreference = "Stop"
$project = "C:\qr_app_detection"
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell

$startShortcut = $shell.CreateShortcut((Join-Path $desktop "QR Desk.lnk"))
$startShortcut.TargetPath = Join-Path $project "Start_QR_Desk.vbs"
$startShortcut.WorkingDirectory = $project
$startShortcut.WindowStyle = 7
$startShortcut.Description = "Start QR Desk local scanner"
$startShortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,102"
$startShortcut.Save()

$stopShortcut = $shell.CreateShortcut((Join-Path $desktop "Stop QR Desk.lnk"))
$stopShortcut.TargetPath = Join-Path $project "Stop_QR_Desk.bat"
$stopShortcut.WorkingDirectory = $project
$stopShortcut.WindowStyle = 7
$stopShortcut.Description = "Stop QR Desk local scanner"
$stopShortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,131"
$stopShortcut.Save()

Write-Host "Created QR Desk shortcuts on Desktop."