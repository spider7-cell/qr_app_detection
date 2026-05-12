from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT_DIR = Path(os.environ.get("QRDESK_ROOT_DIR", Path(__file__).resolve().parent))
APP_DATA_DIR = ROOT_DIR / "app_data"
UPLOADS_DIR = APP_DATA_DIR / "uploads"
SCANS_DIR = APP_DATA_DIR / "scans"
DB_PATH = APP_DATA_DIR / "qr_app.db"

DEFAULT_SETTINGS = {
    "default_profile": "fast",
    "default_workers": "2",
    "export_folder": "",
}


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _folder_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _safe_slug(value: str, fallback: str = "scan") -> str:
    raw = Path(str(value or fallback)).stem.lower().strip()
    cleaned = []
    prev_sep = False
    for ch in raw:
        if ch.isalnum():
            cleaned.append(ch)
            prev_sep = False
        elif not prev_sep:
            cleaned.append("_")
            prev_sep = True
    slug = "".join(cleaned).strip("_")
    return (slug or fallback)[:42]


def readable_record_id(*parts: str, fallback: str = "scan") -> str:
    slug_parts = [_safe_slug(part, fallback="") for part in parts if str(part or "").strip()]
    slug = "_".join(part for part in slug_parts if part) or fallback
    token = secrets.token_hex(3)
    return f"{_folder_timestamp()}_{slug[:58]}_{token}"


def ensure_app_dirs() -> None:
    APP_DATA_DIR.mkdir(exist_ok=True)
    UPLOADS_DIR.mkdir(exist_ok=True)
    SCANS_DIR.mkdir(exist_ok=True)


def export_name_token(scan_id: str, scan_label: str | None = None, image_filenames: Iterable[str] | None = None) -> str:
    names = [str(name).strip() for name in (image_filenames or []) if str(name).strip()]
    if len(names) == 1:
        raw = Path(names[0]).stem
    elif scan_label:
        raw = str(scan_label).strip()
    else:
        raw = f"scan_{scan_id[:8]}"

    cleaned = []
    prev_us = False
    for ch in raw.lower():
        ok = ch.isalnum()
        if ok:
            cleaned.append(ch)
            prev_us = False
        elif not prev_us:
            cleaned.append('_')
            prev_us = True
    token = ''.join(cleaned).strip('_') or f"scan_{scan_id[:8]}"
    return token[:48]


def scan_export_paths(
    scan_id: str,
    scan_label: str | None = None,
    image_filenames: Iterable[str] | None = None,
) -> tuple[Path, Path, Path]:
    token = export_name_token(scan_id, scan_label=scan_label, image_filenames=image_filenames)
    export_dir = SCANS_DIR / scan_id / "exports"
    csv_path = export_dir / f"{token}_detected_qr_codes.csv"
    json_path = export_dir / f"{token}_scan_summary.json"
    xls_path = export_dir / f"{token}_tables.xls"
    return csv_path, json_path, xls_path


def connect() -> sqlite3.Connection:
    ensure_app_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        240_000,
    ).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not password or not stored_hash or "$" not in stored_hash:
        return False
    salt, _ = stored_hash.split("$", 1)
    check = _password_hash(password, salt)
    return hmac.compare_digest(check, stored_hash)


