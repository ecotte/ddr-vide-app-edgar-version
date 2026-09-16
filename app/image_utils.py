"""Small shared helpers for encoding/decoding/comparing small image crops.

Used by arrow-lane calibration (storing reference snapshots inside a layout
profile) and by the arrow state classifier (comparing a live crop against
those references). Kept separate from ocr_engine.py since this has nothing
to do with OCR.
"""
from __future__ import annotations

import base64
from typing import Optional

import cv2
import numpy as np


def encode_crop_to_base64_png(crop_bgr: np.ndarray) -> str:
    """Encode a BGR image crop as a base64 PNG string for JSON storage."""
    ok, buf = cv2.imencode(".png", crop_bgr)
    if not ok:
        raise ValueError("Failed to encode crop as PNG.")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def decode_base64_png_to_crop(data: str) -> Optional[np.ndarray]:
    """Decode a base64 PNG string back into a BGR image crop, or None if
    the data is empty/invalid."""
    if not data:
        return None
    try:
        raw = base64.b64decode(data)
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return img
    except Exception:  # noqa: BLE001 - treat any decode failure as "no reference"
        return None


def resize_crop(crop_bgr: np.ndarray, new_w: int, new_h: int) -> np.ndarray:
    new_w = max(1, int(new_w))
    new_h = max(1, int(new_h))
    return cv2.resize(crop_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)


def similarity(crop_bgr: np.ndarray, reference_bgr: np.ndarray) -> float:
    """Return a similarity score in roughly [0, 1] between two same-purpose
    crops (higher = more similar). Uses normalized cross-correlation on
    grayscale versions, resizing the reference to match the crop's size if
    they differ (e.g. after a resolution rescale) so a comparison is always
    possible rather than silently failing.

    Normalized cross-correlation is undefined when a region has essentially
    no internal contrast (a perfectly flat color, e.g. a single-color LED),
    since it divides by each image's standard deviation. For that
    degenerate case this falls back to a plain mean-brightness comparison
    instead of returning a meaningless/degenerate correlation value.

    This is a generic image-similarity measure - it carries no built-in
    knowledge of DDR skins, colors, or judgment semantics.
    """
    if crop_bgr is None or reference_bgr is None or crop_bgr.size == 0 or reference_bgr.size == 0:
        return 0.0

    gray_crop = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    ref = reference_bgr
    if ref.shape[:2] != crop_bgr.shape[:2]:
        ref = resize_crop(ref, crop_bgr.shape[1], crop_bgr.shape[0])
    gray_ref = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY).astype(np.float32)

    flat_threshold = 1.0  # std-dev (0..255 scale) below which we treat a region as "flat"
    if gray_crop.std() < flat_threshold and gray_ref.std() < flat_threshold:
        diff = abs(float(gray_crop.mean()) - float(gray_ref.mean()))
        return max(0.0, 1.0 - diff / 255.0)

    result = cv2.matchTemplate(gray_crop, gray_ref, cv2.TM_CCOEFF_NORMED)
    score = float(result[0][0])
    # TM_CCOEFF_NORMED is in [-1, 1]; clamp/rescale to [0, 1] for a simpler
    # "higher is more similar, never negative" reading in the UI.
    return max(0.0, (score + 1.0) / 2.0)
