"""OCR engine wrapper.

Uses EasyOCR (CPU mode) as the one maintained OCR library for this project.
EasyOCR is pure-Python/pip-installable (no separate system binary like
Tesseract needs), which matters for a non-technical Windows user, at the
cost of a heavier dependency (PyTorch) and a one-time model download on
first use. See README.md for exact install steps and expected download
sizes.

This module deliberately does no recognition-acceptance logic - it only
turns an image crop into raw OCR text plus whatever confidence the engine
reports. Everything about deciding what to *believe* lives in
app.recognition.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np

from app import config


@dataclass
class OcrResult:
    raw_text: str
    confidence: Optional[float]  # OCR engine's own score, not accuracy
    error: Optional[str] = None


class OcrEngine:
    """Lazily-initialized EasyOCR wrapper, safe to construct before use.

    Model loading (and first-run model download) happens on the first call
    to ``ensure_ready()`` / ``recognize()``, not at import time, so the UI
    can show a clear "loading OCR..." status instead of freezing silently.
    """

    def __init__(self):
        self._reader = None
        self._lock = threading.Lock()
        self._init_error: Optional[str] = None

    @property
    def is_ready(self) -> bool:
        return self._reader is not None

    @property
    def init_error(self) -> Optional[str]:
        return self._init_error

    def ensure_ready(self) -> None:
        """Load the EasyOCR model if not already loaded. Safe to call often."""
        if self._reader is not None or self._init_error is not None:
            return
        with self._lock:
            if self._reader is not None or self._init_error is not None:
                return
            try:
                import easyocr  # imported lazily: heavy import (torch)
                self._reader = easyocr.Reader(config.OCR_LANGUAGES, gpu=False, verbose=False)
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI
                self._init_error = (
                    "Failed to load OCR engine (EasyOCR). Make sure setup.ps1 "
                    f"completed successfully. Original error: {exc}"
                )

    def recognize_digits(self, crop_bgr: np.ndarray) -> OcrResult:
        """Run OCR on a small crop expected to contain only digits.

        Applies a digit allowlist and a simple upscale, since these fields
        are typically small regions where EasyOCR benefits from more pixels.
        """
        self.ensure_ready()
        if self._init_error is not None:
            return OcrResult(raw_text="", confidence=None, error=self._init_error)
        if crop_bgr is None or crop_bgr.size == 0:
            return OcrResult(raw_text="", confidence=None, error="Empty crop (region out of frame bounds?)")

        try:
            processed = _preprocess_for_ocr(crop_bgr)
            detections = self._reader.readtext(
                processed,
                allowlist=config.OCR_DIGIT_ALLOWLIST,
                detail=1,
                paragraph=False,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI, not crashed
            return OcrResult(raw_text="", confidence=None, error=f"OCR error: {exc}")

        if not detections:
            return OcrResult(raw_text="", confidence=None, error=None)

        # Sort left-to-right so multi-blob digit strings read in order.
        detections.sort(key=lambda d: min(pt[0] for pt in d[0]))
        text = "".join(d[1] for d in detections)
        confidences: List[float] = [float(d[2]) for d in detections if len(d) > 2]
        confidence = min(confidences) if confidences else None
        return OcrResult(raw_text=text, confidence=confidence, error=None)


def _preprocess_for_ocr(crop_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    scale = 3 if max(h, w) < 200 else 1
    if scale != 1:
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    return gray
