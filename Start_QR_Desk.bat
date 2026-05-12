@echo off
cd /d "%~dp0"

if exist "%~dp0Start_QR_Desk.vbs" (
  wscript "%~dp0Start_QR_Desk.vbs"
  exit /b
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch_QR_Desk.ps1"
