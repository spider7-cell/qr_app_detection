# QR Desk Startup Handover

## Current Stable Checkpoint

- App flow tested end-to-end: upload image, scan, open result, QR cards, CSV export, Excel export.
- Reference image set tested through the app backend: around `28/38` decoded QR codes without crashing.
- The current priority is stability and QR engine accuracy. Avoid large UI redesigns until the engine is improved.

## Daily Use

1. Double-click `Start_QR_Desk.bat` or the desktop shortcut named `QR Desk`.
2. Sign in with the configured admin account.
3. Use `Upload` for one or more board photos from the PC.
4. Click `Scan`.
5. Open the result, review decoded QR cards, then export `QR Table` or `Excel`.
6. Use `Reference Set` only for testing the bundled sample images.

## Admin Setup

- Default username is `admin`.
- Default password is `admin123` unless it was changed in the app.
- Change the password from `Security`.
- Default scan profile and worker count are also configurable from `Security`.
- Use `Open Exports` to access saved CSV/Excel output files.

## Export Files

- `QR Table` is the clean CSV/table output for decoded QR values.
- `Excel` is an Excel-compatible table for the startup team.
- IMEI values are exported as text to avoid Excel converting them into scientific notation.
- The app stores scan history and QR data locally in `app_data/qr_app.db`.

## Desktop Launcher

- `Start_QR_Desk.bat` starts the app.
- `Start_QR_Desk.vbs` can launch it with a hidden console.
- `Stop_QR_Desk.bat` / `Stop_QR_Desk.ps1` stop local QR Desk server processes.
- `Build_QR_Desk_EXE.bat` and `Build_QR_Desk_EXE.ps1` are the starting point for packaging an installable desktop version.

## Current Engine Baseline

- Current practical target: fast profile, reference set around `28/38`.
- Easy images should remain complete: 2 QR and clean 4 QR boards.
- Hard images still need engine work:
  - `6qrs1.jpeg`
  - `edited_6qrs.jpeg`
  - `4qr_updu6.jpeg`
  - `4qr_down_du6.jpeg`

## External Camera Plan

Camera support is prepared as a secondary module, not the current priority.

Future camera workflow:

1. Connect external USB/fixed webcam to the PC.
2. Start live preview.
3. Capture one board image.
4. Scan the captured image using the same QR engine.

The core QR engine should be stabilized before deeper camera integration.

## Handover Checklist

- Confirm the app starts from `Start_QR_Desk.bat`.
- Confirm login works.
- Confirm `Upload -> Scan -> Result -> Export` works.
- Confirm `Reference Set` finishes without crash.
- Confirm exported Excel/CSV files open correctly.
- Confirm admin password was changed before giving the app to the startup.
