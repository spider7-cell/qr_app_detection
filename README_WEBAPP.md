# QR Desk

Local desktop-style QR scanning application around `qr_static.py`.

## Run

Recommended:

```powershell
.\Start_QR_Desk.bat
```

Development fallback:

```powershell
python run_qrdesk.py
```

Open:

```text
http://127.0.0.1:8000
```

## What it includes

- FastAPI backend
- SQLite database in `app_data/qr_app.db`
- Local media storage in `app_data/`
- Browser dashboard for uploading images, running scans, browsing results, and exporting tables
- Scan profiles: `fast`, `balanced`, `accuracy`, and `slow`
- Local admin authentication
- CSV and Excel-compatible exports
- Desktop launcher scripts
- Prepared external camera workspace

## Login

Default account:

```text
username: admin
password: admin123
```

Change the password from the `Security` panel before delivery.

## Current Tested Baseline

- Upload flow tested: upload image, scan, open result, QR cards, CSV export, Excel export.
- Reference set tested through the app: around `28/38` decoded QR codes without crashing.
- Keep the app stable now; focus future work on improving the QR engine hard images.

## Exports

- `QR Table`: clean CSV/table output.
- `Excel`: Excel-compatible table.
- IMEI values are exported as text so Excel does not convert them to scientific notation.
- Use `Open Exports` in the app to open the local export folder.

## Desktop Packaging

Existing launcher files:

- `Start_QR_Desk.bat`
- `Start_QR_Desk.vbs`
- `Stop_QR_Desk.bat`
- `Stop_QR_Desk.ps1`
- `Build_QR_Desk_EXE.bat`
- `Build_QR_Desk_EXE.ps1`

The installable desktop version should be built only after the QR engine baseline is frozen.

## Handover

See `STARTUP_HANDOVER.md` for the startup delivery checklist.

## Notes

- Uploads use raw file PUT requests from the frontend, so no extra multipart dependency is required.
- The detector engine remains `qr_static.py`.
- Reference-set scans use the repo `images/` directory.
