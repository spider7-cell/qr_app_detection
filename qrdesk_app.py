from __future__ import annotations

import csv
import html
import json
import os, sys, threading, time
import secrets
import shutil
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

import qrdesk_db as db
from qrdesk_scanner import IMAGE_EXTS, ScanManager, sanitize_filename


BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
scan_manager = ScanManager()
app = FastAPI(title="QR Detector Desk")
ACTIVE_SESSIONS: dict[str, dict] = {}
SESSION_COOKIE = "qrdesk_session"
APP_VERSION = "1.0"

LOGIN_PAGE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>QR Desk Login</title>
    <style>
      :root { color-scheme: light; }
      * { box-sizing: border-box; }
      body { margin: 0; min-height: 100vh; display: grid; place-items: center; font-family: Segoe UI, Arial, sans-serif; background: linear-gradient(180deg, #eef4fb 0%, #dfe9f7 100%); color: #1f2a3a; }
      .login-shell { width: min(420px, calc(100vw - 32px)); background: rgba(255,255,255,.92); border: 1px solid #d7e1ef; border-radius: 24px; padding: 28px; box-shadow: 0 24px 50px rgba(42,64,102,.12); }
      .eyebrow { margin: 0 0 8px; font-size: 12px; text-transform: uppercase; letter-spacing: .12em; color: #667792; font-weight: 700; }
      h1 { margin: 0 0 8px; font-size: 34px; line-height: 1.05; }
      p { margin: 0; color: #53657f; }
      form { display: grid; gap: 16px; margin-top: 24px; }
      label { display: grid; gap: 8px; font-weight: 600; color: #2c3a4f; }
      input { width: 100%; border-radius: 14px; border: 1px solid #cad6e7; background: #f7faff; padding: 14px 16px; font-size: 16px; outline: none; }
      input:focus { border-color: #7aa6ff; box-shadow: 0 0 0 4px rgba(122,166,255,.18); }
      button { border: 0; border-radius: 14px; padding: 14px 16px; background: linear-gradient(135deg, #4f86d9, #6b9df5); color: white; font-size: 16px; font-weight: 700; cursor: pointer; }
      button:disabled { opacity: .7; cursor: wait; }
      .hint { margin-top: 14px; font-size: 13px; color: #667792; }
      .error { margin-top: 16px; min-height: 20px; color: #b53333; font-weight: 600; }
    </style>
  </head>
  <body>
    <main class="login-shell">
      <p class="eyebrow">Secure Access</p>
      <h1>QR Desk</h1>
      <p>Sign in to open the scanner workspace.</p>
      <form id="login-form">
        <label>Username<input id="login-username" name="username" type="text" autocomplete="username" required></label>
        <label>Password<input id="login-password" name="password" type="password" autocomplete="current-password" required></label>
        <button id="login-submit" type="submit">Sign in</button>
      </form>
      <div id="login-error" class="error"></div>
      <p class="hint">Local desktop authentication. Contact the admin if you need your access details.</p>
    </main>
    <script>
      const form = document.getElementById('login-form');
      const submit = document.getElementById('login-submit');
      const errorBox = document.getElementById('login-error');
      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        errorBox.textContent = '';
        submit.disabled = true;
        submit.textContent = 'Signing in...';
        try {
          const response = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              username: document.getElementById('login-username').value,
              password: document.getElementById('login-password').value,
            }),
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(data.detail || 'Login failed');
          window.location.href = '/';
        } catch (error) {
          errorBox.textContent = error.message || 'Login failed';
        } finally {
          submit.disabled = false;
          submit.textContent = 'Sign in';
        }
      });
    </script>
  </body>
</html>
"""


class CreateScanRequest(BaseModel):
    source_type: Literal["upload", "sample"] = "upload"
    session_id: str | None = None
    sample_files: list[str] = Field(default_factory=list)
    profile: Literal["fast"] = "fast"
    deep_timeout: int | None = None
    workers: int = Field(default=2, ge=1, le=4)
    label: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)

class AdminSettingsRequest(BaseModel):
    default_profile: Literal["fast"] = "fast"
    default_workers: int = Field(default=2, ge=1, le=4)
    export_folder: str = Field(default="", max_length=500)
PUBLIC_PATHS = {
    "/login",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",
    "/api/health",
    "/favicon.ico",
}
PUBLIC_PREFIXES = ("/assets/", "/media/")


def current_session_user(request: Request) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    user = ACTIVE_SESSIONS.get(token)
    return user if isinstance(user, dict) else None


def invalidate_user_sessions(username: str, keep_token: str | None = None) -> None:
    normalized = username.strip().lower()
    for token, session_user in list(ACTIVE_SESSIONS.items()):
        if token == keep_token:
            continue
        if str(session_user.get('username', '')).strip().lower() == normalized:
            ACTIVE_SESSIONS.pop(token, None)

def shutdown_process(delay: float = 0.8) -> None:
    time.sleep(delay)
    os._exit(0)

@app.middleware("http")
async def require_authentication(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
        return await call_next(request)
    if current_session_user(request):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    return RedirectResponse(url="/login", status_code=303)


def asset_version(path: Path) -> str:
    try:
        return str(path.stat().st_mtime_ns)
    except OSError:
        return "1"


def media_url(rel_path: str | None) -> str | None:
    if not rel_path:
        return None
    rel = rel_path.replace("\\", "/").lstrip("/")
    return f"/media/{rel}"


def serialize_app_settings(settings: dict[str, str] | None = None) -> dict:
    raw = settings or db.get_app_settings()
    try:
        workers = int(raw.get("default_workers") or 2)
    except (TypeError, ValueError):
        workers = 2
    return {
        "default_profile": raw.get("default_profile") or "fast",
        "default_workers": max(1, min(4, workers)),
        "export_folder": raw.get("export_folder") or "",
    }


def configured_export_folder() -> Path | None:
    folder = (db.get_app_settings().get("export_folder") or "").strip()
    if not folder:
        return None
    target = Path(folder).expanduser()
    if not target.is_absolute():
        target = (db.ROOT_DIR / target).resolve()
    target.mkdir(parents=True, exist_ok=True)
    return target


def mirror_exports_to_configured_folder(paths: list[Path]) -> None:
    target_dir = configured_export_folder()
    if target_dir is None:
        return
    for source in paths:
        try:
            if source.exists() and source.is_file():
                target = target_dir / source.name
                if source.resolve() != target.resolve():
                    shutil.copy2(source, target)
        except Exception:
            pass


def export_folder_path() -> Path:
    configured = configured_export_folder()
    if configured is not None:
        return configured
    db.SCANS_DIR.mkdir(parents=True, exist_ok=True)
    return db.SCANS_DIR


def open_folder_in_file_manager(path: Path) -> None:
    target = path.resolve()
    if hasattr(os, "startfile"):
        os.startfile(str(target))
        return
    raise RuntimeError("Opening folders is only supported in the desktop build")
def resolve_scan_label(scan_row: dict) -> str:
    label = str(scan_row.get("source_label") or "Scan").strip()
    normalized = label.lower().replace("_", " ").strip()
    if normalized in {"bundled sample images", "reference image set", "reference set"}:
        return "Reference Image Set"
    total_images = int(scan_row.get("total_images") or 0)
    if total_images == 1:
        filename = db.get_scan_primary_filename(scan_row["id"])
        if filename:
            return filename
    return label

def _scan_identifier(scan_id: str) -> str:
    return f"RUN-{scan_id[:8].upper()}"


def _image_identifier(image_id: str) -> str:
    return f"IMG-{image_id[:8].upper()}"


def _decoded_rows(image: dict) -> list[dict]:
    rows = []
    seen = set()
    for patch in image.get("patches", []):
        raw = str(patch.get("raw", "") or "").strip()
        imei = str(patch.get("imei", "") or "").strip()
        serial = str(patch.get("serial", "") or "").strip()
        if raw.startswith("layout_inferred::"):
            continue
        key = raw or f"{imei}|{serial}"
        if not key.strip("|") or key in seen:
            continue
        seen.add(key)
        rows.append({
            "imei": imei,
            "serial": serial,
            "raw": raw,
            "source": str(patch.get("source", "") or ""),
            "stage": str(patch.get("stage", "") or ""),
        })
    return rows


def serialize_scan(scan_row: dict) -> dict:
    return {
        "id": scan_row["id"],
        "created_at": scan_row["created_at"],
        "started_at": scan_row["started_at"],
        "finished_at": scan_row["finished_at"],
        "source_type": scan_row["source_type"],
        "source_label": resolve_scan_label(scan_row),
        "profile": scan_row["profile"],
        "deep_timeout": scan_row["deep_timeout"],
        "workers": scan_row["workers"],
        "status": scan_row["status"],
        "total_images": scan_row["total_images"],
        "processed_images": scan_row["processed_images"],
        "total_qr": scan_row["total_qr"],
        "localized_total_qr": scan_row.get("localized_total_qr", scan_row["total_qr"]),
        "total_expected": scan_row["total_expected"],
        "total_elapsed": float(scan_row.get("total_elapsed", 0.0) or 0.0),
        "error": scan_row["error"],
        "scan_identifier": _scan_identifier(scan_row["id"]),
    }


def _real_detected_qr_count(image: dict) -> int:
    count = 0
    seen = set()
    for patch in image.get('patches', []):
        raw = str(patch.get('raw', '') or '').strip()
        imei = str(patch.get('imei', '') or '').strip()
        serial = str(patch.get('serial', '') or '').strip()
        if raw.startswith('layout_inferred::'):
            continue
        key = raw or f'{imei}|{serial}'
        if not key.strip('|'):
            continue
        if key in seen:
            continue
        seen.add(key)
        count += 1
    return count


def _scan_real_total_qr(images: list[dict]) -> int:
    return sum(_real_detected_qr_count(image) for image in images)


def _localized_qr_count(image: dict) -> int:
    seen = set()
    count = 0
    for patch in image.get('patches', []):
        raw = str(patch.get('raw', '') or '').strip()
        bbox = tuple(patch.get('bbox', []) or [])
        key = raw or repr(bbox)
        if key in seen:
            continue
        seen.add(key)
        count += 1
    return count


def _scan_localized_total_qr(images: list[dict]) -> int:
    return sum(_localized_qr_count(image) for image in images)


def _image_counts(image: dict) -> tuple[int, int]:
    decoded = _real_detected_qr_count(image)
    localized = max(decoded, _localized_qr_count(image))
    return decoded, localized


def _image_export_paths(scan_id: str, image: dict) -> tuple[Path, Path]:
    token = db.export_name_token(scan_id, image_filenames=[image.get("filename", "")])
    export_dir = db.SCANS_DIR / scan_id / "exports"
    csv_path = export_dir / f"{token}_image_qr_table.csv"
    xls_path = export_dir / f"{token}_image_table.xls"
    return csv_path, xls_path


def _image_excel_table_html(scan_row: dict, image: dict) -> str:
    decoded = _real_detected_qr_count(image)
    rows = _decoded_rows(image)
    qr_rows = [(image.get("filename", ""), str(image.get("expected_qr", 0)), str(decoded), row.get("imei", ""), row.get("serial", "")) for row in rows]

    def table(headers, rows, text_columns=None):
        text_columns = set(text_columns or [])
        head = ''.join(f'<th>{html.escape(h)}</th>' for h in headers)
        body_rows = []
        for row in rows:
            cells = []
            for idx, cell in enumerate(row):
                value = str(cell)
                if idx in text_columns and value:
                    safe = html.escape(value)
                    cells.append(f'<td style="white-space:pre;">&#39;{safe}</td>')
                else:
                    cells.append(f'<td>{html.escape(value)}</td>')
            body_rows.append('<tr>' + ''.join(cells) + '</tr>')
        body = ''.join(body_rows) or f"<tr><td colspan='{len(headers)}'>No rows</td></tr>"
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    summary_rows = [
        ("Scan", resolve_scan_label(scan_row)),
        ("Image", image.get("filename", "-")),
        ("Profile", scan_row.get("profile") or "fast"),
        ("Expected QR", str(image.get("expected_qr", 0))),
        ("Detected QR", str(decoded)),
        ("Created", scan_row.get("created_at") or "-"),
    ]
    image_rows = [(
        image.get("filename", ""),
        str(image.get("expected_qr", 0)),
        str(decoded),
        f"{float(image.get('elapsed', 0) or 0):.1f}",
        image.get("error", "") or "",
    )]

    return (
        '<!doctype html><html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel" xmlns="http://www.w3.org/TR/REC-html40">'
        '<head><meta charset="utf-8"><meta name="ProgId" content="Excel.Sheet"><meta name="Generator" content="QR Desk">'
        '<style>body { font-family: Segoe UI, Arial, sans-serif; padding: 18px; } h2 { margin: 24px 0 10px; } table { border-collapse: collapse; width: 100%; margin-bottom: 18px; } th, td { border: 1px solid #cfd6df; padding: 8px 10px; text-align: left; } th { background: #eef3f8; font-weight: 700; }</style>'
        '</head><body>'
        + '<h2>Image Summary</h2>' + table(["Field", "Value"], summary_rows)
        + '<h2>Image</h2>' + table(["Filename", "Expected QR", "Detected QR", "Elapsed (s)", "Error"], image_rows)
        + '<h2>Detected QR Codes</h2>' + table(["Filename", "Expected QR", "Detected QR", "IMEI", "Serial"], qr_rows, text_columns={3,4})
        + '</body></html>'
    )


def ensure_image_exports(scan_row: dict, image: dict) -> None:
    csv_path, xls_path = _image_export_paths(scan_row["id"], image)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["filename", "expected_qr", "detected_qr", "imei", "serial"])
        rows = _decoded_rows(image)
        detected = _real_detected_qr_count(image)
        if not rows:
            writer.writerow([image.get("filename", ""), image.get("expected_qr", 0), detected, "", ""])
        else:
            for row in rows:
                writer.writerow([image.get("filename", ""), image.get("expected_qr", 0), detected, row.get("imei", ""), row.get("serial", "")])
    xls_path.write_text(_image_excel_table_html(scan_row, image), encoding='utf-8')
    mirror_exports_to_configured_folder([csv_path, xls_path])


def image_exports(scan_row: dict, image: dict) -> dict:
    ensure_image_exports(scan_row, image)
    csv_path, xls_path = _image_export_paths(scan_row["id"], image)
    return {
        "qr_csv_url": f"/api/scans/{scan_row['id']}/images/{image['id']}/exports/qr-csv" if csv_path.exists() else None,
        "excel_table_url": f"/api/scans/{scan_row['id']}/images/{image['id']}/exports/excel-table" if xls_path.exists() else None,
        "qr_csv_name": csv_path.name if csv_path.exists() else None,
        "excel_table_name": xls_path.name if xls_path.exists() else None,
    }


def _excel_table_html(payload: dict) -> str:
    scan = serialize_scan(payload["scan"])
    images = payload["images"]
    real_total_qr = _scan_real_total_qr(images)
    localized_total_qr = _scan_localized_total_qr(images)
    summary_rows = [
        ("Scan", scan.get("source_label") or "Scan"),
        ("Profile", scan.get("profile") or "fast"),
        ("Status", scan.get("status") or "-"),
        ("Images", str(scan.get("total_images") or 0)),
        ("Expected QR", str(scan.get("total_expected") or 0)),
        ("Detected QR", str(real_total_qr)),
        ("Localized QR", str(localized_total_qr)),
        ("Created", scan.get("created_at") or "-"),
    ]
    image_rows = []
    qr_rows = []
    for image in images:
        image_rows.append((
            image.get("filename", ""),
            str(image.get("expected_qr", 0)),
            str(_image_counts(image)[0]),
            f"{float(image.get('elapsed', 0) or 0):.1f}",
            image.get("error", "") or "",
        ))
        seen = set()
        for patch in image.get("patches", []):
            imei = str(patch.get("imei", "") or "").strip()
            serial = str(patch.get("serial", "") or "").strip()
            pair = (imei, serial)
            if not imei and not serial:
                continue
            if pair in seen:
                continue
            seen.add(pair)
            qr_rows.append((image.get("filename", ""), str(image.get("expected_qr", 0)), str(_image_counts(image)[0]), imei, serial))

    def table(headers, rows, text_columns=None):
        text_columns = set(text_columns or [])
        head = ''.join(f'<th>{html.escape(h)}</th>' for h in headers)
        body_rows = []
        for row in rows:
            cells = []
            for idx, cell in enumerate(row):
                value = str(cell)
                if idx in text_columns and value:
                    safe = html.escape(value)
                    cells.append(f'<td style="white-space:pre;">&#39;{safe}</td>')
                else:
                    cells.append(f'<td>{html.escape(value)}</td>')
            body_rows.append('<tr>' + ''.join(cells) + '</tr>')
        body = ''.join(body_rows) or f"<tr><td colspan='{len(headers)}'>No rows</td></tr>"
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    return (
        '<!doctype html><html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel" xmlns="http://www.w3.org/TR/REC-html40">'
        '<head><meta charset="utf-8"><meta name="ProgId" content="Excel.Sheet"><meta name="Generator" content="QR Desk">'
        '<style>body { font-family: Segoe UI, Arial, sans-serif; padding: 18px; } h2 { margin: 24px 0 10px; } table { border-collapse: collapse; width: 100%; margin-bottom: 18px; } th, td { border: 1px solid #cfd6df; padding: 8px 10px; text-align: left; } th { background: #eef3f8; font-weight: 700; }</style>'
        '</head><body>'
        + '<h2>Scan Summary</h2>' + table(["Field", "Value"], summary_rows)
        + '<h2>Images</h2>' + table(["Filename", "Expected QR", "Detected QR", "Elapsed (s)", "Error"], image_rows)
        + '<h2>Detected QR Codes</h2>' + table(["Filename", "Expected QR", "Detected QR", "IMEI", "Serial"], qr_rows, text_columns={3,4})
        + '</body></html>'
    )


def ensure_scan_exports(payload: dict) -> None:
    scan = payload["scan"]
    csv_path, json_path, xls_path = db.scan_export_paths(scan["id"], scan_label=scan.get("source_label"), image_filenames=[img.get("filename", "") for img in payload["images"]])
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    expected_header = "filename,expected_qr,detected_qr,imei,serial"
    rewrite_csv = True

    if csv_path.exists():
        try:
            first_line = csv_path.read_text(encoding="utf-8").splitlines()[0].strip()
            rewrite_csv = first_line != expected_header
        except Exception:
            rewrite_csv = True

    if rewrite_csv:
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "filename",
                    "expected_qr",
                    "detected_qr",
                    "imei",
                    "serial",
                ]
            )
            for image in payload["images"]:
                patches = image["patches"]
                unique_pairs: list[tuple[str, str]] = []
                seen_pairs: set[tuple[str, str]] = set()
                for patch in patches:
                    pair = (
                        str(patch.get("imei", "")).strip(),
                        str(patch.get("serial", "")).strip(),
                    )
                    if not pair[0] and not pair[1]:
                        continue
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    unique_pairs.append(pair)

                if not patches:
                    writer.writerow(
                        [image["filename"], image["expected_qr"], image["qr_count"], "", ""]
                    )
                    continue

                if not unique_pairs:
                    writer.writerow(
                        [
                            image["filename"],
                            image["expected_qr"],
                            _image_counts(image)[0],
                            "",
                            "",
                        ]
                    )
                    continue

                for imei, serial in unique_pairs:
                    writer.writerow(
                        [
                            image["filename"],
                            image["expected_qr"],
                            _image_counts(image)[0],
                            imei,
                            serial,
                        ]
                    )

    json_path.write_text(
        json.dumps(
            {
                "scan": serialize_scan(scan),
                "images": payload["images"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    xls_path.write_text(_excel_table_html(payload), encoding="utf-8")
    mirror_exports_to_configured_folder([csv_path, json_path, xls_path])


def scan_exports(scan_id: str) -> dict:
    payload = db.get_scan_detail(scan_id)
    scan = payload["scan"] if payload else None
    images = payload["images"] if payload else []
    qr_csv, summary_json, excel_table = db.scan_export_paths(scan_id, scan_label=(scan.get("source_label") if scan else None), image_filenames=[img.get("filename", "") for img in images])
    return {
        "qr_csv_url": f"/api/scans/{scan_id}/exports/qr-csv" if qr_csv.exists() else None,
        "excel_table_url": f"/api/scans/{scan_id}/exports/excel-table" if excel_table.exists() else None,
        "qr_csv_name": qr_csv.name if qr_csv.exists() else None,
        "excel_table_name": excel_table.name if excel_table.exists() else None,
        "database_path": db.DB_PATH.relative_to(db.ROOT_DIR).as_posix(),
    }


def serialize_scan_detail(payload: dict) -> dict:
    ensure_scan_exports(payload)
    scan = serialize_scan(payload["scan"])
    images = []
    for image in payload["images"]:
        images.append(
            {
                "id": image["id"],
                "filename": image["filename"],
                "expected_qr": image["expected_qr"],
                "qr_count": _image_counts(image)[0],
                "decoded_count": _image_counts(image)[0],
                "localized_count": _image_counts(image)[1],
                "elapsed": image["elapsed"],
                "error": image["error"],
                "original_url": media_url(image["original_rel_path"]),
                "annotated_url": media_url(image["annotated_rel_path"]),
                "patches": image["patches"],
                "image_identifier": _image_identifier(image["id"]),
                "decoded_rows": _decoded_rows(image),
                "exports": image_exports(payload["scan"], image),
            }
        )
    scan["total_qr"] = _scan_real_total_qr(images)
    scan["localized_total_qr"] = _scan_localized_total_qr(images)
    scan["total_elapsed"] = float(sum(float(image.get("elapsed", 0.0) or 0.0) for image in images))
    return {
        "scan": scan,
        "images": images,
        "exports": scan_exports(scan["id"]),
    }


def sample_images() -> list[str]:
    images_dir = db.ROOT_DIR / "images"
    if not images_dir.exists():
        return []
    return sorted(
        p.name for p in images_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )


@app.on_event("startup")
def on_startup() -> None:
    db.init_database()
    db.ensure_default_user()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    html = (BASE_DIR / "qrdesk_index.html").read_text(encoding="utf-8")
    boot_data = {
        "sampleImages": sample_images(),
        "authUser": current_session_user(request),
        "settings": serialize_app_settings(),
        "appVersion": APP_VERSION,
    }
    css_version = asset_version(BASE_DIR / "qrdesk_app.css")
    js_version = asset_version(BASE_DIR / "qrdesk_app.js")
    html = html.replace(
        "__APP_BOOT_DATA__",
        JSONResponse(content=boot_data).body.decode("utf-8"),
    )
    html = html.replace("/assets/app.css", f"/assets/app.css?v={css_version}")
    html = html.replace("/assets/app.js", f"/assets/app.js?v={js_version}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/history", response_class=HTMLResponse)
@app.get("/history/", response_class=HTMLResponse)
@app.get("/History", response_class=HTMLResponse)
@app.get("/saved-runs", response_class=HTMLResponse)
@app.get("/saved-runs/", response_class=HTMLResponse)
@app.get("/runs", response_class=HTMLResponse)
@app.get("/runs/", response_class=HTMLResponse)
@app.get("/scans", response_class=HTMLResponse)
@app.get("/scans/", response_class=HTMLResponse)
async def history(request: Request) -> HTMLResponse:
    return await index(request)


@app.get("/assets/app.css")
async def app_css() -> FileResponse:
    return FileResponse(BASE_DIR / "qrdesk_app.css", headers={"Cache-Control": "no-store"})


@app.get("/assets/app.js")
async def app_js() -> FileResponse:
    return FileResponse(BASE_DIR / "qrdesk_app.js", headers={"Cache-Control": "no-store"})


@app.get("/media/{rel_path:path}")
async def media(rel_path: str) -> FileResponse:
    target = (db.APP_DATA_DIR / rel_path).resolve()
    try:
        target.relative_to(db.APP_DATA_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Invalid media path") from exc
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Media not found")
    return FileResponse(target)


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "database": str(db.DB_PATH),
        "sample_images": len(sample_images()),
    }


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    if current_session_user(request):
        return RedirectResponse(url="/", status_code=303)
    return HTMLResponse(LOGIN_PAGE, headers={"Cache-Control": "no-store"})


@app.post("/api/auth/login")
async def login(payload: LoginRequest, request: Request) -> Response:
    user = db.authenticate_user(payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    session_user = {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
    }
    token = secrets.token_urlsafe(32)
    ACTIVE_SESSIONS[token] = session_user
    response = JSONResponse({"ok": True, "user": session_user})
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax")
    return response


@app.post("/api/auth/logout")
async def logout(request: Request) -> Response:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        ACTIVE_SESSIONS.pop(token, None)
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/api/auth/me")
async def auth_me(request: Request) -> dict:
    user = current_session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"user": user}


@app.post('/api/admin/password')
async def change_admin_password(payload: ChangePasswordRequest, request: Request) -> dict:
    session_user = current_session_user(request)
    if not session_user:
        raise HTTPException(status_code=401, detail='Authentication required')
    if session_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail='Admin access required')

    username = str(session_user.get('username', '')).strip()
    user_row = db.get_user_by_username(username)
    if user_row is None or not db.verify_password(payload.current_password, str(user_row['password_hash'])):
        raise HTTPException(status_code=400, detail='Current password is incorrect')
    if db.verify_password(payload.new_password, str(user_row['password_hash'])):
        raise HTTPException(status_code=400, detail='Choose a new password different from the current one')

    db.update_user_password(username, payload.new_password)
    invalidate_user_sessions(username, keep_token=request.cookies.get(SESSION_COOKIE))
    return {'ok': True}

@app.post('/api/admin/shutdown')
async def shutdown_app(request: Request) -> Response:
    session_user = current_session_user(request)
    if not session_user:
        raise HTTPException(status_code=401, detail='Authentication required')
    if session_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail='Admin access required')

    ACTIVE_SESSIONS.clear()
    threading.Thread(target=shutdown_process, daemon=True).start()
    response = JSONResponse({'ok': True, 'detail': 'QR Desk is shutting down'})
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get('/api/admin/settings')
async def get_admin_settings(request: Request) -> dict:
    session_user = current_session_user(request)
    if not session_user:
        raise HTTPException(status_code=401, detail='Authentication required')
    if session_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail='Admin access required')
    return {'settings': serialize_app_settings()}


@app.put('/api/admin/settings')
async def update_admin_settings(payload: AdminSettingsRequest, request: Request) -> dict:
    session_user = current_session_user(request)
    if not session_user:
        raise HTTPException(status_code=401, detail='Authentication required')
    if session_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail='Admin access required')

    export_folder = payload.export_folder.strip()
    if export_folder:
        try:
            target = Path(export_folder).expanduser()
            if not target.is_absolute():
                target = (db.ROOT_DIR / target).resolve()
            target.mkdir(parents=True, exist_ok=True)
            export_folder = str(target)
        except Exception as exc:
            raise HTTPException(status_code=400, detail='Export folder is not writable') from exc

    settings = db.update_app_settings({
        'default_profile': 'fast',
        'default_workers': str(payload.default_workers),
        'export_folder': export_folder,
    })
    return {'ok': True, 'settings': serialize_app_settings(settings)}

@app.post('/api/admin/open-export-folder')
async def open_export_folder(request: Request) -> dict:
    session_user = current_session_user(request)
    if not session_user:
        raise HTTPException(status_code=401, detail='Authentication required')
    if session_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail='Admin access required')
    target = export_folder_path()
    try:
        open_folder_in_file_manager(target)
    except Exception as exc:
        raise HTTPException(status_code=500, detail='Unable to open export folder') from exc
    return {'ok': True, 'path': str(target)}
@app.get("/api/meta")
async def meta() -> dict:
    return {
        "profiles": ["fast"],
        "sample_images": sample_images(),
        "max_workers": 4,
        "version": APP_VERSION,
    }


@app.post("/api/upload-sessions")
async def create_upload_session() -> dict:
    session = db.create_upload_session()
    return {
        "session_id": session["id"],
        "upload_dir": session["rel_dir"],
    }


@app.put("/api/upload-sessions/{session_id}/files/{filename}")
async def upload_file(session_id: str, filename: str, request: Request) -> dict:
    session = db.get_upload_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Upload session not found")

    safe_name = sanitize_filename(filename)
    if Path(safe_name).suffix.lower() not in IMAGE_EXTS:
        raise HTTPException(status_code=400, detail="Unsupported image type")

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty upload body")

    target_path = db.APP_DATA_DIR / session["rel_dir"] / safe_name
    target_path.write_bytes(body)
    return {
        "ok": True,
        "filename": safe_name,
        "size": len(body),
    }


@app.post("/api/scans")
async def create_scan(payload: CreateScanRequest) -> dict:
    if payload.source_type == "upload":
        if not payload.session_id:
            raise HTTPException(status_code=400, detail="session_id is required for upload scans")
        session = db.get_upload_session(payload.session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Upload session not found")
        source_ref = payload.session_id
        source_label = payload.label or session["label"]
        if not payload.label:
            upload_dir = db.APP_DATA_DIR / session["rel_dir"]
            uploaded_files = sorted(
                p for p in upload_dir.iterdir()
                if p.is_file() and p.suffix.lower() in IMAGE_EXTS
            ) if upload_dir.exists() else []
            if len(uploaded_files) == 1:
                source_label = uploaded_files[0].name
            elif uploaded_files:
                source_label = f"Uploaded batch {len(uploaded_files)} images"
    else:
        source_ref = "images"
        if payload.label:
            source_label = payload.label
        elif len(payload.sample_files) == 1:
            source_label = sanitize_filename(payload.sample_files[0])
        else:
            source_label = "Reference Image Set"

    scan = db.create_scan(
        source_type=payload.source_type,
        source_label=source_label,
        profile="fast",
        deep_timeout=payload.deep_timeout,
        workers=payload.workers,
    )
    scan_manager.submit_scan(
        scan_id=scan["id"],
        source_type=payload.source_type,
        source_ref=source_ref,
        profile="fast",
        deep_timeout=payload.deep_timeout,
        workers=payload.workers,
        sample_files=payload.sample_files,
    )
    return {"scan_id": scan["id"], "status": "queued"}


@app.get("/api/scans")
async def list_scans(limit: int = 20) -> dict:
    items = []
    for item in db.list_scans(limit=limit):
        scan = serialize_scan(item)
        payload = db.get_scan_detail(item["id"])
        if payload:
            images = payload["images"]
            scan["source_label"] = resolve_scan_label(item)
            scan["total_qr"] = _scan_real_total_qr(images)
            scan["localized_total_qr"] = _scan_localized_total_qr(images)
            scan["total_elapsed"] = float(sum(float(img.get("elapsed", 0.0) or 0.0) for img in images))
            if images:
                scan["total_expected"] = sum(int(img.get("expected_qr", 0) or 0) for img in images)
                scan["processed_images"] = len(images)
                scan["total_images"] = max(int(item.get("total_images") or 0), len(images))
        items.append(scan)
    return {"items": items}


@app.get("/api/scans/{scan_id}")
async def get_scan(scan_id: str) -> dict:
    payload = db.get_scan_detail(scan_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    return serialize_scan_detail(payload)


@app.get("/api/scans/{scan_id}/summary")
async def get_scan_summary(scan_id: str) -> dict:
    scan = db.get_scan(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    return serialize_scan(scan)


@app.post("/api/scans/{scan_id}/cancel")
async def cancel_scan(scan_id: str) -> dict:
    scan = db.get_scan(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if scan.get("status") not in {"running", "queued"}:
        return {"ok": True, "scan_id": scan_id, "status": scan.get("status")}

    scan_manager.cancel_scan(scan_id)
    fields = {
        "status": "cancelled",
        "error": "Scan stopped by user",
    }
    if scan.get("status") == "queued":
        fields["finished_at"] = db.utcnow_iso()
    db.update_scan(scan_id, **fields)
    return {"ok": True, "scan_id": scan_id, "status": "cancelled"}


@app.post("/api/scans/{scan_id}/stop")
async def stop_scan(scan_id: str) -> dict:
    return await cancel_scan(scan_id)


@app.get("/api/scans/{scan_id}/cancel")
async def cancel_scan_get(scan_id: str) -> dict:
    return await cancel_scan(scan_id)


@app.delete("/api/scans/{scan_id}")
async def delete_scan(scan_id: str) -> dict:
    scan = db.get_scan(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if scan.get("status") in {"running", "queued"}:
        raise HTTPException(status_code=409, detail="Stop the scan before deleting it")
    db.delete_scan(scan_id)
    shutil.rmtree(db.SCANS_DIR / scan_id, ignore_errors=True)
    return {"ok": True, "scan_id": scan_id}


@app.post("/api/scans/{scan_id}/delete")
async def delete_scan_post(scan_id: str) -> dict:
    return await delete_scan(scan_id)


@app.get("/api/scans/{scan_id}/exports/qr-csv")
async def download_scan_qr_csv(scan_id: str) -> FileResponse:
    payload = db.get_scan_detail(scan_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    ensure_scan_exports(payload)
    csv_path, _, _ = db.scan_export_paths(scan_id, scan_label=payload["scan"].get("source_label"), image_filenames=[img.get("filename", "") for img in payload["images"]])
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="QR CSV export not found")
    return FileResponse(csv_path, filename=csv_path.name)


@app.get("/api/scans/{scan_id}/exports/summary-json")
async def download_scan_summary_json(scan_id: str) -> FileResponse:
    payload = db.get_scan_detail(scan_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    ensure_scan_exports(payload)
    _, json_path, _ = db.scan_export_paths(scan_id, scan_label=payload["scan"].get("source_label"), image_filenames=[img.get("filename", "") for img in payload["images"]])
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="Summary export not found")
    return FileResponse(json_path, filename=json_path.name)


@app.get("/api/scans/{scan_id}/exports/excel-table")
async def download_scan_excel_table(scan_id: str) -> FileResponse:
    payload = db.get_scan_detail(scan_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    ensure_scan_exports(payload)
    _, _, xls_path = db.scan_export_paths(scan_id, scan_label=payload["scan"].get("source_label"), image_filenames=[img.get("filename", "") for img in payload["images"]])
    if not xls_path.exists():
        raise HTTPException(status_code=404, detail="Excel export not found")
    return FileResponse(xls_path, filename=xls_path.name, media_type="application/vnd.ms-excel")


@app.get("/api/scans/{scan_id}/images/{image_id}/exports/qr-csv")
async def download_image_qr_csv(scan_id: str, image_id: str) -> FileResponse:
    payload = db.get_scan_detail(scan_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    image = next((img for img in payload["images"] if img["id"] == image_id), None)
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")
    ensure_image_exports(payload["scan"], image)
    csv_path, _ = _image_export_paths(scan_id, image)
    return FileResponse(csv_path, filename=csv_path.name)


@app.get("/api/scans/{scan_id}/images/{image_id}/exports/excel-table")
async def download_image_excel_table(scan_id: str, image_id: str) -> FileResponse:
    payload = db.get_scan_detail(scan_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    image = next((img for img in payload["images"] if img["id"] == image_id), None)
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")
    ensure_image_exports(payload["scan"], image)
    _, xls_path = _image_export_paths(scan_id, image)
    return FileResponse(xls_path, filename=xls_path.name, media_type="application/vnd.ms-excel")


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.put("/api/upload-sessions/{session_id}/files")
async def upload_file_without_name(_: str) -> Response:
    raise HTTPException(status_code=405, detail="Upload to /api/upload-sessions/{session_id}/files/{filename}")

@app.get("/{page_path:path}", response_class=HTMLResponse)
async def frontend_page_fallback(request: Request, page_path: str) -> HTMLResponse:
    """Serve QR Desk for clean app-page URLs, but keep real files/API as 404."""
    normalized = (page_path or "").strip("/").lower()
    if (
        normalized
        and not normalized.startswith("api/")
        and not normalized.startswith("assets/")
        and not normalized.startswith("media/")
        and "." not in normalized
    ):
        return await index(request)
    raise HTTPException(status_code=404, detail="Not Found")