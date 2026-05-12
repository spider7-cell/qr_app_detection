#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QR DETECTOR â€” ONE MORE TRY (push back toward 30 QRs, but keep runtime sane)

What changed vs your last run (25 QRs / ~7 min):
âœ… Keeps the fast â€œgood imagesâ€ behavior (4qrsimg, 4qrs, 4 qrs, 4qrss stay strong)
âœ… Pushes HARD images (4qr_updu6, 4qr_down_du6, 6qrs1, edited_6qrs) with:
   1) Better layout ROIs: adds a SMALL "qr-corner" crop inside each cell (where the etched QR usually is)
   2) Stronger ROI variants: invert + blackhat + extra CLAHE + sharpen
   3) Quiet-zone padding (many etched QRs fail without margin)
   4) NEW: OpenCV detect() -> perspective warp -> decode (cheap, helps with glare/tilt)
âœ… Runtime control:
   - Deep stuff runs only if missing QRs
   - Deep grid is the LAST resort, and smaller than before

Tip: keep zxing-cpp installed. Itâ€™s a big accuracy boost on etched QRs.
"""

import os, re, glob, json, csv, time, math, argparse, warnings, threading, sys, subprocess
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import List, Tuple
from collections import defaultdict

warnings.filterwarnings("ignore")
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import cv2
import numpy as np
from pyzbar.pyzbar import decode as pyzbar_decode

# Restrict pyzbar to QR only (faster + cleaner)
try:
    from pyzbar.pyzbar import ZBarSymbol
    PYZBAR_QR_ONLY = [ZBarSymbol.QRCODE]
except Exception:
    PYZBAR_QR_ONLY = None

# Optional ZXing-cpp (recommended)
try:
    import zxingcpp  # pip install zxing-cpp
    ZXING_OK = True
except Exception:
    ZXING_OK = False

# -------------------------
# Config
# -------------------------
class Config:
    PROFILE = "fast"
    MIN_QR_LEN = 6
    BLACKLIST = {
        "CE","RX","TX","GPS","GSM","CHG","220","35V","KEEP","TOP","Q1","ASIC","Z7",
        "MADE IN CHINA","ANATEL","FCC ID"
    }

    PREPROCESS_TECHNIQUES = [
        ("clahe", lambda g: cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(g)),
        ("binary", lambda g: cv2.adaptiveThreshold(
            g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 3
        )),
    ]

    USE_WECHAT = True
    USE_ZXING = True  # if zxingcpp installed
    USE_FINDER = True
    USE_CONTOUR = True

    # Runtime controls
    TIMEOUT_PER_IMAGE = 42         # fast profile must stay responsive; deeper profiles add bounded rescue later
    SOFT_BUDGET_PER_IMAGE = 14      # after this, the base scan should stop spending time on weak returns
    CELL_TIMEOUT = 3

    BALANCED_DEEP_TIMEOUT = 35
    ACCURACY_DEEP_TIMEOUT = 75
    DEEP_RESCUE_MIN_EXPECTED = 4

    MAX_WORKERS = min(4, os.cpu_count() or 2)

    # Zoom caps
    MAX_ZOOMED_SIZE_FAST = 2400
    MAX_ZOOMED_SIZE_DEEP = 4200

    DEFAULT_EXPECTED = 4

    # Grid scanning parameters (FAST then DEEP)
    GRID_FAST = {
        1: {"size": 3, "zooms": [3, 4], "overlap": 0.30},
        2: {"size": 4, "zooms": [3,4,6], "overlap": 0.30},
        4: {"size": 5, "zooms": [3,4,6], "overlap": 0.30},
        6: {"size": 7, "zooms": [3,4,6], "overlap": 0.25},
        8: {"size": 8, "zooms": [3,4,6], "overlap": 0.20},
        10: {"size": 8, "zooms": [2,3,4], "overlap": 0.18},
    }
    # Deep grid smaller than before (ROI work should do the heavy lifting)
    GRID_DEEP = {
        1: {"size": 4, "zooms": [4, 6], "overlap": 0.35},
        2: {"size": 5, "zooms": [6,8], "overlap": 0.35},
        4: {"size": 6, "zooms": [6,8], "overlap": 0.33},
        6: {"size": 7, "zooms": [6,8], "overlap": 0.30},
        8: {"size": 8, "zooms": [6,8], "overlap": 0.25},
        10: {"size": 8, "zooms": [4,6], "overlap": 0.20},
    }

    FINDER_THRESHOLDS = [11, 15, 21]
    FINDER_MIN_AREA = 10
    FINDER_MAX_AREA = 2000
    FINDER_PADDING = 0.45
    FINDER_ZOOMS = [4, 6]

    CONTOUR_MIN_AREA = 20
    CONTOUR_MAX_AREA = 8000
    CONTOUR_MAX_CANDIDATES = 40
    CONTOUR_MARGIN = 0.30
    CONTOUR_ZOOMS = [4, 6]

    DETECT_MULTI_SCALES = [1.0, 1.5]
    DETECT_MULTI_MAX_SIDE = 2200
    DETECT_MULTI_MAX_CANDIDATES = 12

    GRID_IF_MISSING_AT_LEAST = 2
    CONTOUR_IF_MISSING_AT_LEAST = 2
    MAX_FINDER_REGIONS = 20
    MAX_GRID_CELLS_TOTAL = 28

    DUP_CENTER_DIST = 25
    DUP_IOU_THRESH = 0.30

    DEFAULT_INPUT = "images"
    DEFAULT_OUTPUT = "results"
    SAVE_JSON = True
    SAVE_ANNOTATED = True

    DEBUG_SAVE_ROIS = False
    DEBUG_DIR = "results/debug_rois"


# -------------------------
# Data models
# -------------------------
@dataclass
class QRData:
    raw: str = ""
    imei: str = ""
    serial: str = ""

@dataclass
class QRPatch:
    id: int = 0
    data: QRData = field(default_factory=QRData)
    bbox: Tuple[int,int,int,int] = (0,0,0,0)
    points: List[Tuple[int,int]] = field(default_factory=list)
    source: str = "unknown"
    stage: str = ""
    confidence: float = 1.0

    def to_dict(self):
        return {
            "id": self.id,
            "data": vars(self.data),
            "bbox": self.bbox,
            "points": self.points,
            "source": self.source,
            "stage": self.stage,
            "confidence": self.confidence
        }

@dataclass
class ImageReport:
    filename: str
    path: str
    success: bool
    qr_count: int
    patches: List[QRPatch]
    elapsed: float
    error: str = ""


# -------------------------
# Utilities
# -------------------------
def is_valid_qr(data: str) -> bool:
    if not data:
        return False
    s = data.strip()
    if len(s) < Config.MIN_QR_LEN and not re.search(r"\d{6,}", s):
        return False
    if s.upper() in Config.BLACKLIST:
        return False

    parts = [part.strip() for part in s.split(';', 1)] if ';' in s else [s]
    imei = ''
    serial = ''
    if parts:
        m = re.search(r"\b(\d{14,16})\b", parts[0])
        if m:
            imei = m.group(1)
    if len(parts) > 1:
        m = re.search(r"\b[A-Z0-9\-]{6,}\b", parts[1], re.IGNORECASE)
        if m:
            serial = m.group(0)
    else:
        m1 = re.search(r"\b(\d{14,16})\b", s)
        if m1:
            imei = m1.group(1)
        m2 = re.search(r"\b[A-Z]{2,}[A-Z0-9\-]{4,}\b", s, re.IGNORECASE)
        if m2 and m2.group(0) != imei:
            serial = m2.group(0)

    return bool(imei and serial)

def parse_qr(data: str) -> QRData:
    res = QRData(raw=data)
    if ";" in data:
        parts = data.split(";", 1)
        res.imei = parts[0].strip()
        if len(parts) > 1:
            res.serial = parts[1].strip()
    else:
        imei_match = re.search(r"\b(\d{14,16})\b", data)
        if imei_match:
            res.imei = imei_match.group(1)
        serial_match = re.search(r"[A-Z0-9\-]{6,}", data)
        if serial_match:
            res.serial = serial_match.group(0)
    return res

def guess_expected_qr(filename: str) -> int:
    name = os.path.splitext(filename)[0].lower()
    patterns = [
        r"(\d+)\s*[-_ ]*\s*qrs?",
        r"qrs?\s*[-_ ]*(\d+)",
        r"[_\s-](\d+)\s*[-_ ]*\s*qrs?",
    ]
    for pat in patterns:
        for match in re.finditer(pat, name):
            n = int(match.group(1))
            if n in (1, 2, 4, 6, 8, 10):
                return n
    m = re.search(r"\b(10|1|2|4|6|8)\b", name)
    if m:
        return int(m.group(1))
    return Config.DEFAULT_EXPECTED

def bbox_iou(b1, b2):
    x1,y1,w1,h1 = b1
    x2,y2,w2,h2 = b2
    xi1 = max(x1,x2); yi1 = max(y1,y2)
    xi2 = min(x1+w1, x2+w2); yi2 = min(y1+h1, y2+h2)
    inter = max(0, xi2-xi1) * max(0, yi2-yi1)
    union = max(1, w1*h1 + w2*h2 - inter)
    return inter/union

def bbox_area(bbox: Tuple[int, int, int, int]) -> int:
    return max(1, int(bbox[2])) * max(1, int(bbox[3]))

def is_global_patch(p: QRPatch) -> bool:
    return p.stage == "full" or p.source in {"wechat_full", "decode_gray", "decode_v"}

def is_placeholder_patch(p: QRPatch) -> bool:
    raw = getattr(getattr(p, 'data', None), 'raw', '') or ''
    return raw.startswith('layout_inferred::')

def is_real_count_patch(p: QRPatch) -> bool:
    raw = getattr(getattr(p, 'data', None), 'raw', '') or ''
    return bool(raw) and not raw.startswith('layout_inferred::')

def merge_or_append_patch(patches: List[QRPatch], new_patch: QRPatch) -> bool:
    new_area = bbox_area(new_patch.bbox)
    new_global = is_global_patch(new_patch)
    new_has_points = bool(getattr(new_patch, 'points', None))
    new_localized = getattr(new_patch, 'source', '') in {'relocalized_cell', 'module_local', 'wechat_roi'} or str(getattr(new_patch, 'stage', '')).startswith('INF_LOC_')

    for idx, old_patch in enumerate(patches):
        if old_patch.data.raw != new_patch.data.raw:
            continue

        old_area = bbox_area(old_patch.bbox)
        old_global = is_global_patch(old_patch)
        old_has_points = bool(getattr(old_patch, 'points', None))
        old_localized = getattr(old_patch, 'source', '') in {'relocalized_cell', 'module_local', 'wechat_roi'} or str(getattr(old_patch, 'stage', '')).startswith('INF_LOC_')

        replace = False
        if old_global and not new_global:
            replace = True
        elif not old_has_points and new_has_points and new_area <= int(old_area * 1.60):
            replace = True
        elif not old_localized and new_localized and new_area <= int(old_area * 1.60) and new_patch.confidence >= (old_patch.confidence - 0.10):
            replace = True
        elif not new_global and new_area < int(old_area * 0.60) and new_patch.confidence >= (old_patch.confidence - 0.08):
            replace = True
        elif new_patch.confidence > (old_patch.confidence + 0.03) and new_area <= int(old_area * 1.25):
            replace = True

        if replace:
            patches[idx] = new_patch
            return True
        return False

    patches.append(new_patch)
    return True

def deduplicate(patches: List[QRPatch]) -> List[QRPatch]:
    groups = defaultdict(list)
    for p in patches:
        if p.data.raw:
            groups[p.data.raw].append(p)

    by_data = []
    for data, group in groups.items():
        best = max(group, key=lambda x: (0 if is_global_patch(x) else 1, x.confidence, -bbox_area(x.bbox)))
        by_data.append(best)

    final = []
    for p in by_data:
        dup = False
        for e in final:
            if p.data.raw != e.data.raw and (is_global_patch(p) or is_global_patch(e)):
                continue
            if bbox_iou(p.bbox, e.bbox) > Config.DUP_IOU_THRESH:
                dup = True
                break
            cx1 = p.bbox[0] + p.bbox[2]//2
            cy1 = p.bbox[1] + p.bbox[3]//2
            cx2 = e.bbox[0] + e.bbox[2]//2
            cy2 = e.bbox[1] + e.bbox[3]//2
            if math.hypot(cx1-cx2, cy1-cy2) < Config.DUP_CENTER_DIST:
                dup = True
                break
        if not dup:
            final.append(p)
    return final

def adjust_gamma(image, gamma=1.0):
    inv = 1.0 / max(gamma, 1e-6)
    table = np.array([((i / 255.0) ** inv) * 255 for i in range(256)], dtype=np.float32)
    return cv2.LUT(image, table.astype("uint8"))

def preprocess_v_channel_color(img_bgr):
    try:
        hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
        v = hsv[:, :, 2]
        v = cv2.bilateralFilter(v, d=7, sigmaColor=75, sigmaSpace=75)
        v = adjust_gamma(v, gamma=0.9)
        return v
    except Exception:
        return cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

def unsharp(gray: np.ndarray, amount: float = 1.3, radius: float = 1.2) -> np.ndarray:
    blur = cv2.GaussianBlur(gray, (0,0), radius)
    sharp = cv2.addWeighted(gray, 1.0 + amount, blur, -amount, 0)
    return sharp

def quiet_zone_pad(gray: np.ndarray, pad_ratio: float = 0.18) -> int:
    h, w = gray.shape[:2]
    pad = int(max(h, w) * pad_ratio)
    return max(6, pad)

def add_quiet_zone(gray: np.ndarray, pad_ratio: float = 0.18) -> np.ndarray:
    """Add border around ROI to help decoders (quiet zone)."""
    pad = quiet_zone_pad(gray, pad_ratio)
    return cv2.copyMakeBorder(
        gray, pad, pad, pad, pad, borderType=cv2.BORDER_CONSTANT, value=255
    )

def map_qr_points(points, ox: int = 0, oy: int = 0, scale: float = 1.0, pad: int = 0,
                  limit_w: int | None = None, limit_h: int | None = None) -> List[Tuple[int, int]]:
    try:
        pa = np.array(points, dtype=np.float32).reshape(-1, 2)
    except Exception:
        return []

    mapped = []
    for px, py in pa:
        lx = int(px / max(scale, 1e-6)) - pad
        ly = int(py / max(scale, 1e-6)) - pad
        if limit_w is not None:
            lx = min(max(0, lx), max(0, limit_w - 1))
        else:
            lx = max(0, lx)
        if limit_h is not None:
            ly = min(max(0, ly), max(0, limit_h - 1))
        else:
            ly = max(0, ly)
        mapped.append((ox + lx, oy + ly))
    return mapped

def bbox_from_points(points: List[Tuple[int, int]], fallback: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    if not points:
        return fallback
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x1, x2 = min(xs), max(xs)
    y1, y2 = min(ys), max(ys)
    return (int(x1), int(y1), max(1, int(x2 - x1)), max(1, int(y2 - y1)))

def blackhat_enhance(gray: np.ndarray) -> np.ndarray:
    """Highlights dark modules on bright background / glare cases."""
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9,9))
    bh = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel)
    out = cv2.normalize(gray + 2*bh, None, 0, 255, cv2.NORM_MINMAX)
    return out.astype(np.uint8)


def normalize_illumination(gray: np.ndarray) -> np.ndarray:
    """Cheap glare compensation for faint QR modules on metal shields."""
    blur = cv2.GaussianBlur(gray, (0, 0), sigmaX=9, sigmaY=9)
    norm = cv2.divide(gray, blur, scale=190)
    return cv2.normalize(norm, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)

def pyzbar_decode_qr(img):
    try:
        if PYZBAR_QR_ONLY:
            return pyzbar_decode(img, symbols=PYZBAR_QR_ONLY)
        return pyzbar_decode(img)
    except Exception:
        return []


def upscale_simple(img: np.ndarray, scale: float = 2.0) -> np.ndarray:
    h, w = img.shape[:2]
    return cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)


def strong_micro_preprocess(gray: np.ndarray) -> List[Tuple[str, np.ndarray]]:
    out = [("raw", gray)]
    try:
        clahe = cv2.createCLAHE(clipLimit=8.0, tileGridSize=(16, 16))
        c = clahe.apply(gray)
        out.append(("clahe8", c))
        out.append(("clahe8_sharp", unsharp(c, amount=1.5, radius=1.0)))
    except Exception:
        pass

    try:
        out.append(("gauss3", cv2.GaussianBlur(gray, (3, 3), 0)))
    except Exception:
        pass

    try:
        out.append(("sharp", unsharp(gray, amount=1.5, radius=1.0)))
    except Exception:
        pass

    try:
        out.append(("blackhat", blackhat_enhance(gray)))
    except Exception:
        pass

    try:
        norm = normalize_illumination(gray)
        out.append(("norm", norm))
        out.append(("norm_sharp", unsharp(norm, amount=1.35, radius=1.0)))
        _, norm_otsu = cv2.threshold(norm, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        out.append(("norm_otsu", norm_otsu))
    except Exception:
        pass

    try:
        _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        out.append(("otsu", otsu))
    except Exception:
        pass

    try:
        adapt = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 3
        )
        out.append(("adapt21", adapt))
    except Exception:
        pass

    invs = []
    for name, im in out[:]:
        try:
            invs.append((name + "_inv", 255 - im))
        except Exception:
            pass
    out.extend(invs)
    return out


def get_preprocessing_variants(gray: np.ndarray, max_variants: int = None) -> List[Tuple[str, np.ndarray]]:
    variants = [("raw", gray)]
    try:
        variants.append((
            "clahe_light",
            cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        ))
    except Exception:
        pass

    for name, func in Config.PREPROCESS_TECHNIQUES:
        try:
            variants.append((name, func(gray)))
        except Exception:
            pass

    if max_variants is not None:
        return variants[:max_variants]
    return variants


def try_decode_variants(roi_bgr: np.ndarray) -> List[str]:
    texts = []

    try:
        for text, _ in decode_multi(preprocess_v_channel_color(roi_bgr)):
            if is_valid_qr(text):
                texts.append(text)
    except Exception:
        pass

    try:
        gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
        for text, _ in decode_multi(gray):
            if is_valid_qr(text):
                texts.append(text)
    except Exception:
        pass

    try:
        qr = cv2.QRCodeDetector()
        for angle in (0, 90, 180, 270):
            M = cv2.getRotationMatrix2D(
                (roi_bgr.shape[1] / 2, roi_bgr.shape[0] / 2), angle, 1.0
            )
            rot = cv2.warpAffine(
                roi_bgr, M, (roi_bgr.shape[1], roi_bgr.shape[0]), flags=cv2.INTER_LINEAR
            )
            ok, decoded_info, _, _ = qr.detectAndDecodeMulti(rot)
            if ok and decoded_info:
                for text in decoded_info:
                    if text and is_valid_qr(text):
                        texts.append(text)
    except Exception:
        pass

    try:
        gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
        for scale in (1.5, 2.0, 3.0):
            for text, _ in decode_multi(upscale_simple(gray, scale)):
                if is_valid_qr(text):
                    texts.append(text)
    except Exception:
        pass

    unique = []
    for text in texts:
        if text not in unique:
            unique.append(text)
    return unique


# -------------------------
# Decoders
# -------------------------
def decode_with_zxing(gray: np.ndarray) -> List[str]:
    if not (Config.USE_ZXING and ZXING_OK):
        return []
    out = []
    try:
        codes = zxingcpp.read_barcodes(gray)
        for c in codes:
            txt = getattr(c, "text", "") or ""
            if txt and is_valid_qr(txt):
                out.append(txt)
    except Exception:
        pass
    return out

def decode_multi(gray_or_bgr: np.ndarray) -> List[Tuple[str, Tuple[int,int,int,int]]]:
    """Returns list of (text, rect) where rect is (x,y,w,h) in that ROI coordinate space."""
    results = []

    if len(gray_or_bgr.shape) == 3:
        gray = cv2.cvtColor(gray_or_bgr, cv2.COLOR_BGR2GRAY)
    else:
        gray = gray_or_bgr

    # ZXing first
    for txt in decode_with_zxing(gray):
        h,w = gray.shape[:2]
        results.append((txt, (0,0,w,h)))
    if results:
        return results

    # pyzbar
    for b in pyzbar_decode_qr(gray):
        try:
            txt = b.data.decode("utf-8")
        except Exception:
            try:
                txt = b.data.decode("latin-1", errors="ignore")
            except Exception:
                txt = ""
        if is_valid_qr(txt):
            try:
                rect = (int(b.rect.left), int(b.rect.top), int(b.rect.width), int(b.rect.height))
            except Exception:
                rect = (int(b.rect[0]), int(b.rect[1]), int(b.rect[2]), int(b.rect[3]))
            results.append((txt, rect))
    if results:
        return results

    # OpenCV fallback
    try:
        qr = cv2.QRCodeDetector()
        ok, decoded_info, points, _ = qr.detectAndDecodeMulti(gray)
        if ok and decoded_info:
            for txt in decoded_info:
                if txt and is_valid_qr(txt):
                    h,w = gray.shape[:2]
                    results.append((txt, (0,0,w,h)))
    except Exception:
        pass

    return results


# -------------------------
# Thread-safe WeChat detector
# -------------------------
_thread_local = threading.local()

def get_wechat_detector():
    if not Config.USE_WECHAT:
        return None
    det = getattr(_thread_local, "wechat_detector", None)
    if det is not None:
        return det
    try:
        det = cv2.wechat_qrcode_WeChatQRCode()
    except Exception:
        det = None
    _thread_local.wechat_detector = det
    return det


# -------------------------
# Layout ROIs (stronger for your boards)
# -------------------------
def infer_grid_shape(expected: int, w: int, h: int) -> Tuple[int,int]:
    ar = w / max(h, 1)
    if expected <= 1:
        return (1, 1)
    if expected == 2:
        return (1,2) if ar >= 1.0 else (2,1)
    if expected == 4:
        return (2,2)
    if expected == 6:
        return (2,3) if ar >= 1.1 else (3,2)
    if expected == 8:
        return (2,4) if ar >= 1.15 else (4,2)
    if expected == 10:
        return (2,5) if ar >= 1.15 else (5,2)
    return (2,2)

def split_into_cells(gray: np.ndarray, expected: int) -> List[Tuple[int,int,int,int,str,int,int]]:
    H, W = gray.shape[:2]
    rows, cols = infer_grid_shape(expected, W, H)
    cells = []

    for r in range(rows):
        for c in range(cols):
            x0 = int(round(c * W / max(cols, 1)))
            y0 = int(round(r * H / max(rows, 1)))
            x1 = int(round((c + 1) * W / max(cols, 1)))
            y1 = int(round((r + 1) * H / max(rows, 1)))
            x0 = max(0, x0); y0 = max(0, y0)
            x1 = min(W, x1); y1 = min(H, y1)
            if (x1 - x0) < 48 or (y1 - y0) < 48:
                continue
            cells.append((x0, y0, x1 - x0, y1 - y0, f"cell{r}{c}", r, c))
    return cells

def _module_side(cell: np.ndarray) -> str:
    h, w = cell.shape[:2]
    if h < 32 or w < 32:
        return "wide"

    try:
        work = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(8, 8)).apply(cell)
    except Exception:
        work = cell

    candidates = {
        "left":  (int(w * 0.08), int(h * 0.16), int(w * 0.76), int(h * 0.96)),
        "right": (int(w * 0.22), int(h * 0.16), int(w * 0.92), int(h * 0.96)),
    }

    scores = {}
    for name, (x0, y0, x1, y1) in candidates.items():
        roi = work[y0:y1, x0:x1]
        if roi.size == 0:
            scores[name] = 0.0
            continue
        try:
            gx = cv2.Sobel(roi, cv2.CV_32F, 1, 0, ksize=3)
            gy = cv2.Sobel(roi, cv2.CV_32F, 0, 1, ksize=3)
            scores[name] = float(roi.std() + 0.20 * np.mean(np.abs(gx)) + 0.10 * np.mean(np.abs(gy)))
        except Exception:
            scores[name] = float(roi.std())

    left_score = scores.get("left", 0.0)
    right_score = scores.get("right", 0.0)
    best = max(left_score, right_score)
    worst = min(left_score, right_score)
    if best <= 1.0 or best < worst * 1.08:
        return "wide"
    return "left" if left_score >= right_score else "right"

def crop_target_qr_zone(cell: np.ndarray, panel_type=None) -> Tuple[int,int,int,int]:
    h, w = cell.shape[:2]
    side = panel_type or _module_side(cell)
    if side == "left":
        x0 = int(w * 0.08)
        y0 = int(h * 0.16)
        x1 = int(w * 0.76)
        y1 = int(h * 0.96)
    elif side == "right":
        x0 = int(w * 0.22)
        y0 = int(h * 0.16)
        x1 = int(w * 0.92)
        y1 = int(h * 0.96)
    else:
        x0 = int(w * 0.18)
        y0 = int(h * 0.16)
        x1 = int(w * 0.94)
        y1 = int(h * 0.96)
    x0 = max(0, min(x0, w - 1))
    y0 = max(0, min(y0, h - 1))
    x1 = max(x0 + 1, min(x1, w))
    y1 = max(y0 + 1, min(y1, h))
    return (x0, y0, x1 - x0, y1 - y0)

def _peer_qr_templates(gray: np.ndarray, expected: int, patches: List[QRPatch]) -> dict:
    if gray is None or gray.size == 0 or expected <= 1:
        return {"by_col": {}, "global": None}

    h, w = gray.shape[:2]
    rows, cols = infer_grid_shape(expected, w, h)
    cells = {(r, c): (x0, y0, cw, ch) for x0, y0, cw, ch, _, r, c in split_into_cells(gray, expected)}
    if not cells:
        return {"by_col": {}, "global": None}

    by_col = defaultdict(list)
    all_boxes = []
    for p in deduplicate(patches):
        if not is_real_count_patch(p):
            continue
        if p.source == 'layout_inferred' or str(p.stage).startswith('INF_'):
            continue
        x, y, bw, bh = p.bbox
        if bw <= 0 or bh <= 0:
            continue
        cx = x + bw / 2.0
        cy = y + bh / 2.0
        c = min(cols - 1, max(0, int(cx / max(w / cols, 1))))
        r = min(rows - 1, max(0, int(cy / max(h / rows, 1))))
        cell = cells.get((r, c))
        if cell is None:
            continue
        x0, y0, cw, ch = cell
        area_ratio = (bw * bh) / max(1.0, float(cw * ch))
        width_ratio = bw / max(1.0, float(cw))
        height_ratio = bh / max(1.0, float(ch))
        precise = bool(p.points) or (area_ratio <= 0.13 and width_ratio <= 0.22 and height_ratio <= 0.22)
        if not precise:
            continue
        norm = (
            (x - x0) / max(1.0, float(cw)),
            (y - y0) / max(1.0, float(ch)),
            bw / max(1.0, float(cw)),
            bh / max(1.0, float(ch)),
        )
        by_col[c].append(norm)
        all_boxes.append(norm)

    def _agg(vals):
        if not vals:
            return None
        arr = np.array(vals, dtype=np.float32)
        med = np.median(arr, axis=0)
        return tuple(float(x) for x in med)

    return {
        "by_col": {col: _agg(vals) for col, vals in by_col.items() if vals},
        "global": _agg(all_boxes),
    }


def _occupied_cell_keys(gray: np.ndarray, expected: int, patches: List[QRPatch]) -> set:
    h, w = gray.shape[:2]
    rows, cols = infer_grid_shape(expected, w, h)
    cell_h = h / max(rows, 1)
    cell_w = w / max(cols, 1)
    img_area = h * w
    occupied = set()

    for p in deduplicate(patches):
        if getattr(p, 'source', '') == 'layout_inferred' or str(getattr(p, 'stage', '')).startswith('INF_'):
            continue
        x, y, bw, bh = p.bbox
        if bw <= 0 or bh <= 0:
            continue
        if (bw * bh) >= 0.65 * img_area:
            # Whole-image detections are useful, but they should not block per-cell rescue.
            continue
        cx = x + bw / 2.0
        cy = y + bh / 2.0
        c = min(cols - 1, max(0, int(cx / max(cell_w, 1))))
        r = min(rows - 1, max(0, int(cy / max(cell_h, 1))))
        occupied.add((r, c))
    return occupied

def _coarse_patch_candidates(gray: np.ndarray, expected: int, patches: List[QRPatch]) -> List[QRPatch]:
    h, w = gray.shape[:2]
    img_area = h * w
    rows, cols = infer_grid_shape(expected, w, h)
    cell_area = img_area / max(1, rows * cols)
    cell_w = w / max(cols, 1)
    cell_h = h / max(rows, 1)
    out = []
    for p in deduplicate(patches):
        x, y, bw, bh = p.bbox
        area = bw * bh
        if bw <= 0 or bh <= 0:
            continue
        if p.points:
            continue

        cx = x + bw / 2.0
        cy = y + bh / 2.0
        c = min(cols - 1, max(0, int(cx / max(cell_w, 1))))
        r = min(rows - 1, max(0, int(cy / max(cell_h, 1))))
        x0 = int(round(c * w / max(cols, 1)))
        y0 = int(round(r * h / max(rows, 1)))
        x1 = int(round((c + 1) * w / max(cols, 1)))
        y1 = int(round((r + 1) * h / max(rows, 1)))
        cell = gray[y0:y1, x0:x1]
        off_target = False
        if cell.size != 0:
            try:
                zx, zy, zw, zh = crop_target_qr_zone(cell, panel_type=_module_side(cell))
                tx0 = x0 + zx + int(zw * 0.26)
                ty0 = y0 + zy + int(zh * 0.26)
                tx1 = x0 + zx + zw
                ty1 = y0 + zy + zh
                off_target = not (tx0 <= cx <= tx1 and ty0 <= cy <= ty1)
            except Exception:
                off_target = False

        if (
            area >= 0.65 * img_area
            or is_global_patch(p)
            or area >= 0.42 * cell_area
            or (p.source in {'decode_roi', 'targeted_cell_tryvar', 'failed_cell_tryvar', 'peer_template'} and area >= 0.14 * cell_area)
            or off_target
        ):
            out.append(p)
    return out


def _localize_known_text_in_cell(wechat, cell: np.ndarray, x0: int, y0: int,
                                 cell_name: str, target_raw: str,
                                 panel_type=None, deadline=None) -> QRPatch | None:
    if cell is None or cell.size == 0 or not target_raw:
        return None

    side = panel_type or _module_side(cell)
    h, w = cell.shape[:2]
    target = crop_target_qr_zone(cell, panel_type=side)
    windows = [("target", target)]
    if side == "left":
        windows.extend([
            ("qr_bl", (0, int(h * 0.46), int(w * 0.64), h)),
            ("legacy_br", (int(w * 0.42), int(h * 0.40), int(w * 0.99), int(h * 0.99))),
            ("module_wide", (int(w * 0.16), int(h * 0.16), int(w * 0.96), int(h * 0.97))),
            ("alt_target_right", crop_target_qr_zone(cell, panel_type="right")),
            ("alt_qr_br", (int(w * 0.48), int(h * 0.50), w, h)),
            ("module_right", (int(w * 0.22), int(h * 0.16), int(w * 0.92), int(h * 0.96))),
        ])
    elif side == "right":
        windows.extend([
            ("qr_br", (int(w * 0.48), int(h * 0.50), w, h)),
            ("module_wide", (int(w * 0.16), int(h * 0.16), int(w * 0.96), int(h * 0.97))),
            ("legacy_br", (int(w * 0.42), int(h * 0.40), int(w * 0.99), int(h * 0.99))),
            ("module_right", (int(w * 0.22), int(h * 0.16), int(w * 0.92), int(h * 0.96))),
            ("alt_target_left", crop_target_qr_zone(cell, panel_type="left")),
            ("alt_qr_bl", (0, int(h * 0.46), int(w * 0.64), h)),
        ])
    else:
        windows.extend([
            ("module_left", (int(w * 0.10), int(h * 0.16), int(w * 0.78), int(h * 0.96))),
            ("module_right", (int(w * 0.22), int(h * 0.16), int(w * 0.92), int(h * 0.96))),
            ("legacy_br", (int(w * 0.42), int(h * 0.40), int(w * 0.99), int(h * 0.99))),
        ])

    scales = (3.0, 4.0, 6.0)
    candidates: List[QRPatch] = []

    def accept_candidate(candidate: QRPatch, zone_area: int):
        area = bbox_area(candidate.bbox)
        if area >= max(1, int(zone_area * 0.62)):
            return
        candidates.append(candidate)

    for zone_name, zone in windows:
        if len(zone) == 4 and zone_name == 'target':
            zx, zy, zw, zh = zone
            ex0, ey0, ex1, ey1 = zx, zy, zx + zw, zy + zh
        else:
            ex0, ey0, ex1, ey1 = zone
            ex0 = max(0, ex0); ey0 = max(0, ey0)
            ex1 = min(w, ex1); ey1 = min(h, ey1)
            if ex1 <= ex0 or ey1 <= ey0:
                continue
            zw, zh = ex1 - ex0, ey1 - ey0
            zx, zy = ex0, ey0

        roi = cell[zy:zy + zh, zx:zx + zw]
        if roi.size == 0 or roi.shape[0] < 18 or roi.shape[1] < 18:
            continue

        pad_ratio = 0.12
        pad = quiet_zone_pad(roi, pad_ratio)
        padded = add_quiet_zone(roi, pad_ratio=pad_ratio)
        zone_area = zw * zh

        if wechat is not None:
            for sc in scales:
                if deadline and deadline():
                    return None
                try:
                    up = cv2.resize(padded, (0, 0), fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
                    res, pts = wechat.detectAndDecode(cv2.cvtColor(up, cv2.COLOR_GRAY2BGR))
                except Exception:
                    res, pts = [], None
                if not res:
                    continue
                for i, txt in enumerate(res):
                    if txt != target_raw:
                        continue
                    bbox = (x0 + zx, y0 + zy, zw, zh)
                    points = []
                    if pts is not None and len(pts) > i:
                        points = map_qr_points(pts[i], ox=x0 + zx, oy=y0 + zy, scale=sc, pad=pad,
                                               limit_w=roi.shape[1], limit_h=roi.shape[0])
                        bbox = bbox_from_points(points, bbox)
                    accept_candidate(QRPatch(
                        data=parse_qr(txt),
                        bbox=bbox,
                        points=points,
                        source='relocalized_cell',
                        stage=f'LOC_{cell_name}_{zone_name}_wechat_s{sc}',
                        confidence=0.985,
                    ), zone_area)

        variants = [('raw', padded)]
        try:
            variants.append(('clahe', cv2.createCLAHE(clipLimit=3.5, tileGridSize=(8,8)).apply(padded)))
        except Exception:
            pass
        try:
            variants.append(('sharp', unsharp(padded, amount=1.2, radius=1.0)))
        except Exception:
            pass

        for vname, vimg in variants:
            for sc in scales:
                try:
                    up = cv2.resize(vimg, (0, 0), fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
                except Exception:
                    continue
                try:
                    decs = decode_multi(up)
                except Exception:
                    decs = []
                for txt, rect in decs:
                    if txt != target_raw:
                        continue
                    accept_candidate(QRPatch(
                        data=parse_qr(txt),
                        bbox=(
                            x0 + zx + max(0, int(rect[0] / sc) - pad),
                            y0 + zy + max(0, int(rect[1] / sc) - pad),
                            max(1, int(rect[2] / sc)),
                            max(1, int(rect[3] / sc)),
                        ),
                        source='relocalized_cell',
                        stage=f'LOC_{cell_name}_{zone_name}_{vname}_s{sc}',
                        confidence=0.955,
                    ), zone_area)

    if not candidates:
        return None
    return min(candidates, key=lambda p: (bbox_area(p.bbox), -p.confidence))


def refine_real_patch_positions(gray: np.ndarray, expected: int, patches: List[QRPatch], deadline=None) -> int:
    if gray is None or gray.size == 0 or expected <= 1:
        return 0
    wechat = get_wechat_detector()
    replacements = 0
    refined = []
    cells = list(split_into_cells(gray, expected))
    real_patches = [p for p in patches if is_real_count_patch(p)]

    def pick_cell_for_patch(patch: QRPatch):
        x, y, w, h = patch.bbox
        cx = x + max(1, w) // 2
        cy = y + max(1, h) // 2
        for x0, y0, cw, ch, cell_name, r, c in cells:
            if x0 <= cx < x0 + cw and y0 <= cy < y0 + ch:
                return x0, y0, cw, ch, cell_name, r, c
        return None

    for patch in real_patches:
        if deadline and deadline():
            break
        raw = getattr(getattr(patch, 'data', None), 'raw', '') or ''
        if not raw:
            continue
        cell_info = pick_cell_for_patch(patch)
        if not cell_info:
            continue
        x0, y0, cw, ch, cell_name, r, c = cell_info
        cell = gray[y0:y0+ch, x0:x0+cw]
        cand = _localize_known_text_in_cell(wechat, cell, x0, y0, cell_name, raw, panel_type=_module_side(cell), deadline=deadline)
        if not cand:
            continue
        old_area = bbox_area(patch.bbox)
        new_area = bbox_area(cand.bbox)
        old_points = bool(getattr(patch, 'points', None))
        new_points = bool(getattr(cand, 'points', None))
        if new_points or (new_area > 0 and (old_area <= 0 or new_area < old_area * 0.72)):
            patch.bbox = cand.bbox
            patch.points = cand.points
            patch.source = cand.source
            patch.stage = cand.stage
            patch.confidence = max(patch.confidence, cand.confidence)
            replacements += 1
    return replacements


def relocalize_coarse_patches(gray: np.ndarray, expected: int, patches: List[QRPatch], deadline=None, max_candidates: int = 2) -> int:
    if gray is None or gray.size == 0 or expected <= 1:
        return 0

    wechat = get_wechat_detector()
    candidates = _coarse_patch_candidates(gray, expected, patches)
    if not candidates:
        return 0

    candidates = sorted(candidates, key=lambda p: bbox_area(p.bbox), reverse=True)[:max_candidates]
    coarse_raws = {p.data.raw for p in candidates if p.data.raw}
    stable_patches = [p for p in deduplicate(patches) if p.data.raw not in coarse_raws]
    occupied = _occupied_cell_keys(gray, expected, stable_patches)
    found = 0
    for coarse in candidates:
        if deadline and deadline():
            break
        if not coarse.data.raw:
            continue
        for x0, y0, cw, ch, cell_name, r, c in split_into_cells(gray, expected):
            if deadline and deadline():
                break
            if (r, c) in occupied:
                continue
            cell = gray[y0:y0 + ch, x0:x0 + cw]
            localized = _localize_known_text_in_cell(
                wechat, cell, x0, y0, cell_name, coarse.data.raw,
                panel_type=_module_side(cell),
                deadline=deadline,
            )
            if localized is None:
                continue
            if merge_or_append_patch(patches, localized):
                occupied.add((r, c))
                found += 1
            break
    return found

def infer_layout_qr_patches(gray: np.ndarray, expected: int, patches: List[QRPatch]) -> int:
    if gray is None or gray.size == 0 or expected != 4:
        return 0

    cells = list(split_into_cells(gray, expected))
    if len(cells) != 4:
        return 0

    wechat = get_wechat_detector()
    h, w = gray.shape[:2]
    img_area = h * w
    rows, cols = infer_grid_shape(expected, w, h)
    cell_area = img_area / max(1, rows * cols)

    def cell_key_for_bbox(bbox):
        x, y, bw, bh = bbox
        if bw <= 0 or bh <= 0:
            return None
        cx = x + bw / 2.0
        cy = y + bh / 2.0
        c = min(cols - 1, max(0, int(cx / max(w / cols, 1))))
        r = min(rows - 1, max(0, int(cy / max(h / rows, 1))))
        return (r, c)

    def canonical_box(cell):
        x0, y0, cw, ch, cell_name, r, c = cell
        cell_img = gray[y0:y0 + ch, x0:x0 + cw]
        side = _module_side(cell_img)
        rx, ry, rw, rh = crop_target_qr_zone(cell_img, panel_type=side)
        qr_size = max(28, int(min(rw, rh) * 0.23))
        pad_x = max(6, int(rw * 0.08))
        pad_y = max(6, int(rh * 0.10))
        qx = x0 + rx + max(0, rw - qr_size - pad_x)
        qy = y0 + ry + max(0, rh - qr_size - pad_y)
        return (qx, qy, qr_size, qr_size)

    peer_norms = []
    coarse_by_cell = {}
    occupied = set()

    for p in deduplicate(patches):
        key = cell_key_for_bbox(p.bbox)
        if key is None:
            continue
        x, y, bw, bh = p.bbox
        area = bw * bh
        cell = next((cell for cell in cells if (cell[5], cell[6]) == key), None)
        if cell is None:
            continue
        x0, y0, cw, ch, cell_name, r, c = cell
        local = (x - x0, y - y0, bw, bh)
        if p.points or (bw > 0 and bh > 0 and area < 0.12 * cell_area and max(bw, bh) < 0.45 * max(cw, ch)):
            peer_norms.append((
                local[0] / max(cw, 1),
                local[1] / max(ch, 1),
                local[2] / max(cw, 1),
                local[3] / max(ch, 1),
            ))
            occupied.add(key)
        else:
            coarse_by_cell[key] = p

    avg_box = None
    if len(peer_norms) >= 2:
        avg = [sum(vals) / len(vals) for vals in zip(*peer_norms)]
        nx, ny, nw, nh = avg
        nx = min(max(nx, 0.02), 0.88)
        ny = min(max(ny, 0.02), 0.88)
        nw = min(max(nw, 0.06), 0.45)
        nh = min(max(nh, 0.06), 0.45)
        avg_box = (nx, ny, nw, nh)

    def inferred_box(cell, prefer_canonical=False):
        x0, y0, cw, ch, cell_name, r, c = cell
        canon = canonical_box(cell)
        if avg_box is None or prefer_canonical:
            return canon
        nx, ny, nw, nh = avg_box
        avg_abs = (
            x0 + int(nx * cw),
            y0 + int(ny * ch),
            max(28, int(nw * cw)),
            max(28, int(nh * ch)),
        )
        if (
            abs(avg_abs[0] - canon[0]) > int(0.18 * cw)
            or abs(avg_abs[1] - canon[1]) > int(0.18 * ch)
            or avg_abs[2] > int(1.8 * canon[2])
            or avg_abs[3] > int(1.8 * canon[3])
        ):
            return canon
        return (
            int(0.35 * avg_abs[0] + 0.65 * canon[0]),
            int(0.35 * avg_abs[1] + 0.65 * canon[1]),
            max(28, int(0.35 * avg_abs[2] + 0.65 * canon[2])),
            max(28, int(0.35 * avg_abs[3] + 0.65 * canon[3])),
        )

    changed = 0

    for cell in cells:
        x0, y0, cw, ch, cell_name, r, c = cell
        key = (r, c)
        inf_box = inferred_box(cell, prefer_canonical=True)
        if key in coarse_by_cell:
            old = coarse_by_cell[key]
            cell_img = gray[y0:y0 + ch, x0:x0 + cw]
            localized = None
            if old.data.raw:
                localized = _localize_known_text_in_cell(
                    wechat,
                    cell_img,
                    x0,
                    y0,
                    cell_name,
                    old.data.raw,
                    panel_type=_module_side(cell_img),
                )
            if localized is not None:
                localized.stage = f'INF_LOC_{cell_name}'
                localized.confidence = max(localized.confidence, old.confidence, 0.985)
                if merge_or_append_patch(patches, localized):
                    changed += 1
                    occupied.add(key)
                continue

            # Do not replace a real decoded payload with a layout-inferred box.
            # If localization fails, keep the original patch and leave only placeholder boxes for missing cells.
            occupied.add(key)

    missing = [cell for cell in cells if (cell[5], cell[6]) not in occupied]
    for x0, y0, cw, ch, cell_name, r, c in missing:
        inf_box = inferred_box((x0, y0, cw, ch, cell_name, r, c))
        placeholder_raw = f'layout_inferred::{cell_name}'
        if not any(p.data.raw == placeholder_raw for p in patches):
            patches.append(QRPatch(
                data=QRData(raw=placeholder_raw, imei='', serial=''),
                bbox=inf_box,
                source='layout_inferred',
                stage=f'INF_{cell_name}_missing',
                confidence=0.55,
            ))
            changed += 1

    return changed

def layout_rois(gray: np.ndarray, expected: int) -> List[Tuple[int,int,int,int,str]]:
    rois = []
    for x0, y0, cw, ch, cell_name, _, _ in split_into_cells(gray, expected):
        rx0 = x0 + int(0.20 * cw)
        ry0 = y0 + int(0.18 * ch)
        rx1 = x0 + int(0.96 * cw)
        ry1 = y0 + int(0.96 * ch)
        rois.append((rx0, ry0, rx1 - rx0, ry1 - ry0, f"{cell_name}_chip"))

        bx0 = x0 + int(0.38 * cw)
        by0 = y0 + int(0.42 * ch)
        bx1 = x0 + int(0.97 * cw)
        by1 = y0 + int(0.98 * ch)
        rois.append((bx0, by0, bx1 - bx0, by1 - by0, f"{cell_name}_br"))

        qx0 = x0 + int(0.52 * cw)
        qy0 = y0 + int(0.52 * ch)
        qx1 = x0 + int(0.90 * cw)
        qy1 = y0 + int(0.90 * ch)
        rois.append((qx0, qy0, qx1 - qx0, qy1 - qy0, f"{cell_name}_qrCorner"))
    return rois


def layout_rescue_rois(gray: np.ndarray, expected: int) -> List[Tuple[int,int,int,int,str]]:
    H, W = gray.shape[:2]
    rois = []
    seen = set()
    overlap = 0.12

    def add_grid(cols: int, rows: int, tag: str):
        cell_w = W / max(cols, 1)
        cell_h = H / max(rows, 1)
        pad_w = int(cell_w * overlap)
        pad_h = int(cell_h * overlap)
        for r in range(rows):
            for c in range(cols):
                x0 = max(0, int(c * cell_w) - pad_w)
                y0 = max(0, int(r * cell_h) - pad_h)
                x1 = min(W, int((c + 1) * cell_w) + pad_w)
                y1 = min(H, int((r + 1) * cell_h) + pad_h)
                cw = x1 - x0
                ch = y1 - y0
                if cw < 60 or ch < 60:
                    continue

                candidates = [
                    (x0 + int(0.18 * cw), y0 + int(0.14 * ch), x0 + int(0.98 * cw), y0 + int(0.98 * ch), f"{tag}_{cols}x{rows}_{r}_{c}_chip"),
                    (x0 + int(0.56 * cw), y0 + int(0.56 * ch), x0 + int(0.995 * cw), y0 + int(0.995 * ch), f"{tag}_{cols}x{rows}_{r}_{c}_qrCorner"),
                ]
                for ax0, ay0, ax1, ay1, label in candidates:
                    ax0 = max(0, min(ax0, W - 1))
                    ay0 = max(0, min(ay0, H - 1))
                    ax1 = max(ax0 + 1, min(ax1, W))
                    ay1 = max(ay0 + 1, min(ay1, H))
                    key = (ax0, ay0, ax1, ay1)
                    if key in seen:
                        continue
                    seen.add(key)
                    rois.append((ax0, ay0, ax1 - ax0, ay1 - ay0, label))

    rows, cols = infer_grid_shape(expected, W, H)
    add_grid(cols, rows, "layoutA")
    if cols != rows:
        add_grid(rows, cols, "layoutB")
    if expected == 4:
        add_grid(3, 2, "layoutC")
        add_grid(2, 3, "layoutD")
    if expected >= 6:
        add_grid(2, 2, "layoutC")

    return rois[:18]


# -------------------------
# NEW: perspective warp decode using OpenCV detect() points
# -------------------------
def warp_from_points(gray: np.ndarray, pts: np.ndarray, out_size: int = 420) -> np.ndarray:
    # pts shape (4,2) float32 expected order not guaranteed -> sort by angle around center
    c = pts.mean(axis=0)
    angles = np.arctan2(pts[:,1]-c[1], pts[:,0]-c[0])
    order = np.argsort(angles)
    p = pts[order].astype(np.float32)

    # build consistent order: tl, tr, br, bl by y/x heuristic
    # after angle sort, re-map to start at top-left
    s = p.sum(axis=1)
    tl_idx = np.argmin(s)
    p = np.roll(p, -tl_idx, axis=0)

    dst = np.array([
        [0,0],
        [out_size-1,0],
        [out_size-1,out_size-1],
        [0,out_size-1]
    ], dtype=np.float32)

    M = cv2.getPerspectiveTransform(p, dst)
    return cv2.warpPerspective(gray, M, (out_size, out_size), flags=cv2.INTER_CUBIC)

def opencv_detect_warp_decode(roi_gray: np.ndarray) -> List[str]:
    """
    Cheap extra pass:
    - detect() gives points even if decode fails
    - warp to square
    - decode on warped with variants
    """
    out = []
    try:
        det = cv2.QRCodeDetector()
        ok, pts = det.detect(roi_gray)
        if not ok or pts is None:
            return out
        pts = pts.reshape(-1, 2)
        if pts.shape[0] != 4:
            return out

        warped = warp_from_points(roi_gray, pts, out_size=460)

        # variants on warped
        variants = []
        variants.append(warped)
        try:
            variants.append(cv2.createCLAHE(clipLimit=6.0, tileGridSize=(8,8)).apply(warped))
        except Exception:
            pass
        try:
            variants.append(unsharp(warped, amount=1.4, radius=1.0))
        except Exception:
            pass
        try:
            variants.append(blackhat_enhance(warped))
        except Exception:
            pass
        try:
            variants.append(255 - warped)  # invert
        except Exception:
            pass

        for v in variants:
            vq = add_quiet_zone(v, pad_ratio=0.15)
            for txt, _ in decode_multi(vq):
                if txt and is_valid_qr(txt):
                    out.append(txt)
    except Exception:
        pass

    # dedupe keep order
    uniq = []
    for t in out:
        if t not in uniq:
            uniq.append(t)
    return uniq


def opencv_detectmulti_candidates(gray: np.ndarray) -> List[np.ndarray]:
    pts_list = []
    try:
        det = cv2.QRCodeDetector()
        ok, points = det.detectMulti(gray)
        if ok and points is not None:
            for p in points:
                if p is not None and len(p) == 4:
                    pts_list.append(np.array(p))
    except Exception:
        pass
    return pts_list[:Config.DETECT_MULTI_MAX_CANDIDATES]


def detect_finder_patterns_robust(gray: np.ndarray) -> List[Tuple[int, int, int, int]]:
    regions = []
    h, w = gray.shape
    try:
        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        grad_x = cv2.Sobel(blur, cv2.CV_16S, 1, 0, ksize=3)
        grad_y = cv2.Sobel(blur, cv2.CV_16S, 0, 1, ksize=3)
        mag = cv2.magnitude(grad_x.astype(float), grad_y.astype(float))
        mag = np.uint8(np.clip(mag / (mag.max() + 1e-6) * 255, 0, 255))
        _, th = cv2.threshold(mag, 40, 255, cv2.THRESH_BINARY)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel, iterations=2)
        contours, hier = cv2.findContours(th, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        if hier is not None:
            hier = hier[0]
            for i, cnt in enumerate(contours):
                area = cv2.contourArea(cnt)
                if area < Config.FINDER_MIN_AREA or area > Config.FINDER_MAX_AREA:
                    continue
                child = hier[i][2]
                if child >= 0:
                    grand = hier[child][2]
                    if grand >= 0:
                        x, y, cw, ch = cv2.boundingRect(cnt)
                        if min(cw, ch) == 0:
                            continue
                        if max(cw, ch) / min(cw, ch) > 2.5:
                            continue
                        pad = int(max(cw, ch) * Config.FINDER_PADDING)
                        x1 = max(0, x - pad)
                        y1 = max(0, y - pad)
                        x2 = min(w, x + cw + pad)
                        y2 = min(h, y + ch + pad)
                        regions.append((x1, y1, x2 - x1, y2 - y1))
    except Exception:
        pass

    if not regions:
        for thresh in Config.FINDER_THRESHOLDS:
            try:
                binary = cv2.adaptiveThreshold(
                    gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                    cv2.THRESH_BINARY_INV, thresh, 5
                )
                contours, hier = cv2.findContours(binary, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
                if hier is None:
                    continue
                hier = hier[0]
                for i, cnt in enumerate(contours):
                    area = cv2.contourArea(cnt)
                    if area < Config.FINDER_MIN_AREA or area > Config.FINDER_MAX_AREA:
                        continue
                    child = hier[i][2]
                    if child >= 0:
                        grand = hier[child][2]
                        if grand >= 0:
                            x, y, cw, ch = cv2.boundingRect(cnt)
                            if min(cw, ch) == 0:
                                continue
                            if max(cw, ch) / min(cw, ch) > 2.0:
                                continue
                            pad = int(max(cw, ch) * Config.FINDER_PADDING)
                            x1 = max(0, x - pad)
                            y1 = max(0, y - pad)
                            x2 = min(w, x + cw + pad)
                            y2 = min(h, y + ch + pad)
                            regions.append((x1, y1, x2 - x1, y2 - y1))
            except Exception:
                pass

    unique = []
    for reg in regions:
        cx = reg[0] + reg[2] // 2
        cy = reg[1] + reg[3] // 2
        dup = False
        for other in unique:
            ocx = other[0] + other[2] // 2
            ocy = other[1] + other[3] // 2
            if math.hypot(cx - ocx, cy - ocy) < 60:
                dup = True
                break
        if not dup:
            unique.append(reg)
    return unique[:Config.MAX_FINDER_REGIONS]


def find_contour_zones(gray: np.ndarray) -> List[Tuple[int, int, int, int]]:
    if not Config.USE_CONTOUR:
        return []

    zones = []
    try:
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 15, 5
        )
        kernel = np.ones((3, 3), np.uint8)
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if not (Config.CONTOUR_MIN_AREA < area < Config.CONTOUR_MAX_AREA):
                continue
            x, y, cw, ch = cv2.boundingRect(cnt)
            if min(cw, ch) == 0:
                continue
            if max(cw, ch) / min(cw, ch) > 3.0:
                continue
            margin = int(max(cw, ch) * Config.CONTOUR_MARGIN)
            x1 = max(0, x - margin)
            y1 = max(0, y - margin)
            x2 = min(gray.shape[1], x + cw + margin)
            y2 = min(gray.shape[0], y + ch + margin)
            zones.append((x1, y1, x2 - x1, y2 - y1))
            if len(zones) >= Config.CONTOUR_MAX_CANDIDATES:
                break
    except Exception:
        pass
    return zones


# -------------------------
# ROI decode (FAST + DEEP)
# -------------------------
def decode_roi_push(wechat, roi_gray: np.ndarray, ox: int, oy: int, label: str,
                    patches: List[QRPatch], deep: bool,
                    max_zoomed: int,
                    stop_after_first: bool = False,
                    deadline=None):
    if roi_gray.size == 0:
        return 0
    if deadline and deadline():
        return 0

    found_count = 0

    # add quiet zone early (helps a lot)
    pad_ratio = 0.16 if not deep else 0.20
    pad = quiet_zone_pad(roi_gray, pad_ratio)
    base = add_quiet_zone(roi_gray, pad_ratio=pad_ratio)

    # Base variants
    variants = []
    variants.append(("raw", base))
    max_dim = max(base.shape[:2])
    try:
        variants.append(("clahe", cv2.createCLAHE(clipLimit=3.5 if not deep else 7.0, tileGridSize=(8,8)).apply(base)))
    except Exception:
        pass
    if deep and max_dim <= 180:
        try:
            variants.append(("sharp", unsharp(base, amount=1.5, radius=1.1)))
        except Exception:
            pass
    if deep:
        try:
            variants.append(("invert", 255 - base))
        except Exception:
            pass
        try:
            variants.append(("blackhat", blackhat_enhance(base)))
        except Exception:
            pass
        try:
            variants.append(("otsu", cv2.threshold(base, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]))
        except Exception:
            pass

    # Dynamic scales: keep the base path cheap and let deeper profiles spend the heavier budget later.
    if deep:
        if max_dim <= 90:
            scales = [4.0, 6.0]
        elif max_dim <= 160:
            scales = [3.0, 4.0]
        elif max_dim <= 320:
            scales = [2.0, 3.0]
        else:
            scales = [1.5, 2.0]
    else:
        if max_dim <= 90:
            scales = [3.0, 4.0]
        elif max_dim <= 160:
            scales = [2.0, 3.0]
        elif max_dim <= 320:
            scales = [2.0]
        else:
            scales = [1.5]

    # 1) WeChat on scaled ROIs (fast win)
    if wechat is not None:
        for sc in scales:
            if deadline and deadline():
                return found_count
            try:
                up = cv2.resize(base, (0,0), fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
                if max(up.shape[:2]) > max_zoomed:
                    continue
                up_bgr = cv2.cvtColor(up, cv2.COLOR_GRAY2BGR)
                res, pts = wechat.detectAndDecode(up_bgr)
                if not res:
                    continue
                for i, txt in enumerate(res):
                    if deadline and deadline():
                        return found_count
                    if not (txt and is_valid_qr(txt)):
                        continue
                    bbox = (ox, oy, roi_gray.shape[1], roi_gray.shape[0])
                    points = []
                    if pts is not None and len(pts) > i:
                        points = map_qr_points(pts[i], ox=ox, oy=oy, scale=sc, pad=pad,
                                               limit_w=roi_gray.shape[1], limit_h=roi_gray.shape[0])
                        bbox = bbox_from_points(points, bbox)
                    if not merge_or_append_patch(patches, QRPatch(
                        data=parse_qr(txt),
                        bbox=bbox,
                        points=points,
                        source="wechat_roi",
                        stage=f"{label}_s{sc}",
                        confidence=0.99
                    )):
                        continue
                    found_count += 1
                    if stop_after_first:
                        return found_count
            except Exception:
                pass

    # 2) decode_multi on variants + scales
    for vname, vimg in variants:
        if deadline and deadline():
            return found_count
        for sc in scales:
            if deadline and deadline():
                return found_count
            try:
                up = cv2.resize(vimg, (0,0), fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
                if max(up.shape[:2]) > max_zoomed:
                    continue
                for txt, rect in decode_multi(up):
                    if not (txt and is_valid_qr(txt)):
                        continue
                    x = ox + max(0, int(rect[0] / sc) - pad)
                    y = oy + max(0, int(rect[1] / sc) - pad)
                    w = max(1, int(rect[2] / sc))
                    h = max(1, int(rect[3] / sc))
                    if not merge_or_append_patch(patches, QRPatch(
                        data=parse_qr(txt),
                        bbox=(x,y,w,h),
                        source="decode_roi",
                        stage=f"{label}_{vname}_s{sc}",
                        confidence=0.94 if not deep else 0.965
                    )):
                        continue
                    found_count += 1
                    if stop_after_first:
                        return found_count
            except Exception:
                pass

        # 3) NEW warp decode (cheap, helps glare/tilt) â€” only deep
        if False and found_count == 0:
            micro_scales = [5.0, 7.0] if not deep else [5.0, 7.0, 9.0]
            for sc in micro_scales:
                try:
                    up = cv2.resize(vimg, (0,0), fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
                    if max(up.shape[:2]) > max_zoomed:
                        continue
                    for mvname, mimg in strong_micro_preprocess(up):
                        for txt, rect in decode_multi(mimg):
                            if not (txt and is_valid_qr(txt)):
                                continue
                            if any(p.data.raw == txt for p in patches):
                                continue
                            patches.append(QRPatch(
                                data=parse_qr(txt),
                                bbox=(
                                    ox + int(rect[0] / sc),
                                    oy + int(rect[1] / sc),
                                    max(1, int(rect[2] / sc)),
                                    max(1, int(rect[3] / sc)),
                                ),
                                source="micro_roi",
                                stage=f"{label}_{mvname}_s{sc}",
                                confidence=0.945 if not deep else 0.97
                            ))
                            found_count += 1
                            if stop_after_first:
                                return found_count
                except Exception:
                    pass

        if deep:
            if deadline and deadline():
                return found_count
            for t in opencv_detect_warp_decode(vimg):
                if deadline and deadline():
                    return found_count
                if not merge_or_append_patch(patches, QRPatch(
                    data=parse_qr(t),
                    bbox=(ox, oy, roi_gray.shape[1], roi_gray.shape[0]),
                    source="warp_roi",
                    stage=f"{label}_{vname}",
                    confidence=0.90
                )):
                    continue
                found_count += 1
                if stop_after_first:
                    return found_count

    return found_count


def decode_targeted_cells(gray: np.ndarray, expected: int,
                          patches: List[QRPatch], deadline,
                          panel_type=None) -> int:
    if gray is None or gray.size == 0:
        return 0

    wechat = get_wechat_detector()
    real_patches = [p for p in deduplicate(patches) if is_real_count_patch(p)]
    occupied = _occupied_cell_keys(gray, expected, real_patches)
    found = 0

    cells = list(split_into_cells(gray, expected))
    if expected <= 4:
        cells.sort(key=lambda item: (-item[5], item[6]))

    for x0, y0, cw, ch, cell_name, r, c in cells:
        if deadline():
            break
        if (r, c) in occupied:
            continue

        cell = gray[y0:y0 + ch, x0:x0 + cw]
        if cell.size == 0 or cell.shape[0] < 24 or cell.shape[1] < 24:
            continue

        zx, zy, zw, zh = crop_target_qr_zone(cell, panel_type=panel_type)
        roi = cell[zy:zy + zh, zx:zx + zw]
        if roi.size == 0 or roi.shape[0] < 18 or roi.shape[1] < 18:
            continue

        before = len(patches)
        found += decode_roi_push(
            wechat, roi, x0 + zx, y0 + zy, f"TC_{cell_name}",
            patches, deep=False,
            max_zoomed=min(Config.MAX_ZOOMED_SIZE_FAST, 3000),
            stop_after_first=True,
            deadline=deadline
        )
        if len(patches) > before:
            occupied.add((r, c))
            continue

        try:
            pad = quiet_zone_pad(roi, 0.12)
            padded = add_quiet_zone(roi, pad_ratio=0.12)
            for txt, rect in decode_multi(padded):
                if not (txt and is_valid_qr(txt)):
                    continue
                if not merge_or_append_patch(patches, QRPatch(
                    data=parse_qr(txt),
                    bbox=(
                        x0 + zx + max(0, int(rect[0]) - pad),
                        y0 + zy + max(0, int(rect[1]) - pad),
                        max(1, int(rect[2])),
                        max(1, int(rect[3])),
                    ),
                    source="targeted_cell_direct",
                    stage=f"TC_{cell_name}_direct",
                    confidence=0.92,
                )):
                    continue
                occupied.add((r, c))
                found += 1
                break
        except Exception:
            pass

        if (r, c) in occupied:
            continue

        try:
            roi_bgr = cv2.cvtColor(add_quiet_zone(roi, pad_ratio=0.12), cv2.COLOR_GRAY2BGR)
            for text in try_decode_variants(roi_bgr):
                if not (text and is_valid_qr(text)):
                    continue
                if not merge_or_append_patch(patches, QRPatch(
                    data=parse_qr(text),
                    bbox=(x0 + zx, y0 + zy, zw, zh),
                    source="targeted_cell_tryvar",
                    stage=f"TC_{cell_name}_tryvar",
                    confidence=0.90,
                )):
                    continue
                occupied.add((r, c))
                found += 1
                break
        except Exception:
            pass

        if (r, c) in occupied or expected > 4:
            continue

        side = panel_type or _module_side(cell)
        if side == "left":
            extra_specs = [
                ("legacy_br", 0.42, 0.40, 0.99, 0.99),
                ("qr_bl", 0.00, 0.46, 0.64, 1.00),
                ("qr_br", 0.48, 0.50, 1.00, 1.00),
            ]
        elif side == "right":
            extra_specs = [
                ("legacy_br", 0.42, 0.40, 0.99, 0.99),
                ("qr_br", 0.48, 0.50, 1.00, 1.00),
                ("qr_bl", 0.00, 0.46, 0.64, 1.00),
            ]
        else:
            extra_specs = [
                ("qr_bl", 0.00, 0.46, 0.64, 1.00),
                ("legacy_br", 0.42, 0.40, 0.99, 0.99),
                ("qr_br", 0.48, 0.50, 1.00, 1.00),
            ]

        for zone_name, xr1, yr1, xr2, yr2 in extra_specs:
            if deadline():
                break
            ex0 = max(0, int(cw * xr1))
            ey0 = max(0, int(ch * yr1))
            ex1 = min(cw, int(cw * xr2))
            ey1 = min(ch, int(ch * yr2))
            if ex1 <= ex0 or ey1 <= ey0:
                continue

            extra_roi = cell[ey0:ey1, ex0:ex1]
            if extra_roi.size == 0 or extra_roi.shape[0] < 18 or extra_roi.shape[1] < 18:
                continue

            try:
                pad = quiet_zone_pad(extra_roi, 0.12)
                padded = add_quiet_zone(extra_roi, pad_ratio=0.12)
                for txt, rect in decode_multi(padded):
                    if not (txt and is_valid_qr(txt)):
                        continue
                    if not merge_or_append_patch(patches, QRPatch(
                        data=parse_qr(txt),
                        bbox=(
                            x0 + ex0 + max(0, int(rect[0]) - pad),
                            y0 + ey0 + max(0, int(rect[1]) - pad),
                            max(1, int(rect[2])),
                            max(1, int(rect[3])),
                        ),
                        source="targeted_cell_extra",
                        stage=f"TC_{cell_name}_{zone_name}_direct",
                        confidence=0.91,
                    )):
                        continue
                    occupied.add((r, c))
                    found += 1
                    break
            except Exception:
                pass

            if (r, c) in occupied:
                break

            try:
                roi_bgr = cv2.cvtColor(add_quiet_zone(extra_roi, pad_ratio=0.12), cv2.COLOR_GRAY2BGR)
                for text in try_decode_variants(roi_bgr):
                    if not (text and is_valid_qr(text)):
                        continue
                    if not merge_or_append_patch(patches, QRPatch(
                        data=parse_qr(text),
                        bbox=(x0 + ex0, y0 + ey0, ex1 - ex0, ey1 - ey0),
                        source="targeted_cell_extra",
                        stage=f"TC_{cell_name}_{zone_name}_tryvar",
                        confidence=0.89,
                    )):
                        continue
                    occupied.add((r, c))
                    found += 1
                    break
            except Exception:
                pass

            if (r, c) in occupied:
                break

    return found


def _decode_peer_template_windows(wechat, gray: np.ndarray, expected: int,
                                  cell: np.ndarray, x0: int, y0: int,
                                  cell_name: str, r: int, c: int,
                                  patches: List[QRPatch], deep: bool = False,
                                  deadline=None) -> int:
    templates = _peer_qr_templates(gray, expected, patches)
    template = templates.get("by_col", {}).get(c) or templates.get("global")
    if not template:
        return 0

    cw = cell.shape[1]
    ch = cell.shape[0]
    nx, ny, nw, nh = template
    base_x = int(nx * cw)
    base_y = int(ny * ch)
    base_w = max(24, int(nw * cw))
    base_h = max(24, int(nh * ch))
    grows = (0.72, 0.86, 1.0, 1.18, 1.35) if deep else (0.82, 1.0, 1.18)
    shifts = [
        (-0.05, -0.05), (0.0, -0.05), (0.05, -0.05),
        (-0.05, 0.0), (0.0, 0.0), (0.05, 0.0),
        (-0.05, 0.05), (0.0, 0.05), (0.05, 0.05),
    ] if deep else [(-0.02, 0.0), (0.0, 0.0), (0.02, 0.0), (0.0, 0.02)]

    for grow_idx, grow in enumerate(grows):
        if deadline and deadline():
            return 0
        qw = max(24, int(base_w * grow))
        qh = max(24, int(base_h * grow))
        for shift_idx, (sx, sy) in enumerate(shifts):
            if deadline and deadline():
                return 0
            qx = max(0, min(cw - 1, int(base_x + sx * cw - (qw - base_w) / 2)))
            qy = max(0, min(ch - 1, int(base_y + sy * ch - (qh - base_h) / 2)))
            qw2 = max(1, min(qw, cw - qx))
            qh2 = max(1, min(qh, ch - qy))
            if qw2 < 18 or qh2 < 18:
                continue
            roi = cell[qy:qy + qh2, qx:qx + qw2]
            if roi.size == 0:
                continue
            before = len(patches)
            decode_roi_push(
                wechat, roi, x0 + qx, y0 + qy,
                f"PT_{cell_name}_{grow_idx}_{shift_idx}",
                patches,
                deep=deep,
                max_zoomed=Config.MAX_ZOOMED_SIZE_DEEP if deep else max(Config.MAX_ZOOMED_SIZE_FAST, 3600),
                stop_after_first=True,
                deadline=deadline,
            )
            if len(patches) > before:
                return 1
            try:
                roi_bgr = cv2.cvtColor(add_quiet_zone(roi, pad_ratio=0.14), cv2.COLOR_GRAY2BGR)
                for text in try_decode_variants(roi_bgr):
                    if not (text and is_valid_qr(text)):
                        continue
                    if not merge_or_append_patch(patches, QRPatch(
                        data=parse_qr(text),
                        bbox=(x0 + qx, y0 + qy, qw2, qh2),
                        source="peer_template",
                        stage=f"PT_{cell_name}_{grow_idx}_{shift_idx}_tryvar",
                        confidence=0.91 if not deep else 0.94,
                    )):
                        continue
                    return 1
            except Exception:
                pass
    return 0


def _decode_module_local_windows(wechat, cell: np.ndarray, x0: int, y0: int,
                                 cell_name: str, patches: List[QRPatch],
                                 expected: int, panel_type=None, deadline=None) -> int:
    if cell is None or cell.size == 0:
        return 0

    base_side = panel_type or _module_side(cell)
    side_candidates = [base_side]
    if base_side == 'left':
        side_candidates.append('right')
    elif base_side == 'right':
        side_candidates.append('left')
    else:
        side_candidates.extend(['left', 'right'])

    max_zoomed = 5000 if expected >= 10 else 4200
    scales = (4.0, 6.0, 8.0) if expected <= 4 else ((4.0, 6.0, 8.0) if expected >= 10 else (4.0, 6.0))
    if expected <= 4:
        preferred_variants = {"raw", "clahe8_sharp", "otsu", "raw_inv", "clahe8_sharp_inv"}
        qr_size_order = [0.19, 0.23, 0.28]
        shift_order = [(-0.05, -0.05), (0.0, -0.05), (0.04, -0.05), (-0.03, 0.0), (0.0, 0.0), (0.04, 0.0), (0.0, 0.04)]
    else:
        preferred_variants = {"raw", "clahe8_sharp", "norm", "norm_sharp", "norm_otsu", "otsu", "raw_inv"}
        qr_size_order = [0.19, 0.23]
        shift_order = [(-0.05, -0.05), (0.0, -0.05), (0.04, -0.05), (-0.03, 0.0), (0.0, 0.0), (0.04, 0.0), (0.0, 0.04)]

    for side_idx, side_name in enumerate(side_candidates):
        if deadline and deadline():
            return 0
        zx, zy, zw, zh = crop_target_qr_zone(cell, panel_type=side_name)
        target = cell[zy:zy + zh, zx:zx + zw]
        if target.size == 0 or target.shape[0] < 20 or target.shape[1] < 20:
            continue

        th, tw = target.shape[:2]
        probe_windows = []
        for ratio_idx, qr_ratio in enumerate(qr_size_order):
            qr_size = max(24, int(min(tw, th) * qr_ratio))
            base_x = max(0, tw - qr_size - max(4, int(tw * 0.08)))
            base_y = max(0, th - qr_size - max(4, int(th * 0.10)))
            for shift_idx, (sx, sy) in enumerate(shift_order):
                qx = max(0, min(tw - qr_size, int(base_x + sx * tw)))
                qy = max(0, min(th - qr_size, int(base_y + sy * th)))
                probe_windows.append((
                    f"qr_probe_{side_name}_{ratio_idx}_{shift_idx}",
                    qx,
                    qy,
                    min(tw, qx + qr_size),
                    min(th, qy + qr_size),
                ))
                if expected > 4 or shift_idx < 2:
                    probe_windows.append((
                        f"qr_probe_wide_{side_name}_{ratio_idx}_{shift_idx}",
                        max(0, qx - int(tw * 0.04)),
                        max(0, qy - int(th * 0.04)),
                        min(tw, qx + qr_size + int(tw * 0.05)),
                        min(th, qy + qr_size + int(th * 0.05)),
                    ))
        if expected <= 4:
            # Glare-heavy 4-QR panels often decode first on the module mid-wide crop.
            # Try that before full-target crops to avoid spending the whole budget on
            # less useful windows.
            module_windows = [
                (f"mod_mid_wide_{side_name}", int(tw * 0.18), int(th * 0.24), tw, th),
                (f"target_wide_{side_name}", 0, 0, tw, min(th, int(th * 1.00))),
                (f"target_{side_name}", 0, 0, tw, th),
                (f"mod_mid_{side_name}", int(tw * 0.25), int(th * 0.30), int(tw * 0.95), int(th * 0.95)),
                (f"mod_br_{side_name}", int(tw * 0.45), int(th * 0.45), tw, th),
                (f"mod_br_wide_{side_name}", int(tw * 0.36), int(th * 0.36), tw, th),
            ] + probe_windows
        else:
            module_windows = [
                (f"target_{side_name}", 0, 0, tw, th),
                (f"mod_mid_{side_name}", int(tw * 0.25), int(th * 0.30), int(tw * 0.95), int(th * 0.95)),
                (f"mod_br_{side_name}", int(tw * 0.45), int(th * 0.45), tw, th),
            ] + probe_windows

        for zone_name, mx0, my0, mx1, my1 in module_windows:
            if deadline and deadline():
                return 0
            mx0 = max(0, mx0)
            my0 = max(0, my0)
            mx1 = min(tw, mx1)
            my1 = min(th, my1)
            if mx1 <= mx0 or my1 <= my0:
                continue

            roi = target[my0:my1, mx0:mx1]
            if roi.size == 0 or roi.shape[0] < 18 or roi.shape[1] < 18:
                continue

            pad = quiet_zone_pad(roi, 0.18)
            base = add_quiet_zone(roi, pad_ratio=0.18)
            for sc in scales:
                actual_sc = min(sc, max_zoomed / float(max(base.shape[:2])))
                if actual_sc < 2.0:
                    continue

                try:
                    up = cv2.resize(base, (0, 0), fx=actual_sc, fy=actual_sc, interpolation=cv2.INTER_CUBIC)
                except Exception:
                    continue

                if wechat is not None:
                    try:
                        res, pts = wechat.detectAndDecode(cv2.cvtColor(up, cv2.COLOR_GRAY2BGR))
                    except Exception:
                        res, pts = [], None
                    if res:
                        for i, txt in enumerate(res):
                            if not (txt and is_valid_qr(txt)):
                                continue
                            bbox = (x0 + zx + mx0, y0 + zy + my0, mx1 - mx0, my1 - my0)
                            points = []
                            if pts is not None and len(pts) > i:
                                points = map_qr_points(pts[i], ox=x0 + zx + mx0, oy=y0 + zy + my0,
                                                       scale=actual_sc, pad=pad,
                                                       limit_w=roi.shape[1], limit_h=roi.shape[0])
                                bbox = bbox_from_points(points, bbox)
                            if not merge_or_append_patch(patches, QRPatch(
                                data=parse_qr(txt),
                                bbox=bbox,
                                points=points,
                                source="module_local",
                                stage=f"MOD_{cell_name}_{zone_name}_s{actual_sc:.1f}",
                                confidence=0.96 - (0.01 * side_idx),
                            )):
                                continue
                            return 1

                for vname, vimg in strong_micro_preprocess(up):
                    if deadline and deadline():
                        return 0
                    if vname not in preferred_variants:
                        continue
                    try:
                        decs = decode_multi(vimg)
                    except Exception:
                        decs = []
                    for txt, rect in decs:
                        if not (txt and is_valid_qr(txt)):
                            continue
                        if not merge_or_append_patch(patches, QRPatch(
                            data=parse_qr(txt),
                            bbox=(
                                x0 + zx + mx0 + max(0, int(rect[0] / actual_sc) - pad),
                                y0 + zy + my0 + max(0, int(rect[1] / actual_sc) - pad),
                                max(1, int(rect[2] / actual_sc)),
                                max(1, int(rect[3] / actual_sc)),
                            ),
                            source="module_local",
                            stage=f"MOD_{cell_name}_{zone_name}_{vname}_s{actual_sc:.1f}",
                            confidence=0.93 - (0.01 * side_idx),
                        )):
                            continue
                        return 1

    return 0


def rescue_failed_cells(gray: np.ndarray, expected: int,
                        patches: List[QRPatch], deadline,
                        panel_type=None) -> int:
    if gray is None or gray.size == 0 or expected <= 1:
        return 0

    wechat = get_wechat_detector()
    real_patches = [p for p in deduplicate(patches) if is_real_count_patch(p)]
    occupied = _occupied_cell_keys(gray, expected, real_patches)
    found = 0

    cells = list(split_into_cells(gray, expected))
    if expected in {4, 6, 8, 10}:
        # The lower rows contain the most reliable recoverable QR crops in the
        # current PCB family. Scan them first so bounded profiles do not spend
        # the whole tail budget on faint top cells.
        cells.sort(key=lambda item: (-item[5], item[6]))

    for x0, y0, cw, ch, cell_name, r, c in cells:
        if deadline():
            break
        if (r, c) in occupied:
            continue

        cell = gray[y0:y0 + ch, x0:x0 + cw]
        if cell.size == 0 or cell.shape[0] < 24 or cell.shape[1] < 24:
            continue

        cell_started = time.time()
        # Some glare-heavy 4-QR cells need a little longer for the
        # module-local decoder, but keep larger panels bounded per cell.
        cell_budget_s = 26.0 if expected <= 4 else 14.0
        def cell_deadline() -> bool:
            return deadline() or ((time.time() - cell_started) >= cell_budget_s)

        if expected <= 4:
            before = len(patches)
            found += _decode_module_local_windows(
                wechat, cell, x0, y0, cell_name, patches, expected,
                panel_type=panel_type,
                deadline=cell_deadline,
            )
            if len(patches) > before:
                occupied.add((r, c))
                continue

            before = len(patches)
            found += _decode_peer_template_windows(
                wechat, gray, expected, cell, x0, y0, cell_name, r, c, patches,
                deep=False,
                deadline=cell_deadline,
            )
            if len(patches) > before:
                occupied.add((r, c))
                continue

        else:
            # For 6/10 PCB panels, the module-local QR crop is much more reliable
            # than peer-template search and cheaper. Run it first so bounded scans
            # do not spend the whole cell budget on empty template windows.
            before = len(patches)
            found += _decode_module_local_windows(
                wechat, cell, x0, y0, cell_name, patches, expected,
                panel_type=panel_type,
                deadline=cell_deadline,
            )
            if len(patches) > before:
                occupied.add((r, c))
                continue

            before = len(patches)
            found += _decode_peer_template_windows(
                wechat, gray, expected, cell, x0, y0, cell_name, r, c, patches,
                deep=True,
                deadline=cell_deadline,
            )
            if len(patches) > before:
                occupied.add((r, c))
                continue

        side = panel_type or _module_side(cell)
        if expected >= 6:
            if side == "left":
                zone_specs = [
                    ("target_left", 0.08, 0.16, 0.76, 0.96, False, 3000),
                    ("qr_br", 0.48, 0.50, 1.00, 1.00, True, 3400),
                    ("module_wide", 0.16, 0.16, 0.96, 0.97, False, 3000),
                ]
            elif side == "right":
                zone_specs = [
                    ("target_right", 0.22, 0.16, 0.92, 0.96, False, 3000),
                    ("qr_br", 0.48, 0.50, 1.00, 1.00, True, 3400),
                    ("module_wide", 0.16, 0.16, 0.96, 0.97, False, 3000),
                ]
            else:
                zone_specs = [
                    ("module_left", 0.10, 0.16, 0.78, 0.96, False, 3000),
                    ("module_right", 0.22, 0.16, 0.92, 0.96, False, 3000),
                    ("qr_br", 0.48, 0.50, 1.00, 1.00, True, 3400),
                ]
        else:
            if side == "left":
                zone_specs = [
                    ("target_left", 0.08, 0.16, 0.76, 0.96, False, 3200),
                    ("legacy_br", 0.42, 0.40, 0.99, 0.99, True, 3800),
                    ("qr_bl", 0.00, 0.46, 0.64, 1.00, True, 3600),
                    ("module_wide", 0.16, 0.16, 0.96, 0.97, False, 3200),
                    ("qr_br", 0.48, 0.50, 1.00, 1.00, True, 3600),
                ]
            elif side == "right":
                zone_specs = [
                    ("target_right", 0.22, 0.16, 0.92, 0.96, False, 3200),
                    ("legacy_br", 0.42, 0.40, 0.99, 0.99, True, 3800),
                    ("qr_br", 0.48, 0.50, 1.00, 1.00, True, 3600),
                    ("module_wide", 0.16, 0.16, 0.96, 0.97, False, 3200),
                    ("qr_bl", 0.00, 0.46, 0.64, 1.00, True, 3600),
                ]
            else:
                zone_specs = [
                    ("module_wide", 0.16, 0.16, 0.96, 0.97, False, 3200),
                    ("legacy_br", 0.42, 0.40, 0.99, 0.99, True, 3800),
                    ("qr_br", 0.48, 0.50, 1.00, 1.00, True, 3600),
                    ("qr_bl", 0.00, 0.46, 0.64, 1.00, True, 3600),
                    ("module_left", 0.10, 0.16, 0.78, 0.96, False, 3200),
                    ("module_right", 0.22, 0.16, 0.92, 0.96, False, 3200),
                ]

        cell_found = False
        for zone_name, xr1, yr1, xr2, yr2, use_deep, max_zoomed in zone_specs:
            if deadline():
                break

            zx0 = max(0, int(cw * xr1))
            zy0 = max(0, int(ch * yr1))
            zx1 = min(cw, int(cw * xr2))
            zy1 = min(ch, int(ch * yr2))
            if zx1 <= zx0 or zy1 <= zy0:
                continue

            roi = cell[zy0:zy1, zx0:zx1]
            if roi.size == 0 or roi.shape[0] < 18 or roi.shape[1] < 18:
                continue

            before = len(patches)
            found += decode_roi_push(
                wechat, roi, x0 + zx0, y0 + zy0, f"FAIL_{cell_name}_{zone_name}",
                patches, deep=use_deep,
                max_zoomed=max_zoomed,
                stop_after_first=True,
                deadline=deadline
            )
            if len(patches) > before:
                occupied.add((r, c))
                cell_found = True
                break

            if deadline():
                break

            try:
                padded = add_quiet_zone(roi, pad_ratio=0.12)
                roi_bgr = cv2.cvtColor(padded, cv2.COLOR_GRAY2BGR)
                for text in try_decode_variants(roi_bgr):
                    if not (text and is_valid_qr(text)):
                        continue
                    if not merge_or_append_patch(patches, QRPatch(
                        data=parse_qr(text),
                        bbox=(x0 + zx0, y0 + zy0, zx1 - zx0, zy1 - zy0),
                        source="failed_cell_tryvar",
                        stage=f"FAIL_{cell_name}_{zone_name}_tryvar",
                        confidence=0.89,
                    )):
                        continue
                    occupied.add((r, c))
                    found += 1
                    cell_found = True
                    break
            except Exception:
                pass

            if cell_found:
                break

        if cell_found:
            continue

    return found


# -------------------------
# Grid scanning (last resort)
# -------------------------
def scan_grid_cell(args):
    gray, grid_size, zoom, i, j, overlap, max_zoomed, deep = args
    h,w = gray.shape
    cell_h = h // grid_size
    cell_w = w // grid_size
    pad_h = int(cell_h * overlap)
    pad_w = int(cell_w * overlap)

    y1 = max(0, i*cell_h - pad_h)
    x1 = max(0, j*cell_w - pad_w)
    y2 = min(h, (i+1)*cell_h + pad_h)
    x2 = min(w, (j+1)*cell_w + pad_w)

    roi = gray[y1:y2, x1:x2]
    if roi.size == 0 or roi.shape[0] < 24 or roi.shape[1] < 24:
        return []

    max_dim = max(roi.shape)
    actual_zoom = zoom
    if max_dim * actual_zoom > max_zoomed:
        actual_zoom = max(2, max_zoomed // max_dim)
    if actual_zoom < 2:
        return []

    try:
        up = cv2.resize(roi, (0,0), fx=actual_zoom, fy=actual_zoom, interpolation=cv2.INTER_CUBIC)
    except Exception:
        return []

    variants = [("raw", up)]
    try:
        variants.append(("clahe", cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8)).apply(up)))
    except Exception:
        pass
    try:
        variants.append(("otsu", cv2.threshold(up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]))
    except Exception:
        pass
    if deep:
        try:
            variants.append(("sharp", unsharp(up, amount=1.3, radius=1.0)))
        except Exception:
            pass
        try:
            variants.append(("invert", 255 - up))
        except Exception:
            pass

    results = []
    for name, proc in variants:
        pad = quiet_zone_pad(proc, 0.12)
        proc2 = add_quiet_zone(proc, pad_ratio=0.12)
        for txt, rect in decode_multi(proc2):
            if not is_valid_qr(txt):
                continue
            bx = x1 + max(0, int(rect[0] / actual_zoom) - int(pad / actual_zoom))
            by = y1 + max(0, int(rect[1] / actual_zoom) - int(pad / actual_zoom))
            bw = max(1, int(rect[2] / actual_zoom))
            bh = max(1, int(rect[3] / actual_zoom))
            results.append(QRPatch(
                data=parse_qr(txt),
                bbox=(bx,by,bw,bh),
                source="grid_deep" if deep else "grid_fast",
                stage=f"g{grid_size}_{name}_z{zoom}",
                confidence=0.86 if not deep else 0.90
            ))
    return results


# -------------------------
# Processor
# -------------------------
class QRProcessor:
    def _finalize(self, patches, t0, filename, path):
        patches = deduplicate(patches)
        patches.sort(key=lambda p: (p.bbox[1], p.bbox[0]))
        for idx, p in enumerate(patches, 1):
            p.id = idx
        real_count = sum(1 for p in patches if is_real_count_patch(p))
        return ImageReport(filename, path, True, real_count, patches, time.time()-t0)

    def _count(self, patches: List[QRPatch]) -> int:
        return sum(1 for p in deduplicate(patches) if is_real_count_patch(p))

    def _missing(self, patches: List[QRPatch], expected: int) -> int:
        return max(0, expected - self._count(patches))

    def _add_patch(self, patches: List[QRPatch], text: str, bbox, source: str, stage: str,
                   confidence: float) -> bool:
        if not (text and is_valid_qr(text)):
            return False
        return merge_or_append_patch(patches, QRPatch(
            data=parse_qr(text),
            bbox=tuple(map(int, bbox)),
            source=source,
            stage=stage,
            confidence=confidence,
        ))

    def _decode_from_detectmulti(self, gray_scaled: np.ndarray, inv_scale: float,
                                 patches: List[QRPatch], stage_prefix: str, expired) -> int:
        if expired():
            return 0

        found = 0
        candidates = opencv_detectmulti_candidates(gray_scaled)
        if not candidates:
            return 0

        for pts in candidates:
            if expired():
                break
            warped = warp_from_points(gray_scaled, pts, out_size=360)
            if warped is None or warped.size == 0:
                continue

            pmin = pts.min(axis=0)
            pmax = pts.max(axis=0)

            for up_s in (1.0, 1.5, 2.0):
                if expired():
                    break
                try:
                    wimg = warped if up_s == 1.0 else cv2.resize(
                        warped, (0, 0), fx=up_s, fy=up_s, interpolation=cv2.INTER_CUBIC
                    )
                    for vname, vimg in strong_micro_preprocess(wimg):
                        if expired():
                            break
                        for text, _ in decode_multi(vimg):
                            if not (text and is_valid_qr(text)):
                                continue
                            if self._add_patch(
                                patches,
                                text,
                                (
                                    max(0, int(pmin[0] * inv_scale)),
                                    max(0, int(pmin[1] * inv_scale)),
                                    max(1, int((pmax[0] - pmin[0]) * inv_scale)),
                                    max(1, int((pmax[1] - pmin[1]) * inv_scale)),
                                ),
                                source="opencv_detectmulti",
                                stage=f"{stage_prefix}_{vname}_up{up_s}",
                                confidence=0.91,
                            ):
                                found += 1
                except Exception:
                    pass
        return found

    def _scan_regions(self, gray: np.ndarray, regions: List[Tuple[int, int, int, int]],
                      zoom_levels: List[int], preprocess_count: int, stage: str,
                      patches: List[QRPatch], expired):
        for (x, y, w, h) in regions:
            if expired():
                break
            if w <= 0 or h <= 0:
                continue

            x1, y1, x2, y2 = x, y, x + w, y + h
            roi = gray[y1:y2, x1:x2]
            if roi.size == 0:
                continue

            found_any = False
            for zoom in zoom_levels:
                if expired():
                    break
                max_dim = max(roi.shape)
                actual_zoom = zoom
                if max_dim * actual_zoom > Config.MAX_ZOOMED_SIZE_DEEP:
                    actual_zoom = max(2, Config.MAX_ZOOMED_SIZE_DEEP // max_dim)
                if actual_zoom < 2:
                    continue

                try:
                    nh = int(roi.shape[0] * actual_zoom)
                    nw = int(roi.shape[1] * actual_zoom)
                    roi_zoom = cv2.resize(roi, (nw, nh), interpolation=cv2.INTER_LINEAR)
                except Exception:
                    continue

                variants = get_preprocessing_variants(roi_zoom, max_variants=preprocess_count)
                for name, proc in variants:
                    if expired():
                        break
                    for text, (rx, ry, rw, rh) in decode_multi(proc):
                        if self._add_patch(
                            patches,
                            text,
                            (
                                x1 + int(rx / actual_zoom),
                                y1 + int(ry / actual_zoom),
                                max(1, int(rw / actual_zoom)),
                                max(1, int(rh / actual_zoom)),
                            ),
                            source=stage,
                            stage=name,
                            confidence=0.90,
                        ):
                            found_any = True
                if found_any:
                    break

            if found_any or expired():
                continue

            try:
                roi_bgr = cv2.cvtColor(roi, cv2.COLOR_GRAY2BGR)
                for text in try_decode_variants(roi_bgr):
                    if self._add_patch(
                        patches, text, (x1, y1, w, h),
                        source=stage, stage="tryvar", confidence=0.75,
                    ):
                        found_any = True
            except Exception:
                pass

    def _grid_tasks(self, gray: np.ndarray, grid_size: int, zoom: int, overlap: float,
                    max_zoomed: int, deep: bool):
        h, w = gray.shape
        cell_h = h // grid_size
        cell_w = w // grid_size
        tasks = []

        for i in range(grid_size):
            for j in range(grid_size):
                pad_h = int(cell_h * overlap)
                pad_w = int(cell_w * overlap)
                y1 = max(0, i * cell_h - pad_h)
                x1 = max(0, j * cell_w - pad_w)
                y2 = min(h, (i + 1) * cell_h + pad_h)
                x2 = min(w, (j + 1) * cell_w + pad_w)
                roi = gray[y1:y2, x1:x2]
                if roi.size == 0:
                    continue
                score = roi.shape[0] * roi.shape[1]
                tasks.append((score, (gray, grid_size, zoom, i, j, overlap, max_zoomed, deep)))

        tasks.sort(key=lambda item: item[0])
        return [task for _, task in tasks[:Config.MAX_GRID_CELLS_TOTAL]]

    def _run_grid_scan(self, gray: np.ndarray, expected: int, patches: List[QRPatch],
                       soft_time_up, hard_time_up, deep: bool):
        params = Config.GRID_DEEP if deep else Config.GRID_FAST
        cfg = params.get(expected, params[Config.DEFAULT_EXPECTED])
        grid_size = cfg["size"]
        zooms = cfg["zooms"]
        overlap = cfg["overlap"]
        max_zoomed = Config.MAX_ZOOMED_SIZE_DEEP if deep else Config.MAX_ZOOMED_SIZE_FAST

        H, W = gray.shape
        gray_up = gray
        scaled = False
        if max(H, W) < 1200:
            try:
                gray_up = cv2.resize(gray, (W * 2, H * 2), interpolation=cv2.INTER_LINEAR)
                scaled = True
            except Exception:
                gray_up = gray

        scale_div = 2 if scaled else 1

        for zoom in zooms:
            if hard_time_up() or (soft_time_up() and not deep):
                break
            tasks = self._grid_tasks(gray_up, grid_size, zoom, overlap, max_zoomed, deep)
            for task in tasks:
                if hard_time_up() or (soft_time_up() and not deep):
                    return
                for det in scan_grid_cell(task):
                    if scaled:
                        bx, by, bw, bh = det.bbox
                        det.bbox = (
                            max(0, int(bx / scale_div)),
                            max(0, int(by / scale_div)),
                            max(1, int(bw / scale_div)),
                            max(1, int(bh / scale_div)),
                        )
                    if not merge_or_append_patch(patches, det):
                        continue
                if self._count(patches) >= expected:
                    return

    def process(self, path: str) -> ImageReport:
        t0 = time.time()
        filename = os.path.basename(path)
        expected = guess_expected_qr(filename)

        img = cv2.imread(path)
        if img is None:
            return ImageReport(filename, path, False, 0, [], time.time()-t0, error="Cannot read image")

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        H, W = gray.shape

        # slight boost for dark images
        if float(np.mean(gray)) < 80:
            gray = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(8,8)).apply(gray)

        wechat = get_wechat_detector()
        patches: List[QRPatch] = []

        def elapsed():
            return time.time() - t0

        def hard_time_up():
            return elapsed() > Config.TIMEOUT_PER_IMAGE

        def soft_time_up():
            return elapsed() > Config.SOFT_BUDGET_PER_IMAGE

        def complete_locations_are_precise() -> bool:
            real = [p for p in deduplicate(patches) if is_real_count_patch(p)]
            if len(real) < expected:
                return False
            if expected <= 2:
                return True
            precise = 0
            for p in real:
                if getattr(p, "points", None) and len(p.points) >= 4:
                    precise += 1
                    continue
                x, y, w, h = p.bbox
                if w <= 0 or h <= 0:
                    continue
                ratio = max(w, h) / max(1, min(w, h))
                if ratio <= 1.75 and w <= W * 0.20 and h <= H * 0.20:
                    precise += 1
            return precise >= expected

        def finalize_report():
            # If the payload count is already complete and locations are usable,
            # do not spend extra seconds trying to relocalize the same QR codes.
            if self._count(patches) >= expected and complete_locations_are_precise():
                return self._finalize(patches, t0, filename, path)

            if not hard_time_up():
                refine_deadline = lambda: hard_time_up() or elapsed() > min(Config.SOFT_BUDGET_PER_IMAGE + 6, Config.TIMEOUT_PER_IMAGE - 2)
                if elapsed() <= min(Config.SOFT_BUDGET_PER_IMAGE + 4, Config.TIMEOUT_PER_IMAGE - 4):
                    refine_real_patch_positions(gray, expected, patches, deadline=refine_deadline)
                missing_now = self._missing(patches, expected)
                if expected <= 4 and missing_now <= 1 and elapsed() <= min(Config.SOFT_BUDGET_PER_IMAGE + 8, Config.TIMEOUT_PER_IMAGE - 1):
                    relocalize_coarse_patches(
                        gray,
                        expected,
                        patches,
                        deadline=lambda: hard_time_up() or elapsed() > min(Config.SOFT_BUDGET_PER_IMAGE + 8, Config.TIMEOUT_PER_IMAGE - 1),
                        max_candidates=2,
                    )
            return self._finalize(patches, t0, filename, path)

        # PASS 0: full-image quick
        if not hard_time_up() and wechat is not None:
            try:
                res, pts = wechat.detectAndDecode(img)
                if res:
                    for i, txt in enumerate(res):
                        bbox = (0,0,W,H)
                        points = []
                        if pts is not None and len(pts) > i:
                            points = map_qr_points(pts[i], ox=0, oy=0, scale=1.0, pad=0, limit_w=W, limit_h=H)
                            bbox = bbox_from_points(points, bbox)
                        merge_or_append_patch(patches, QRPatch(
                            data=parse_qr(txt),
                            bbox=bbox,
                            points=points,
                            source="wechat_full",
                            stage="full",
                            confidence=0.99,
                        ))
            except Exception:
                pass

        # zxing/pyzbar on v-channel + gray (cheap)
        try:
            vchan = preprocess_v_channel_color(img)
            for txt, rect in decode_multi(vchan):
                self._add_patch(patches, txt, rect, "decode_v", "full", 0.935)
        except Exception:
            pass
        try:
            for txt, rect in decode_multi(gray):
                self._add_patch(patches, txt, rect, "decode_gray", "full", 0.93)
        except Exception:
            pass

        if self._count(patches) >= expected:
            return finalize_report()

        # PASS 0.7: 6/10 panels have predictable module QR positions. Decode the
        # strongest bottom-row cell crops early before broad ROI scans burn time.
        if not hard_time_up() and expected >= 6:
            rescue_failed_cells(
                gray,
                expected,
                patches,
                deadline=lambda: hard_time_up() or elapsed() > min(Config.SOFT_BUDGET_PER_IMAGE + 18, Config.TIMEOUT_PER_IMAGE - 8),
            )
            if self._count(patches) >= expected:
                return finalize_report()

        # PASS 1: layout ROIs FAST
        if not hard_time_up():
            for (x,y,w,h,label) in layout_rois(gray, expected):
                roi = gray[y:y+h, x:x+w]
                decode_roi_push(
                    wechat, roi, x, y, "L1_"+label,
                    patches, deep=False,
                    max_zoomed=Config.MAX_ZOOMED_SIZE_FAST,
                    deadline=lambda: hard_time_up() or soft_time_up()
                )
                if self._count(patches) >= expected:
                    return finalize_report()
                if soft_time_up():
                    break

        if self._count(patches) >= expected:
            return finalize_report()

        # PASS 1.5: targeted cell crops early for small structured panels
        if not hard_time_up() and self._missing(patches, expected) > 0 and expected <= 4:
            decode_targeted_cells(
                gray,
                expected,
                patches,
                deadline=hard_time_up,
            )
            if self._count(patches) < expected:
                rescue_failed_cells(
                    gray,
                    expected,
                    patches,
                    deadline=lambda: hard_time_up() or elapsed() > min(Config.TIMEOUT_PER_IMAGE - 5, Config.SOFT_BUDGET_PER_IMAGE + 34),
                )

        if self._count(patches) >= expected:
            return finalize_report()

        # PASS 2: classic split ROIs FAST (keeps older wins on larger panels)
        if expected > 4 and not hard_time_up() and not soft_time_up():
            if W > 400 and H > 200:
                rois = [
                    (0, int(H*0.18), int(W*0.52), int(H*0.70), "splitL"),
                    (int(W*0.48), int(H*0.18), int(W*0.52), int(H*0.70), "splitR"),
                ]
                for (x,y,w,h,label) in rois:
                    roi = gray[y:y+h, x:x+w]
                    decode_roi_push(
                        wechat, roi, x, y, "L2_"+label,
                        patches, deep=False,
                        max_zoomed=Config.MAX_ZOOMED_SIZE_FAST,
                        deadline=lambda: hard_time_up() or soft_time_up()
                    )
                    if self._count(patches) >= expected:
                        return finalize_report()

        if self._count(patches) >= expected:
            return finalize_report()

        # PASS 2.15: for 6/10 panels, run the strongest local failed-cell rescue
        # before broad alternate layouts. This recovers bottom-row QR codes faster.
        if not hard_time_up() and self._missing(patches, expected) > 0 and expected >= 6:
            rescue_failed_cells(
                gray,
                expected,
                patches,
                deadline=lambda: hard_time_up() or elapsed() > (Config.TIMEOUT_PER_IMAGE - 3),
            )
            if self._count(patches) >= expected:
                return finalize_report()

        # PASS 2.25: overlapping alternate layout ROIs for larger panels only
        if not hard_time_up() and self._missing(patches, expected) > 0 and expected >= 6:
            for (x, y, w, h, label) in layout_rescue_rois(gray, expected):
                if hard_time_up():
                    break
                roi = gray[y:y + h, x:x + w]
                is_qr_corner = label.endswith("qrCorner")
                decode_roi_push(
                    wechat, roi, x, y, "LALT_" + label,
                    patches, deep=is_qr_corner,
                    max_zoomed=(min(Config.MAX_ZOOMED_SIZE_DEEP, 4200) if is_qr_corner else min(Config.MAX_ZOOMED_SIZE_FAST, 3200)),
                    stop_after_first=True,
                    deadline=lambda: hard_time_up() or soft_time_up(),
                )
                if self._count(patches) >= expected:
                    return finalize_report()

        if self._count(patches) >= expected:
            return finalize_report()

        # PASS 2.5: one targeted crop per still-empty cell.
        # For 6/10 panels, skip this weaker pass and save tail budget for the
        # stronger failed-cell module-local rescue below.
        if not hard_time_up() and self._missing(patches, expected) > 0 and expected <= 4:
            decode_targeted_cells(
                gray,
                expected,
                patches,
                deadline=hard_time_up,
            )

        if self._count(patches) >= expected:
            return finalize_report()

        # Stop only if we are almost out of hard budget. Large panels still need the
        # local failed-cell pass below; it recovers real bottom-row decodes cheaply.
        missing_now = self._missing(patches, expected)
        if expected >= 6 and elapsed() > Config.TIMEOUT_PER_IMAGE - 7 and missing_now >= max(2, expected // 3):
            return finalize_report()

        # PASS 3: rescue only failed cells with shifted local crops
        if not hard_time_up() and self._missing(patches, expected) > 0:
            rescue_failed_cells(
                gray,
                expected,
                patches,
                deadline=lambda: hard_time_up() or elapsed() > min(
                    Config.TIMEOUT_PER_IMAGE - 3,
                    Config.SOFT_BUDGET_PER_IMAGE + (26 if expected <= 4 else 34),
                ),
            )

        if self._count(patches) >= expected:
            return finalize_report()

        missing_now = self._missing(patches, expected)
        if soft_time_up() and (expected >= 6 or missing_now > 1):
            return finalize_report()

        # PASS 4: bounded deep ROI pass only on still-missing cells
        if not hard_time_up() and self._missing(patches, expected) == 1 and expected <= 4:
            occupied_names = {f"cell{r}{c}" for (r, c) in _occupied_cell_keys(gray, expected, [p for p in deduplicate(patches) if is_real_count_patch(p)])}
            for (x, y, w, h, label) in layout_rois(gray, expected):
                if hard_time_up():
                    break
                cell_name = label.split("_", 1)[0]
                if cell_name in occupied_names:
                    continue
                roi = gray[y:y + h, x:x + w]
                decode_roi_push(
                    wechat, roi, x, y, "DEEP_" + label,
                    patches, deep=True,
                    max_zoomed=Config.MAX_ZOOMED_SIZE_DEEP,
                    stop_after_first=True,
                    deadline=hard_time_up
                )
                occupied_names = {f"cell{r}{c}" for (r, c) in _occupied_cell_keys(gray, expected, [p for p in deduplicate(patches) if is_real_count_patch(p)])}
                if self._count(patches) >= expected:
                    return finalize_report()

        if self._count(patches) >= expected:
            return finalize_report()

        # PASS 5: bounded detectMulti warp fallback
        if not hard_time_up() and self._missing(patches, expected) == 1 and expected <= 4:
            dmulti_scales = list(Config.DETECT_MULTI_SCALES)
            dmulti_max_side = Config.DETECT_MULTI_MAX_SIDE
            if expected >= 6:
                if 2.0 not in dmulti_scales:
                    dmulti_scales.append(2.0)
                dmulti_max_side = max(dmulti_max_side, 2600)
            for scale in dmulti_scales:
                if hard_time_up():
                    break
                if scale == 1.0:
                    gray_scaled = gray
                    actual_scale = 1.0
                else:
                    max_side = max(H, W)
                    actual_scale = min(scale, dmulti_max_side / float(max_side))
                    if actual_scale <= 1.0:
                        continue
                    try:
                        gray_scaled = cv2.resize(
                            gray, (0, 0), fx=actual_scale, fy=actual_scale,
                            interpolation=cv2.INTER_LINEAR
                        )
                    except Exception:
                        continue

                self._decode_from_detectmulti(
                    gray_scaled, 1.0 / actual_scale, patches,
                    stage_prefix=f"dmulti_s{actual_scale:.2f}", expired=hard_time_up
                )
                if self._count(patches) >= expected:
                    return finalize_report()

        if self._count(patches) >= expected:
            return finalize_report()

        # PASS 6: only use grid when very close to complete
        if (
            not hard_time_up()
            and self._missing(patches, expected) > 0
            and self._missing(patches, expected) <= max(2, expected // 3)
            and not soft_time_up()
        ):
            self._run_grid_scan(gray, expected, patches, soft_time_up, hard_time_up, deep=False)

        if self._count(patches) >= expected:
            return finalize_report()

        # PASS 7: deep grid only for very small residual misses
        if (
            not hard_time_up()
            and self._missing(patches, expected) == 1
            and expected <= 6
        ):
            self._run_grid_scan(gray, expected, patches, soft_time_up, hard_time_up, deep=True)

        return finalize_report()


# -------------------------
# Output helpers
# -------------------------

def save_annotated_image(img: np.ndarray, patches: List[QRPatch], out_path: str, filename: str | None = None):
    """Draw only true QR-level locations.

    Some decoders, especially ZXing on a larger ROI, return a valid payload but
    not QR corner coordinates. Those payloads must still count as decoded, but
    their coarse ROI should not be drawn as a green square. Before saving the
    annotation, try one bounded text-matching localization pass inside grid cells
    so every drawable green square stays on the actual QR code.
    """
    vis = img.copy()
    expected = guess_expected_qr(filename or '') if filename else Config.DEFAULT_EXPECTED
    real_patches = [p for p in deduplicate(patches) if is_real_count_patch(p)]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    H, W = img.shape[:2]
    img_area = max(1, H * W)
    cells = list(split_into_cells(gray, expected)) if expected > 1 else [(0, 0, W, H, 'cell00', 0, 0)]

    def cell_key_for_bbox(bbox):
        x, y, w, h = bbox
        if w <= 0 or h <= 0:
            return None
        cx = x + w / 2.0
        cy = y + h / 2.0
        for x0, y0, cw, ch, _cell_name, r, c in cells:
            if x0 <= cx < x0 + cw and y0 <= cy < y0 + ch:
                return (r, c)
        return None

    def qr_level_box(p: QRPatch) -> bool:
        if p.source == 'layout_inferred' or str(p.stage).startswith('INF_'):
            return False
        if p.points and len(p.points) >= 4:
            return True
        x, y, w, h = p.bbox
        if w <= 0 or h <= 0:
            return False
        ratio = max(w, h) / max(1, min(w, h))
        if ratio > 1.75:
            return False
        if w > W * 0.18 or h > H * 0.18:
            return False
        if (w * h) > (img_area * 0.026):
            return False
        return p.source in {
            'module_local', 'relocalized_cell', 'targeted_cell_direct',
            'targeted_deep', 'warp_roi', 'wechat_roi'
        } or str(p.stage).startswith('LOC_') or str(p.stage).startswith('INF_LOC_')

    drawable: List[QRPatch] = []
    occupied_cells = set()
    for p in real_patches:
        if qr_level_box(p):
            drawable.append(p)
            key = cell_key_for_bbox(p.bbox)
            if key is not None:
                occupied_cells.add(key)

    # Bounded annotation-only localization for decoded payloads that have only a
    # coarse box. This does not increase QR count; it only tries to draw a true box.
    if len(drawable) < len(real_patches) and expected in {2, 4, 6, 8, 10}:
        wechat = get_wechat_detector()
        end_time = time.time() + (3.2 if expected <= 4 else 2.0)
        drawable_raws = {p.data.raw for p in drawable if p.data.raw}

        for p in real_patches:
            if time.time() >= end_time:
                break
            raw = getattr(getattr(p, 'data', None), 'raw', '') or ''
            if not raw or raw in drawable_raws:
                continue

            preferred = cell_key_for_bbox(p.bbox)
            ordered_cells = []
            if preferred is not None:
                ordered_cells.extend([cell for cell in cells if (cell[5], cell[6]) == preferred])
            ordered_cells.extend([cell for cell in cells if (cell[5], cell[6]) != preferred])

            for x0, y0, cw, ch, cell_name, r, c in ordered_cells:
                if time.time() >= end_time:
                    break
                if (r, c) in occupied_cells:
                    continue
                cell = gray[y0:y0 + ch, x0:x0 + cw]
                if cell.size == 0:
                    continue
                localized = _localize_known_text_in_cell(
                    wechat,
                    cell,
                    x0,
                    y0,
                    cell_name,
                    raw,
                    panel_type=_module_side(cell),
                    deadline=lambda: time.time() >= end_time,
                )
                if localized is None or not qr_level_box(localized):
                    continue
                drawable.append(localized)
                drawable_raws.add(raw)
                occupied_cells.add((r, c))
                break

    for p in drawable:
        if p.points and len(p.points) >= 4:
            pts = np.array(p.points, dtype=np.int32).reshape((-1, 1, 2))
            cv2.polylines(vis, [pts], True, (0,255,0), 2)
        else:
            x, y, w, h = p.bbox
            cv2.rectangle(vis, (x, y), (x + w, y + h), (0,255,0), 2)
    cv2.imwrite(out_path, vis)

def _patch_to_dict(patch: QRPatch) -> dict:
    return {
        "raw": patch.data.raw,
        "imei": patch.data.imei,
        "serial": patch.data.serial,
        "bbox": list(patch.bbox),
        "points": [list(map(int, pt)) for pt in (getattr(patch, "points", None) or [])],
        "source": patch.source,
        "stage": patch.stage,
        "confidence": patch.confidence,
    }


def _make_patch_from_dict(item: dict) -> QRPatch:
    data = item.get("data", {}) if isinstance(item.get("data"), dict) else {}
    raw = item.get("raw", "") or data.get("raw", "")
    imei = item.get("imei", "") or data.get("imei", "")
    serial = item.get("serial", "") or data.get("serial", "")
    bbox = tuple(item.get("bbox", (0, 0, 0, 0)))
    if len(bbox) != 4:
        bbox = (0, 0, 0, 0)
    points = []
    for pt in item.get("points", []) or []:
        try:
            if len(pt) >= 2:
                points.append((int(pt[0]), int(pt[1])))
        except Exception:
            pass
    return QRPatch(
        data=QRData(raw=raw, imei=imei, serial=serial),
        bbox=tuple(int(v) for v in bbox),
        points=points,
        source=str(item.get("source", "")),
        stage=str(item.get("stage", "")),
        confidence=float(item.get("confidence", 0.0) or 0.0),
    )


def _finalize_patch_list(patches: List[QRPatch]) -> List[QRPatch]:
    final = deduplicate(patches)
    final.sort(key=lambda p: (p.bbox[1], p.bbox[0], p.data.raw))
    for idx, p in enumerate(final, 1):
        p.id = idx
    return final


def _real_patch_count(patches: List[QRPatch]) -> int:
    return sum(1 for p in patches if is_real_count_patch(p))


def _merge_patch_lists(base_patches: List[QRPatch], extra_patches: List[QRPatch]) -> List[QRPatch]:
    merged: List[QRPatch] = []
    for p in list(base_patches) + list(extra_patches):
        raw = getattr(getattr(p, "data", None), "raw", "") or ""
        if not raw:
            continue
        merge_or_append_patch(merged, p)
    return _finalize_patch_list(merged)


def _missing_cells_for_patches(path: str, expected: int, patches: List[QRPatch]) -> List[Tuple[int, int]]:
    if expected <= 1:
        return []
    img = cv2.imread(path)
    if img is None:
        return []
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    rows, cols = infer_grid_shape(expected, w, h)
    real_patches = [patch for patch in patches if is_real_count_patch(patch)]
    occupied = _occupied_cell_keys(gray, expected, real_patches)
    missing = []
    for r in range(rows):
        for c in range(cols):
            if (r, c) not in occupied:
                missing.append((r, c))
    return missing


def _profile_base_budgets(profile: str, expected: int) -> tuple[int, int, int, int, int]:
    # Keep easy images responsive, but reserve enough tail budget for the targeted
    # failed-cell rescue. The previous values returned before that rescue could run.
    if profile == "fast":
        if expected <= 2:
            return (28, 8, 2200, 3000, 10)
        if expected >= 6:
            return (50, 14, 2400, 3600, 14)
        return (56, 16, 2400, 3400, 14)
    if profile == "balanced":
        if expected >= 6:
            return (58, 18, 2500, 3800, 18)
        return (70, 20, 2500, 3800, 18)
    if profile == "accuracy":
        if expected >= 6:
            return (76, 24, 2600, 4400, 22)
        return (90, 28, 2600, 4400, 22)
    if profile == "slow":
        if expected >= 6:
            return (105, 34, 2800, 5200, 26)
        return (120, 38, 2800, 5200, 26)
    return (Config.TIMEOUT_PER_IMAGE, Config.SOFT_BUDGET_PER_IMAGE, Config.MAX_ZOOMED_SIZE_FAST, Config.MAX_ZOOMED_SIZE_DEEP, Config.MAX_GRID_CELLS_TOTAL)


def _profile_deep_timeout(profile: str, override_timeout: int = None) -> int:
    if override_timeout is not None:
        return max(0, int(override_timeout))
    if profile == "balanced":
        return 18
    if profile == "accuracy":
        return 40
    if profile == "slow":
        return 70
    return 0


def _profile_targeted_budget(profile: str, expected: int, total_budget: int) -> int:
    total_budget = max(0, int(total_budget or 0))
    if total_budget <= 0:
        return 0
    if profile == "balanced":
        if expected >= 6:
            return max(12, min(18, total_budget // 2))
        return max(10, min(16, total_budget // 2))
    if profile == "accuracy":
        if expected >= 6:
            return max(18, min(28, (total_budget * 2) // 3))
        return max(14, min(22, total_budget // 2))
    if profile == "slow":
        if expected >= 6:
            return max(24, min(40, (total_budget * 2) // 3))
        return max(18, min(28, total_budget // 2))
    return 0


def _should_run_global_deep(profile: str, expected: int, missing: int) -> bool:
    if missing <= 0:
        return False
    if expected >= 6:
        return False
    if profile == "balanced":
        return missing == 1
    if profile == "accuracy":
        return missing == 1
    if profile == "slow":
        return missing <= 1
    return False


def _terminate_process_tree(proc: subprocess.Popen):
    if proc is None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
            )
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_deep_rescue_subprocess(path: str, timeout_s: int) -> dict:
    if timeout_s <= 0:
        return {"ok": False, "patches": [], "elapsed": 0.0, "error": "deep disabled"}

    started = time.time()
    saved = {
        "timeout": Config.TIMEOUT_PER_IMAGE,
        "soft": Config.SOFT_BUDGET_PER_IMAGE,
        "max_fast": Config.MAX_ZOOMED_SIZE_FAST,
        "max_deep": Config.MAX_ZOOMED_SIZE_DEEP,
        "grid_cells": Config.MAX_GRID_CELLS_TOTAL,
    }
    try:
        hard_budget = min(max(1, int(timeout_s)), 120)
        soft_budget = max(18, min(hard_budget - 1 if hard_budget > 1 else hard_budget, int(hard_budget * 0.64)))
        Config.TIMEOUT_PER_IMAGE = max(saved["timeout"], hard_budget)
        Config.SOFT_BUDGET_PER_IMAGE = max(saved["soft"], soft_budget)
        Config.MAX_ZOOMED_SIZE_FAST = max(saved["max_fast"], 3600)
        Config.MAX_ZOOMED_SIZE_DEEP = max(saved["max_deep"], 5600)
        Config.MAX_GRID_CELLS_TOTAL = max(saved["grid_cells"], 28)
        rep = QRProcessor().process(path)
        patches = [_patch_to_dict(patch) for patch in rep.patches]
        return {
            "ok": True,
            "patches": patches,
            "elapsed": max(float(rep.elapsed or 0.0), time.time() - started),
            "error": rep.error or "",
        }
    except Exception as exc:
        return {"ok": False, "patches": [], "elapsed": time.time() - started, "error": str(exc)}
    finally:
        Config.TIMEOUT_PER_IMAGE = saved["timeout"]
        Config.SOFT_BUDGET_PER_IMAGE = saved["soft"]
        Config.MAX_ZOOMED_SIZE_FAST = saved["max_fast"]
        Config.MAX_ZOOMED_SIZE_DEEP = saved["max_deep"]
        Config.MAX_GRID_CELLS_TOTAL = saved["grid_cells"]


def _run_targeted_cell_deep_subprocess(path: str, expected: int,
                                       missing_cells: List[Tuple[int, int]],
                                       timeout_s: int, seed_patches: List[QRPatch] | None = None,
                                       aggressive: bool = False) -> dict:
    if timeout_s <= 0 or expected not in {4, 6} or not missing_cells:
        return {"ok": False, "patches": [], "elapsed": 0.0, "error": "targeted deep disabled"}

    started = time.time()
    img = cv2.imread(path)
    if img is None:
        return {"ok": False, "patches": [], "elapsed": 0.0, "error": "targeted deep cannot read image"}

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    wechat = get_wechat_detector()
    patches: List[QRPatch] = []
    seen_raw = set()
    cell_lookup = {(r, c): (x0, y0, cw, ch, cell_name) for x0, y0, cw, ch, cell_name, r, c in split_into_cells(gray, expected)}
    peer_templates = _peer_qr_templates(gray, expected, seed_patches or [])

    def expired() -> bool:
        return (time.time() - started) >= timeout_s

    per_cell_budget = max(8, int(timeout_s / max(1, len(missing_cells))))
    if aggressive:
        per_cell_budget = min(max(per_cell_budget, 16), 24)
    else:
        per_cell_budget = min(per_cell_budget, 18)

    for cell_key in missing_cells:
        if expired():
            break
        cell_started = time.time()

        def cell_expired() -> bool:
            return expired() or ((time.time() - cell_started) >= per_cell_budget)

        cell_info = cell_lookup.get(tuple(cell_key))
        if not cell_info:
            continue
        x0, y0, cw, ch, cell_name = cell_info
        cell = gray[y0:y0 + ch, x0:x0 + cw]
        if cell.size == 0:
            continue

        side = _module_side(cell)
        side_candidates = [side]
        if side == 'left':
            side_candidates.append('right')
        elif side == 'right':
            side_candidates.append('left')
        else:
            side_candidates.extend(['left', 'right'])

        windows = []
        seen_windows = set()

        def add_window(wx: int, wy: int, ww: int, wh: int, label: str):
            wx = max(0, min(int(wx), cw - 1))
            wy = max(0, min(int(wy), ch - 1))
            ww = max(1, min(int(ww), cw - wx))
            wh = max(1, min(int(wh), ch - wy))
            if ww < 18 or wh < 18:
                return
            key = (wx, wy, ww, wh)
            if key in seen_windows:
                return
            seen_windows.add(key)
            windows.append((wx, wy, ww, wh, label))

        def add_template_windows(template, suffix: str):
            if not template:
                return
            nx, ny, nw, nh = template
            base_x = int(nx * cw)
            base_y = int(ny * ch)
            base_w = max(24, int(nw * cw))
            base_h = max(24, int(nh * ch))
            grows = (1.0, 1.18, 1.35) if aggressive else (1.0, 1.18)
            shifts = [(-0.04, -0.04), (0.0, -0.04), (0.04, -0.04), (-0.04, 0.0), (0.0, 0.0), (0.04, 0.0), (0.0, 0.04)] if aggressive else [(0.0, 0.0), (0.03, 0.0), (0.0, 0.03)]
            for grow_idx, grow in enumerate(grows):
                qw = max(24, int(base_w * grow))
                qh = max(24, int(base_h * grow))
                for shift_idx, (sx, sy) in enumerate(shifts):
                    qx = int(base_x + sx * cw - (qw - base_w) / 2)
                    qy = int(base_y + sy * ch - (qh - base_h) / 2)
                    add_window(qx, qy, qw, qh, f"{cell_name}_peerqr_{grow_idx}_{shift_idx}{suffix}")

        def add_probe_windows(panel_side: str, suffix: str):
            qzx, qzy, qzw, qzh = crop_target_qr_zone(cell, panel_type=panel_side)
            base_windows = [(qzx, qzy, qzw, qzh, f"{cell_name}_target{suffix}")]
            qr_size = max(28, int(min(qzw, qzh) * 0.23))
            base_qx = qzx + max(0, qzw - qr_size - max(6, int(qzw * 0.08)))
            base_qy = qzy + max(0, qzh - qr_size - max(6, int(qzh * 0.10)))
            shifts = [(-0.06, -0.06), (0.0, -0.06), (0.06, -0.06), (-0.04, 0.0), (0.0, 0.0), (0.06, 0.0), (-0.04, 0.06), (0.0, 0.06), (0.06, 0.06)]
            for idx, (sx, sy) in enumerate(shifts if aggressive else shifts[3:6]):
                qx = max(0, min(cw - 1, int(base_qx + sx * cw)))
                qy = max(0, min(ch - 1, int(base_qy + sy * ch)))
                qw = min(max(28, qr_size), cw - qx)
                qh = min(max(28, qr_size), ch - qy)
                if qw >= 18 and qh >= 18:
                    base_windows.append((qx, qy, qw, qh, f"{cell_name}_qrprobe_{idx}{suffix}"))
            for win in base_windows:
                key = win[:4]
                if key in seen_windows:
                    continue
                seen_windows.add(key)
                windows.append(win)

        primary_template = peer_templates.get("by_col", {}).get(cell_key[1]) or peer_templates.get("global")
        add_template_windows(primary_template, '')
        add_probe_windows(side_candidates[0], '')
        for idx, panel_side in enumerate(side_candidates[1:], 1):
            add_template_windows(primary_template, f'_alt{idx}')
            add_probe_windows(panel_side, f'_alt{idx}')

        zx, zy, zw, zh = crop_target_qr_zone(cell, panel_type=side_candidates[0])

        if aggressive:
            tight_x = zx + max(0, int(zw * 0.42))
            tight_y = zy + max(0, int(zh * 0.44))
            tight_w = max(28, int(zw * 0.34))
            tight_h = max(28, int(zh * 0.34))
            windows.append((tight_x, tight_y, min(tight_w, cw - tight_x), min(tight_h, ch - tight_y), f"{cell_name}_target_tight"))
            medium_x = zx + max(0, int(zw * 0.30))
            medium_y = zy + max(0, int(zh * 0.30))
            medium_w = max(32, int(zw * 0.46))
            medium_h = max(32, int(zh * 0.46))
            windows.append((medium_x, medium_y, min(medium_w, cw - medium_x), min(medium_h, ch - medium_y), f"{cell_name}_target_mid"))
        wide_x = max(0, zx - int(0.08 * cw))
        wide_y = max(0, zy - int(0.08 * ch))
        wide_w = min(cw - wide_x, zw + int(0.16 * cw))
        wide_h = min(ch - wide_y, zh + int(0.18 * ch))
        windows.append((wide_x, wide_y, wide_w, wide_h, f"{cell_name}_wide"))
        if side == "left":
            windows.extend([
                (int(0.26 * cw), int(0.34 * ch), int(0.46 * cw), int(0.56 * ch), f"{cell_name}_qrleft"),
                (int(0.10 * cw), int(0.14 * ch), int(0.76 * cw), int(0.84 * ch), f"{cell_name}_moduleleft"),
            ])
        else:
            windows.extend([
                (int(0.48 * cw), int(0.34 * ch), int(0.42 * cw), int(0.56 * ch), f"{cell_name}_qrright"),
                (int(0.24 * cw), int(0.14 * ch), int(0.70 * cw), int(0.84 * ch), f"{cell_name}_moduleright"),
            ])

        found_here = False
        for wx, wy, ww, wh, label in windows:
            if cell_expired() or found_here:
                break
            ww = max(1, min(ww, cw - wx))
            wh = max(1, min(wh, ch - wy))
            if ww < 18 or wh < 18:
                continue
            roi = cell[wy:wy + wh, wx:wx + ww]
            if roi.size == 0:
                continue
            temp_patches: List[QRPatch] = []
            decode_roi_push(wechat, roi, x0 + wx, y0 + wy, f"TD_{label}", temp_patches, deep=True, max_zoomed=min(Config.MAX_ZOOMED_SIZE_DEEP, 6400 if aggressive else 5600), stop_after_first=False, deadline=cell_expired)
            for patch in temp_patches:
                raw = patch.data.raw or ""
                if not raw or raw in seen_raw or not is_real_count_patch(patch):
                    continue
                seen_raw.add(raw)
                patches.append(patch)
                found_here = True
            if not found_here:
                try:
                    roi_bgr = cv2.cvtColor(add_quiet_zone(roi, pad_ratio=0.14), cv2.COLOR_GRAY2BGR)
                    for text in try_decode_variants(roi_bgr):
                        if not (text and is_valid_qr(text)) or text in seen_raw:
                            continue
                        seen_raw.add(text)
                        patches.append(QRPatch(
                            data=parse_qr(text),
                            bbox=(x0 + wx, y0 + wy, ww, wh),
                            source="targeted_deep",
                            stage=f"TD_{label}_tryvar",
                            confidence=0.92,
                        ))
                        found_here = True
                        break
                except Exception:
                    pass

        if aggressive and expected <= 4 and not found_here and not cell_expired():
            temp_patches = []
            decode_roi_push(wechat, cell, x0, y0, f"TD_{cell_name}_fullcell", temp_patches, deep=True, max_zoomed=min(Config.MAX_ZOOMED_SIZE_DEEP, 5600), stop_after_first=False, deadline=cell_expired)
            for patch in temp_patches:
                raw = patch.data.raw or ""
                if not raw or raw in seen_raw or not is_real_count_patch(patch):
                    continue
                seen_raw.add(raw)
                patches.append(patch)
                found_here = True

        if found_here or cell_expired():
            continue

        # Keep the targeted rescue bounded: prefer a few strict QR windows instead of a full heavy module sweep.
        fallback_windows = [
            (int(0.44 * cw), int(0.46 * ch), int(0.38 * cw), int(0.40 * ch), f"{cell_name}_tight_qr"),
            (int(0.34 * cw), int(0.30 * ch), int(0.52 * cw), int(0.58 * ch), f"{cell_name}_tight_module"),
        ]
        for wx, wy, ww, wh, label in fallback_windows:
            if cell_expired() or found_here:
                break
            ww = max(1, min(ww, cw - wx))
            wh = max(1, min(wh, ch - wy))
            if ww < 18 or wh < 18:
                continue
            roi = cell[wy:wy + wh, wx:wx + ww]
            if roi.size == 0:
                continue
            temp_patches = []
            decode_roi_push(wechat, roi, x0 + wx, y0 + wy, f"TD_{label}", temp_patches, deep=True, max_zoomed=min(Config.MAX_ZOOMED_SIZE_DEEP, 5200), stop_after_first=True, deadline=cell_expired)
            for patch in temp_patches:
                raw = patch.data.raw or ""
                if not raw or raw in seen_raw or not is_real_count_patch(patch):
                    continue
                seen_raw.add(raw)
                patches.append(patch)
                found_here = True
                break

    return {
        "ok": True,
        "patches": [_patch_to_dict(patch) for patch in patches],
        "elapsed": time.time() - started,
        "error": "",
    }


def process_image_with_profile(path: str, profile: str = None, deep_timeout: int = None) -> ImageReport:
    profile = profile or Config.PROFILE
    expected = guess_expected_qr(os.path.basename(path))
    saved = {
        "timeout": Config.TIMEOUT_PER_IMAGE,
        "soft": Config.SOFT_BUDGET_PER_IMAGE,
        "max_fast": Config.MAX_ZOOMED_SIZE_FAST,
        "max_deep": Config.MAX_ZOOMED_SIZE_DEEP,
        "grid_cells": Config.MAX_GRID_CELLS_TOTAL,
    }
    base_timeout, base_soft, max_fast, max_deep, max_grid = _profile_base_budgets(profile, expected)
    Config.TIMEOUT_PER_IMAGE = base_timeout
    Config.SOFT_BUDGET_PER_IMAGE = base_soft
    Config.MAX_ZOOMED_SIZE_FAST = min(saved["max_fast"], max_fast)
    Config.MAX_ZOOMED_SIZE_DEEP = min(saved["max_deep"], max_deep)
    Config.MAX_GRID_CELLS_TOTAL = min(saved["grid_cells"], max_grid)
    try:
        fast_rep = QRProcessor().process(path)
    finally:
        Config.TIMEOUT_PER_IMAGE = saved["timeout"]
        Config.SOFT_BUDGET_PER_IMAGE = saved["soft"]
        Config.MAX_ZOOMED_SIZE_FAST = saved["max_fast"]
        Config.MAX_ZOOMED_SIZE_DEEP = saved["max_deep"]
        Config.MAX_GRID_CELLS_TOTAL = saved["grid_cells"]
    expected = guess_expected_qr(fast_rep.filename)
    if profile == "fast" or fast_rep.qr_count >= expected or expected < Config.DEEP_RESCUE_MIN_EXPECTED:
        return fast_rep

    missing = max(0, expected - fast_rep.qr_count)
    # Balanced should still rescue structured 4-QR plates even if the fast pass is weak.
    # For larger panels, avoid runaway deep work when the base scan is far from complete.
    if profile == "balanced" and expected >= 6 and missing > max(2, expected // 2):
        return fast_rep

    timeout_s = _profile_deep_timeout(profile, deep_timeout)
    if profile in {"accuracy", "slow"} and expected in {4, 6} and missing > 0:
        targeted_budget = _profile_targeted_budget(profile, expected, timeout_s)
        missing_cells = _missing_cells_for_patches(path, expected, fast_rep.patches)
        targeted_res = _run_targeted_cell_deep_subprocess(path, expected, missing_cells, targeted_budget, seed_patches=fast_rep.patches, aggressive=(profile == "slow" or (profile == "accuracy" and expected >= 6)))
        if targeted_res.get("ok"):
            extra_patches = []
            for item in targeted_res.get("patches", []):
                try:
                    extra_patches.append(_make_patch_from_dict(item))
                except Exception:
                    pass
            if extra_patches:
                merged = _merge_patch_lists(fast_rep.patches, extra_patches)
                fast_rep = ImageReport(
                    filename=fast_rep.filename,
                    path=fast_rep.path,
                    success=True,
                    qr_count=_real_patch_count(merged),
                    patches=merged,
                    elapsed=float(fast_rep.elapsed + float(targeted_res.get("elapsed", 0.0) or 0.0)),
                    error=targeted_res.get("error", ""),
                )
                if fast_rep.qr_count >= expected:
                    return fast_rep
                missing = max(0, expected - fast_rep.qr_count)
        elif targeted_res.get("error") and targeted_res.get("error") not in {"targeted deep timeout", "targeted deep disabled"}:
            fast_rep.error = targeted_res["error"]

        timeout_s = max(0, int(timeout_s) - int(targeted_res.get("elapsed", 0.0) or 0.0))
        missing = max(0, expected - fast_rep.qr_count)
        if timeout_s <= 0 or not _should_run_global_deep(profile, expected, missing):
            return fast_rep

    deep_res = _run_deep_rescue_subprocess(path, timeout_s)
    if not deep_res.get("ok"):
        if deep_res.get("error"):
            fast_rep.error = deep_res["error"]
        return fast_rep

    extra_patches = []
    for item in deep_res.get("patches", []):
        try:
            extra_patches.append(_make_patch_from_dict(item))
        except Exception:
            pass

    merged = _merge_patch_lists(fast_rep.patches, extra_patches)
    return ImageReport(
        filename=fast_rep.filename,
        path=fast_rep.path,
        success=True,
        qr_count=_real_patch_count(merged),
        patches=merged,
        elapsed=float(fast_rep.elapsed + float(deep_res.get("elapsed", 0.0) or 0.0)),
        error=deep_res.get("error", ""),
    )


def batch_scan(input_dir: str, output_dir: str, recursive: bool=True, workers: int=Config.MAX_WORKERS,
               profile: str = None, deep_timeout: int = None):
    os.makedirs(output_dir, exist_ok=True)
    profile = profile or Config.PROFILE

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    exts = (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp")

    files = []
    if recursive:
        for root, _, fnames in os.walk(input_dir):
            for f in fnames:
                if f.lower().endswith(exts):
                    files.append(os.path.join(root, f))
    else:
        for e in exts:
            files.extend(glob.glob(os.path.join(input_dir, "*" + e)))

    if not files:
        print(f"No images found in '{input_dir}'")
        return

    deep_budget = _profile_deep_timeout(profile, deep_timeout)

    print(f"\n{'='*80}")
    print("  QR DETECTOR ENGINE")
    if profile == "fast":
        prof_line = "fast"
    else:
        prof_line = f"{profile} | deep_timeout={deep_budget}s"
    print(f"  {len(files)} images | workers: {workers} | timeout: {Config.TIMEOUT_PER_IMAGE}s | profile: {prof_line}")
    print(f"{'='*80}\n")
    print("  Initialising detectors...", flush=True)

    wechat_ok = False
    try:
        wechat_ok = (cv2.wechat_qrcode_WeChatQRCode is not None) and (get_wechat_detector() is not None)
    except Exception:
        wechat_ok = False

    print("  WeChatQRCode available" if wechat_ok else "  WeChatQRCode unavailable", flush=True)
    print("  ZXing-cpp available" if (ZXING_OK and Config.USE_ZXING) else "  ZXing-cpp unavailable", flush=True)

    csv_path = os.path.join(output_dir, f"qr_results_{ts}.csv")
    processor = QRProcessor()
    reports_by_path = {}
    t_global = time.time()

    for p in files:
        print(f"  -> {os.path.basename(p)}", flush=True)

    # Use processes for batch scans: process_image_with_profile temporarily adjusts
    # Config values, and process isolation prevents cross-image profile interference.
    executor_cls = ProcessPoolExecutor if workers > 1 else ThreadPoolExecutor
    with executor_cls(max_workers=workers) as executor:
        futures = {executor.submit(process_image_with_profile, p, profile, deep_timeout): p for p in files}
        for i, fut in enumerate(as_completed(futures), 1):
            path = futures[fut]
            try:
                rep = fut.result()
            except Exception as e:
                rep = ImageReport(os.path.basename(path), path, False, 0, [], 0, error=str(e))
            reports_by_path[path] = rep
            if rep.success:
                print(f"  [F{i:>2}/{len(files)}] {rep.filename:<35} -> {rep.qr_count:>2} QR ({rep.elapsed:>5.1f}s)", flush=True)
            else:
                print(f"  [F{i:>2}/{len(files)}] {rep.filename:<35} -> 0 QR ({rep.elapsed:>5.1f}s) ERR={rep.error}", flush=True)

    # Each image already applies its own profile-aware bounded rescue in process_image_with_profile().
    # Do not run a second batch-level deep rescue; it caused duplicate work and long tail latency.

    reports = [reports_by_path[p] for p in files]
    total_qr = sum(r.qr_count for r in reports if r.success)

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Image","QR_ID","IMEI","Serial","Raw_Data","Source","Stage"])
        for rep in reports:
            if rep.success:
                for patch in rep.patches:
                    writer.writerow([
                        rep.filename,
                        patch.id,
                        patch.data.imei,
                        patch.data.serial,
                        patch.data.raw,
                        patch.source,
                        patch.stage
                    ])
            else:
                writer.writerow([rep.filename, "", "", "", "", "", ""])

    elapsed = time.time() - t_global

    print(f"\n{'='*80}")
    print(f"  COMPLETE: {total_qr} QR codes from {len(files)} images")
    print(f"  {elapsed:.1f}s total | {elapsed/len(files):.1f}s avg")
    print(f"  CSV: {csv_path}")
    print(f"{'='*80}\n")

    if Config.SAVE_JSON:
        json_path = os.path.join(output_dir, f"qr_summary_{ts}.json")
        with open(json_path, "w", encoding="utf-8") as jf:
            json.dump({
                "timestamp": ts,
                "profile": profile,
                "deep_timeout": deep_budget,
                "total_images": len(files),
                "total_qr": total_qr,
                "reports": [{
                    "filename": r.filename,
                    "qr_count": r.qr_count,
                    "elapsed": r.elapsed,
                    "error": r.error,
                    "patches": [p.to_dict() for p in r.patches]
                } for r in reports if r.success]
            }, jf, indent=2, ensure_ascii=False)
        print(f"  JSON: {json_path}")

    if Config.SAVE_ANNOTATED:
        annot_dir = os.path.join(output_dir, "annotated")
        os.makedirs(annot_dir, exist_ok=True)
        for rep in reports:
            if rep.success and rep.patches:
                img = cv2.imread(rep.path)
                if img is not None:
                    out_path = os.path.join(annot_dir, f"det_{rep.filename}")
                    save_annotated_image(img, rep.patches, out_path, rep.filename)


def main():
    parser = argparse.ArgumentParser(description="QR detector engine with fast, balanced, and accuracy profiles")
    parser.add_argument("-i", "--input", default=Config.DEFAULT_INPUT)
    parser.add_argument("-o", "--output", default=Config.DEFAULT_OUTPUT)
    parser.add_argument("--single", metavar="FILE", help="Process a single image")
    parser.add_argument("--workers", type=int, default=Config.MAX_WORKERS)
    parser.add_argument("--no-recursive", dest="recursive", action="store_false")
    parser.add_argument("--timeout", type=int, default=Config.TIMEOUT_PER_IMAGE)
    parser.add_argument("--soft", type=int, default=Config.SOFT_BUDGET_PER_IMAGE)
    parser.add_argument("--profile", choices=["fast", "balanced", "accuracy", "slow"], default=Config.PROFILE)
    parser.add_argument("--deep-timeout", type=int, default=None, help="Override deep rescue timeout in seconds")
    args = parser.parse_args()

    Config.TIMEOUT_PER_IMAGE = int(args.timeout)
    Config.SOFT_BUDGET_PER_IMAGE = int(args.soft)
    Config.PROFILE = args.profile

    if args.single:
        if not os.path.isfile(args.single):
            print(f"File not found: {args.single}")
            return
        rep = process_image_with_profile(args.single, profile=args.profile, deep_timeout=args.deep_timeout)
        expected = guess_expected_qr(rep.filename)
        print(f"\n{rep.filename}: {rep.qr_count}/{expected} QR codes ({rep.elapsed:.1f}s) [{args.profile}]")
        if rep.error:
            print(f"error: {rep.error}")
        for p in rep.patches:
            print(f"  QR{p.id}: IMEI={p.data.imei or '?'}  SN={p.data.serial or '?'} [{p.source}/{p.stage}]")
    else:
        batch_scan(args.input, args.output, args.recursive, args.workers, args.profile, args.deep_timeout)


if __name__ == "__main__":
    main()

