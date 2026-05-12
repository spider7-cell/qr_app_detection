from __future__ import annotations

import csv
import html
import json
import shutil
import threading
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any
from uuid import uuid4

import cv2

import qr_static
import qrdesk_db as db


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}


def process_one_image_worker(args: tuple[str, str, int | None, str]) -> dict[str, Any]:
    path_text, profile, deep_timeout, scan_id = args
    path = Path(path_text)
    report = qr_static.process_image_with_profile(
        str(path),
        profile=profile,
        deep_timeout=deep_timeout,
    )

    annotated_rel_path = None
    try:
        img = cv2.imread(str(path))
        if img is not None:
            annotated_dir = db.SCANS_DIR / scan_id / "annotated"
            annotated_dir.mkdir(parents=True, exist_ok=True)
            annotated_path = annotated_dir / path.name
            qr_static.save_annotated_image(img, report.patches, str(annotated_path), filename=path.name)
            annotated_rel_path = annotated_path.relative_to(db.APP_DATA_DIR).as_posix()
    except Exception:
        annotated_rel_path = None

    expected_qr = qr_static.guess_expected_qr(path.name)
    return {
        "filename": path.name,
        "original_rel_path": path.relative_to(db.APP_DATA_DIR).as_posix(),
        "annotated_rel_path": annotated_rel_path,
        "expected_qr": expected_qr,
        "qr_count": report.qr_count,
        "elapsed": report.elapsed,
        "error": report.error,
        "patches": [serialize_patch_for_worker(patch) for patch in report.patches],
    }


def serialize_patch_for_worker(patch: Any) -> dict[str, Any]:
    points = getattr(patch, "points", None) or []
    return {
        "raw": patch.data.raw,
        "imei": patch.data.imei,
        "serial": patch.data.serial,
        "source": patch.source,
        "stage": patch.stage,
        "confidence": patch.confidence,
        "bbox": list(patch.bbox),
        "points": [list(point) for point in points],
    }


def sanitize_filename(name: str) -> str:
    candidate = Path(name).name.strip().replace("\\", "_").replace("/", "_")
    if not candidate:
        candidate = f"image_{uuid4().hex[:8]}.png"
    safe = "".join(ch if ch.isalnum() or ch in {" ", ".", "-", "_"} else "_" for ch in candidate)
    return safe[:180]