def init_database() -> None:
    ensure_app_dirs()
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS upload_sessions (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                label TEXT NOT NULL,
                rel_dir TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scans (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                source_type TEXT NOT NULL,
                source_label TEXT NOT NULL,
                profile TEXT NOT NULL,
                deep_timeout INTEGER,
                workers INTEGER NOT NULL,
                status TEXT NOT NULL,
                total_images INTEGER NOT NULL DEFAULT 0,
                processed_images INTEGER NOT NULL DEFAULT 0,
                total_qr INTEGER NOT NULL DEFAULT 0,
                total_expected INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS scan_images (
                id TEXT PRIMARY KEY,
                scan_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                original_rel_path TEXT NOT NULL,
                annotated_rel_path TEXT,
                expected_qr INTEGER NOT NULL DEFAULT 0,
                qr_count INTEGER NOT NULL DEFAULT 0,
                elapsed REAL NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (scan_id) REFERENCES scans(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS qr_patches (
                id TEXT PRIMARY KEY,
                image_id TEXT NOT NULL,
                raw TEXT NOT NULL,
                imei TEXT NOT NULL DEFAULT '',
                serial TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                stage TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                bbox_json TEXT NOT NULL DEFAULT '[0,0,0,0]',
                FOREIGN KEY (image_id) REFERENCES scan_images(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
    ensure_default_settings()


def ensure_default_settings() -> None:
    now = utcnow_iso()
    with connect() as conn:
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, now),
            )


def get_app_settings() -> dict[str, str]:
    ensure_default_settings()
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    settings = dict(DEFAULT_SETTINGS)
    settings.update({str(row["key"]): str(row["value"]) for row in rows})
    return settings


def update_app_settings(settings: dict[str, str]) -> dict[str, str]:
    allowed = set(DEFAULT_SETTINGS)
    now = utcnow_iso()
    with connect() as conn:
        for key, value in settings.items():
            if key not in allowed:
                continue
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, str(value), now),
            )
    return get_app_settings()


def create_upload_session(label: str = "Uploaded files") -> dict[str, str]:
    session_id = readable_record_id("upload", label, fallback="upload")
    rel_dir = f"uploads/{session_id}"
    (APP_DATA_DIR / rel_dir).mkdir(parents=True, exist_ok=True)
    row = {
        "id": session_id,
        "created_at": utcnow_iso(),
        "label": label,
        "rel_dir": rel_dir,
    }
    with connect() as conn:
        conn.execute(
            "INSERT INTO upload_sessions (id, created_at, label, rel_dir) VALUES (?, ?, ?, ?)",
            (row["id"], row["created_at"], row["label"], row["rel_dir"]),
        )
    return row


def get_upload_session(session_id: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM upload_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()


def create_scan(
    source_type: str,
    source_label: str,
    profile: str,
    deep_timeout: int | None,
    workers: int,
) -> dict[str, Any]:
    scan_id = readable_record_id(source_label, profile, fallback="scan")
    row = {
        "id": scan_id,
        "created_at": utcnow_iso(),
        "started_at": None,
        "finished_at": None,
        "source_type": source_type,
        "source_label": source_label,
        "profile": profile,
        "deep_timeout": deep_timeout,
        "workers": workers,
        "status": "queued",
        "total_images": 0,
        "processed_images": 0,
        "total_qr": 0,
        "total_expected": 0,
        "error": "",
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO scans (
                id, created_at, started_at, finished_at, source_type, source_label,
                profile, deep_timeout, workers, status, total_images, processed_images,
                total_qr, total_expected, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["created_at"],
                row["started_at"],
                row["finished_at"],
                row["source_type"],
                row["source_label"],
                row["profile"],
                row["deep_timeout"],
                row["workers"],
                row["status"],
                row["total_images"],
                row["processed_images"],
                row["total_qr"],
                row["total_expected"],
                row["error"],
            ),
        )
    return row


def update_scan(scan_id: str, **fields: Any) -> None:
    if not fields:
        return
    columns = ", ".join(f"{name} = ?" for name in fields)
    values = list(fields.values()) + [scan_id]
    with connect() as conn:
        conn.execute(f"UPDATE scans SET {columns} WHERE id = ?", values)


def insert_scan_image(
    scan_id: str,
    filename: str,
    original_rel_path: str,
    annotated_rel_path: str | None,
    expected_qr: int,
    qr_count: int,
    elapsed: float,
    error: str,
) -> str:
    image_id = uuid.uuid4().hex
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO scan_images (
                id, scan_id, filename, original_rel_path, annotated_rel_path,
                expected_qr, qr_count, elapsed, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                image_id,
                scan_id,
                filename,
                original_rel_path,
                annotated_rel_path,
                expected_qr,
                qr_count,
                elapsed,
                error,
                utcnow_iso(),
            ),
        )
    return image_id


def replace_image_patches(image_id: str, patches: Iterable[dict[str, Any]]) -> None:
    patch_rows = list(patches)
    with connect() as conn:
        conn.execute("DELETE FROM qr_patches WHERE image_id = ?", (image_id,))
        conn.executemany(
            """
            INSERT INTO qr_patches (
                id, image_id, raw, imei, serial, source, stage, confidence, bbox_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    uuid.uuid4().hex,
                    image_id,
                    str(item.get("raw", "")),
                    str(item.get("imei", "")),
                    str(item.get("serial", "")),
                    str(item.get("source", "")),
                    str(item.get("stage", "")),
                    float(item.get("confidence", 0.0) or 0.0),
                    json.dumps(item.get("bbox", [0, 0, 0, 0])),
                )
                for item in patch_rows
            ],
        )


def list_scans(limit: int = 20) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM scans
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_scan(scan_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
    return dict(row) if row else None


def get_scan_primary_filename(scan_id: str) -> str | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT filename
            FROM scan_images
            WHERE scan_id = ?
            ORDER BY created_at ASC, filename ASC
            LIMIT 1
            """,
            (scan_id,),
        ).fetchone()
    return str(row["filename"]) if row else None


def get_scan_detail(scan_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        scan_row = conn.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        if scan_row is None:
            return None

        image_rows = conn.execute(
            """
            SELECT *
            FROM scan_images
            WHERE scan_id = ?
            ORDER BY filename ASC, created_at ASC
            """,
            (scan_id,),
        ).fetchall()

        image_ids = [row["id"] for row in image_rows]
        patches_by_image: dict[str, list[dict[str, Any]]] = {image_id: [] for image_id in image_ids}

        if image_ids:
            placeholders = ", ".join("?" for _ in image_ids)
            patch_rows = conn.execute(
                f"""
                SELECT *
                FROM qr_patches
                WHERE image_id IN ({placeholders})
                ORDER BY confidence DESC, raw ASC
                """,
                image_ids,
            ).fetchall()
            for row in patch_rows:
                payload = dict(row)
                try:
                    payload["bbox"] = json.loads(payload.pop("bbox_json"))
                except Exception:
                    payload["bbox"] = [0, 0, 0, 0]
                patches_by_image[row["image_id"]].append(payload)

    images = []
    for row in image_rows:
        payload = dict(row)
        payload["patches"] = patches_by_image.get(row["id"], [])
        images.append(payload)

    return {
        "scan": dict(scan_row),
        "images": images,
    }


def delete_scan(scan_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM scans WHERE id = ?", (scan_id,))


def get_user_by_username(username: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE lower(username) = lower(?)",
            (username.strip(),),
        ).fetchone()


def create_user(username: str, password: str, role: str = "admin") -> dict[str, str]:
    username = username.strip()
    if not username:
        raise ValueError("username is required")
    if not password:
        raise ValueError("password is required")
    user_id = uuid.uuid4().hex
    row = {
        "id": user_id,
        "username": username,
        "password_hash": _password_hash(password),
        "role": role,
        "created_at": utcnow_iso(),
    }
    with connect() as conn:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (row["id"], row["username"], row["password_hash"], row["role"], row["created_at"]),
        )
    return row


def ensure_default_user() -> dict[str, str]:
    username = os.getenv("QRDESK_ADMIN_USERNAME", "admin").strip() or "admin"
    password = os.getenv("QRDESK_ADMIN_PASSWORD", "admin123")
    existing = get_user_by_username(username)
    if existing is not None:
        return dict(existing)
    return create_user(username, password, role="admin")


def authenticate_user(username: str, password: str) -> sqlite3.Row | None:
    row = get_user_by_username(username)
    if row is None:
        return None
    if not verify_password(password, str(row["password_hash"])):
        return None
    return row

def update_user_password(username: str, new_password: str) -> None:
    username = username.strip()
    if not username:
        raise ValueError('username is required')
    if not new_password:
        raise ValueError('password is required')
    with connect() as conn:
        conn.execute(
            'UPDATE users SET password_hash = ? WHERE lower(username) = lower(?)',
            (_password_hash(new_password), username),
        )