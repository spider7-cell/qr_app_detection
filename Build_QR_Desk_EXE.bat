@echo off
setlocal
cd /d "%~dp0"

echo.
echo Installing desktop packaging dependencies...
python -m pip install -r requirements_desktop.txt
if errorlevel 1 goto fail

echo.
echo Building QR Desk.exe...
python -m PyInstaller --noconfirm --clean --windowed --name "QR Desk" --icon "assets\qrdesk_icon.ico" ^
  --add-data "qrdesk_index.html;." ^
  --add-data "qrdesk_app.css;." ^
  --add-data "qrdesk_app.js;." ^
  --add-data "images;images" ^
  --add-data "assets;assets" ^
  --collect-all pyzbar ^
  --hidden-import pystray ^
  --hidden-import PIL._tkinter_finder ^
  qrdesk_desktop.py
if errorlevel 1 goto fail

echo.
echo Preparing installer payload...
if exist installer_payload rmdir /s /q installer_payload
mkdir "installer_payload\QR Desk"
xcopy "dist\QR Desk" "installer_payload\QR Desk" /E /I /Y >nul
if errorlevel 1 goto fail

echo.
echo Building QR Desk Setup.exe...
python -m PyInstaller --noconfirm --clean --onefile --windowed --name "QR Desk Setup" --icon "assets\qrdesk_icon.ico" ^
  --add-data "installer_payload\QR Desk;QR Desk" ^
  qrdesk_installer.py
if errorlevel 1 goto fail

echo.
echo Done.
echo App:   dist\QR Desk\QR Desk.exe
echo Setup: dist\QR Desk Setup.exe
pause
exit /b 0

:fail
echo.
echo Build failed. Check the messages above.
pause
exit /b 1