class ScanManager:
    def __init__(self) -> None:
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="scan-job")
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def submit_scan(
        self,
        scan_id: str,
        source_type: str,
        source_ref: str,
        profile: str,
        deep_timeout: int | None,
        workers: int,
        sample_files: list[str] | None = None,
    ) -> None:
        cancel_event = threading.Event()
        future = self.executor.submit(
            self._run_scan_job,
            scan_id,
            source_type,
            source_ref,
            profile,
            deep_timeout,
            workers,
            sample_files or [],
            cancel_event,
        )
        with self._lock:
            self._jobs[scan_id] = {"future": future, "cancel_event": cancel_event}

    def cancel_scan(self, scan_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(scan_id)
        if not job:
            return False
        cancel_event = job.get("cancel_event")
        future = job.get("future")
        if cancel_event:
            cancel_event.set()
        if future and not future.running():
            future.cancel()
        return True

    def _drop_job(self, scan_id: str) -> None:
        with self._lock:
            self._jobs.pop(scan_id, None)

    def _stage_input_files(
        self,
        scan_id: str,
        source_type: str,
        source_ref: str,
        sample_files: list[str],
    ) -> list[Path]:
        scan_root = db.SCANS_DIR / scan_id
        originals_dir = scan_root / "originals"
        originals_dir.mkdir(parents=True, exist_ok=True)

        if source_type == "upload":
            upload_session = db.get_upload_session(source_ref)
            if upload_session is None:
                raise FileNotFoundError("Upload session not found")
            source_dir = db.APP_DATA_DIR / upload_session["rel_dir"]
            candidate_files = sorted(
                p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS
            )
        else:
            source_dir = db.ROOT_DIR / "images"
            selected = {sanitize_filename(name) for name in sample_files if name}
            raw_files = sorted(
                p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS
            )
            candidate_files = [
                p for p in raw_files if not selected or sanitize_filename(p.name) in selected
            ]

        staged_files: list[Path] = []
        seen_names: set[str] = set()
        for source_path in candidate_files:
            safe_name = sanitize_filename(source_path.name)
            if safe_name in seen_names:
                stem = Path(safe_name).stem
                suffix = Path(safe_name).suffix
                safe_name = f"{stem}_{uuid4().hex[:6]}{suffix}"
            seen_names.add(safe_name)
            target_path = originals_dir / safe_name
            shutil.copy2(source_path, target_path)
            staged_files.append(target_path)

        return staged_files

    @staticmethod
    def _serialize_patch(patch: qr_static.QRPatch) -> dict[str, Any]:
        return {
            "raw": patch.data.raw,
            "imei": patch.data.imei,
            "serial": patch.data.serial,
            "source": patch.source,
            "stage": patch.stage,
            "confidence": patch.confidence,
            "bbox": list(patch.bbox),
        }

    def _write_scan_exports(
        self,
        scan_id: str,
        source_label: str,
        profile: str,
        deep_timeout: int | None,
        workers: int,
        image_results: list[dict[str, Any]],
    ) -> None:
        csv_path, json_path, xls_path = db.scan_export_paths(
            scan_id,
            scan_label=source_label,
            image_filenames=[item.get("filename", "") for item in image_results],
        )
        csv_path.parent.mkdir(parents=True, exist_ok=True)

        total_expected = sum(int(item.get("expected_qr", 0) or 0) for item in image_results)
        total_qr = sum(int(item.get("qr_count", 0) or 0) for item in image_results)

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
            for image in image_results:
                patches = image.get("patches", [])
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
                        [
                            image["filename"],
                            image["expected_qr"],
                            image["qr_count"],
                            "",
                            "",
                        ]
                    )
                    continue

                if not unique_pairs:
                    writer.writerow(
                        [
                            image["filename"],
                            image["expected_qr"],
                            image["qr_count"],
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
                            image["qr_count"],
                            imei,
                            serial,
                        ]
                    )

        json_payload = {
            "scan_id": scan_id,
            "profile": profile,
            "deep_timeout": deep_timeout,
            "workers": workers,
            "total_images": len(image_results),
            "total_expected": total_expected,
            "total_qr": total_qr,
            "images": image_results,
        }
        json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        def clean_cell(value: Any) -> str:
            return html.escape(str(value or ""), quote=True)

        xls_rows: list[str] = [
            "<html><head><meta charset=\"utf-8\"></head><body>",
            "<h1>Scan Summary</h1>",
            "<table border=\"1\" cellspacing=\"0\" cellpadding=\"5\">",
            "<tr><th>Field</th><th>Value</th></tr>",
            f"<tr><td>Scan</td><td>{clean_cell(source_label)}</td></tr>",
            f"<tr><td>Profile</td><td>{clean_cell(profile)}</td></tr>",
            f"<tr><td>Images</td><td>{len(image_results)}</td></tr>",
            f"<tr><td>Expected QR</td><td>{total_expected}</td></tr>",
            f"<tr><td>Detected QR</td><td>{total_qr}</td></tr>",
            "</table>",
            "<h1>Images</h1>",
            "<table border=\"1\" cellspacing=\"0\" cellpadding=\"5\">",
            "<tr><th>Filename</th><th>Expected QR</th><th>Detected QR</th><th>Elapsed (s)</th><th>Error</th></tr>",
        ]
        for image in image_results:
            elapsed = round(float(image.get("elapsed", 0) or 0), 1)
            xls_rows.append(
                "<tr>"
                f"<td>{clean_cell(image.get('filename'))}</td>"
                f"<td>{clean_cell(image.get('expected_qr'))}</td>"
                f"<td>{clean_cell(image.get('qr_count'))}</td>"
                f"<td>{clean_cell(elapsed)}</td>"
                f"<td>{clean_cell(image.get('error'))}</td>"
                "</tr>"
            )
        xls_rows.extend(
            [
                "</table>",
                "<h1>Detected QR Codes</h1>",
                "<table border=\"1\" cellspacing=\"0\" cellpadding=\"5\">",
                "<tr><th>Filename</th><th>Expected QR</th><th>Detected QR</th><th>IMEI</th><th>Serial</th></tr>",
            ]
        )
        for image in image_results:
            unique_pairs: list[tuple[str, str]] = []
            seen_pairs: set[tuple[str, str]] = set()
            for patch in image.get("patches", []):
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
            if not unique_pairs:
                xls_rows.append(
                    "<tr>"
                    f"<td>{clean_cell(image.get('filename'))}</td>"
                    f"<td>{clean_cell(image.get('expected_qr'))}</td>"
                    f"<td>{clean_cell(image.get('qr_count'))}</td>"
                    "<td></td><td></td>"
                    "</tr>"
                )
                continue
            for imei, serial in unique_pairs:
                xls_rows.append(
                    "<tr>"
                    f"<td>{clean_cell(image.get('filename'))}</td>"
                    f"<td>{clean_cell(image.get('expected_qr'))}</td>"
                    f"<td>{clean_cell(image.get('qr_count'))}</td>"
                    f"<td style=\"mso-number-format:'\\@';\">{clean_cell(imei)}</td>"
                    f"<td style=\"mso-number-format:'\\@';\">{clean_cell(serial)}</td>"
                    "</tr>"
                )
        xls_rows.extend(["</table>", "</body></html>"])
        xls_path.write_text("\n".join(xls_rows), encoding="utf-8")

    def _process_one_image(
        self,
        path: Path,
        profile: str,
        deep_timeout: int | None,
        scan_id: str,
    ) -> dict[str, Any]:
        return process_one_image_worker((str(path), profile, deep_timeout, scan_id))

    def _run_scan_job(
        self,
        scan_id: str,
        source_type: str,
        source_ref: str,
        profile: str,
        deep_timeout: int | None,
        workers: int,
        sample_files: list[str],
        cancel_event: threading.Event,
    ) -> None:
        def cancelled() -> bool:
            return cancel_event.is_set()

        try:
            if cancelled():
                db.update_scan(
                    scan_id,
                    status="cancelled",
                    finished_at=db.utcnow_iso(),
                    error="Scan stopped by user",
                )
                return

            staged_files = self._stage_input_files(scan_id, source_type, source_ref, sample_files)
            total_expected = sum(qr_static.guess_expected_qr(path.name) for path in staged_files)

            if cancelled():
                db.update_scan(
                    scan_id,
                    status="cancelled",
                    finished_at=db.utcnow_iso(),
                    total_images=len(staged_files),
                    total_expected=total_expected,
                    error="Scan stopped by user",
                )
                return

            db.update_scan(
                scan_id,
                status="running",
                started_at=db.utcnow_iso(),
                total_images=len(staged_files),
                total_expected=total_expected,
                processed_images=0,
                total_qr=0,
                error="",
            )

            if not staged_files:
                db.update_scan(
                    scan_id,
                    status="failed",
                    finished_at=db.utcnow_iso(),
                    error="No valid image files were staged for scanning",
                )
                return

            processed = 0
            total_qr = 0
            completed_results: list[dict[str, Any]] = []
            max_workers = max(1, min(int(workers or 1), len(staged_files), 4))
            # Use process isolation when multiple images run together. The QR engine
            # adjusts profile budgets per image, so threads can cross-contaminate
            # timing/config values and produce unstable reference-set results.
            pool_cls = ProcessPoolExecutor if max_workers > 1 else ThreadPoolExecutor
            pool_kwargs = {"max_workers": max_workers}
            if pool_cls is ThreadPoolExecutor:
                pool_kwargs["thread_name_prefix"] = "scan-image"
            pool = pool_cls(**pool_kwargs)
            pending_files = iter(staged_files)
            future_map: dict[Any, Path] = {}

            def submit_next() -> None:
                while len(future_map) < max_workers and not cancelled():
                    try:
                        path = next(pending_files)
                    except StopIteration:
                        break
                    future = pool.submit(process_one_image_worker, (str(path), profile, deep_timeout, scan_id))
                    future_map[future] = path

            try:
                submit_next()
                while future_map:
                    if cancelled():
                        break

                    done, _ = wait(list(future_map.keys()), timeout=0.25, return_when=FIRST_COMPLETED)
                    if not done:
                        continue

                    for future in done:
                        path = future_map.pop(future)
                        try:
                            result = future.result()
                        except Exception as exc:
                            result = {
                                "filename": path.name,
                                "original_rel_path": path.relative_to(db.APP_DATA_DIR).as_posix(),
                                "annotated_rel_path": None,
                                "expected_qr": qr_static.guess_expected_qr(path.name),
                                "qr_count": 0,
                                "elapsed": 0.0,
                                "error": str(exc),
                                "patches": [],
                            }

                        image_id = db.insert_scan_image(
                            scan_id=scan_id,
                            filename=result["filename"],
                            original_rel_path=result["original_rel_path"],
                            annotated_rel_path=result["annotated_rel_path"],
                            expected_qr=result["expected_qr"],
                            qr_count=result["qr_count"],
                            elapsed=result["elapsed"],
                            error=result["error"],
                        )
                        db.replace_image_patches(image_id, result["patches"])

                        processed += 1
                        total_qr += int(result["qr_count"])
                        completed_results.append(result)
                        db.update_scan(
                            scan_id,
                            processed_images=processed,
                            total_qr=total_qr,
                        )

                        if cancelled():
                            break
                        submit_next()
            finally:
                pool.shutdown(wait=False, cancel_futures=True)

            if completed_results:
                self._write_scan_exports(
                    scan_id=scan_id,
                    source_label=db.get_scan(scan_id)["source_label"],
                    profile=profile,
                    deep_timeout=deep_timeout,
                    workers=workers,
                    image_results=sorted(completed_results, key=lambda item: item["filename"].lower()),
                )

            if cancelled():
                db.update_scan(
                    scan_id,
                    status="cancelled",
                    finished_at=db.utcnow_iso(),
                    processed_images=processed,
                    total_qr=total_qr,
                    error="Scan stopped by user",
                )
                return

            db.update_scan(
                scan_id,
                status="completed",
                finished_at=db.utcnow_iso(),
                processed_images=processed,
                total_qr=total_qr,
            )
        except Exception as exc:
            status = "cancelled" if cancelled() else "failed"
            message = "Scan stopped by user" if cancelled() else str(exc)
            db.update_scan(
                scan_id,
                status=status,
                finished_at=db.utcnow_iso(),
                error=message,
            )
        finally:
            self._drop_job(scan_id)


