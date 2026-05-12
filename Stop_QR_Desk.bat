@echo off
title Stop QR Desk
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Stop_QR_Desk.ps1"