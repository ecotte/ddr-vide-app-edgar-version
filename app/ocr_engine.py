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

import re
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
        self._recognition_lock = threading.Lock()
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
                import torch

                torch.set_num_threads(1)
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
            with self._recognition_lock:
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

        selected = _select_numeric_group(detections, processed.shape[1])
        text = "".join(_longest_digit_run(d[1]) for d in selected)
        confidences: List[float] = [float(d[2]) for d in selected if len(d) > 2]
        confidence = min(confidences) if confidences else None
        return OcrResult(raw_text=text, confidence=confidence, error=None)

    def recognize_score_digits(self, crop_bgr: np.ndarray) -> OcrResult:
        """Read a fixed-width score while excluding short punctuation glyphs."""
        self.ensure_ready()
        if self._init_error is not None:
            return OcrResult(raw_text="", confidence=None, error=self._init_error)
        if crop_bgr is None or crop_bgr.size == 0:
            return OcrResult(
                raw_text="",
                confidence=None,
                error="Empty crop (region out of frame bounds?)",
            )

        gray, boxes, _ = _extract_score_glyphs(crop_bgr)

        if not boxes or len(boxes) > config.FIELD_MAX_DIGITS["score"]:
            return OcrResult(raw_text="", confidence=None, error=None)

        try:
            with self._recognition_lock:
                detections = self._reader.recognize(
                    gray,
                    horizontal_list=boxes,
                    free_list=[],
                    decoder="beamsearch",
                    batch_size=len(boxes),
                    allowlist=config.OCR_DIGIT_ALLOWLIST,
                    detail=1,
                )
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI, not crashed
            return OcrResult(raw_text="", confidence=None, error=f"OCR error: {exc}")

        if len(detections) != len(boxes) or any(
            not _first_digit(detection[1]) for detection in detections
        ):
            return OcrResult(raw_text="", confidence=None, error=None)
        text = "".join(_first_digit(detection[1]) for detection in detections)
        confidences = [
            1.0 if len(_longest_digit_run(detection[1])) > 1 else float(detection[2])
            for detection in detections
        ]
        return OcrResult(
            raw_text=text,
            confidence=min(confidences) if confidences else None,
            error=None,
        )

    def learn_score_templates(self, crop_bgr: np.ndarray) -> list[tuple[str, np.ndarray]]:
        self.ensure_ready()
        if self._init_error is not None or crop_bgr is None or crop_bgr.size == 0:
            return []
        gray, boxes, glyphs = _extract_score_glyphs(crop_bgr)
        if not boxes:
            return []
        with self._recognition_lock:
            detections = self._reader.recognize(
                gray,
                horizontal_list=boxes,
                free_list=[],
                decoder="beamsearch",
                batch_size=len(boxes),
                allowlist=config.OCR_DIGIT_ALLOWLIST,
                detail=1,
            )
        learned = []
        for detection, glyph in zip(detections, glyphs):
            run = _longest_digit_run(detection[1])
            confidence = float(detection[2])
            if len(run) == 1 and confidence >= config.SCORE_TEMPLATE_LEARN_CONFIDENCE:
                learned.append((run, glyph))
        return learned

    def recognize_score_from_templates(
        self,
        crop_bgr: np.ndarray,
        templates: dict[str, list[np.ndarray]],
    ) -> OcrResult:
        if crop_bgr is None or crop_bgr.size == 0:
            return OcrResult(
                raw_text="",
                confidence=None,
                error="Empty crop (region out of frame bounds?)",
            )
        _, _, glyphs = _extract_score_glyphs(crop_bgr)
        if not glyphs:
            return OcrResult(raw_text="", confidence=None, error=None)

        digits = []
        confidences = []
        for glyph in glyphs:
            best_digit = None
            best_score = 0.0
            for digit, examples in templates.items():
                scores = [
                    1.0 - float(
                        np.mean(cv2.absdiff(glyph, example))
                    ) / 255.0
                    for example in examples
                ]
                score = float(np.median(scores))
                if score > best_score:
                    best_digit = digit
                    best_score = score
            if best_digit is None or best_score < 0.72:
                return OcrResult(raw_text="", confidence=best_score, error=None)
            digits.append(best_digit)
            confidences.append(best_score)
        return OcrResult(
            raw_text="".join(digits),
            confidence=min(confidences),
            error=None,
        )


def _preprocess_for_ocr(crop_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    scale = 3 if max(h, w) < 200 else 1
    if scale != 1:
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    return gray


def _longest_digit_run(text: str) -> str:
    runs = re.findall(r"\d+", text)
    return max(runs, key=len, default="")


def _first_digit(text: str) -> str:
    run = _longest_digit_run(text)
    return run[0] if run else ""


def _extract_score_glyphs(
    crop_bgr: np.ndarray,
) -> tuple[np.ndarray, list[list[int]], list[np.ndarray]]:
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY)
    _, _, stats, _ = cv2.connectedComponentsWithStats(binary)
    crop_h, crop_w = gray.shape[:2]
    boxes = []
    glyphs = []
    for x, y, w, h, _ in sorted(stats[1:], key=lambda stat: stat[0]):
        if (
            h >= max(8, round(crop_h * 0.35))
            and w >= 3
            and w <= crop_w * 0.2
            and y < crop_h * 0.5
        ):
            boxes.append([
                max(0, int(x) - 4),
                min(crop_w, int(x + w) + 4),
                max(0, int(y) - 4),
                min(crop_h, int(y + h) + 4),
            ])
            glyphs.append(_normalize_score_glyph(binary[y:y + h, x:x + w]))
    return gray, boxes, glyphs


def _normalize_score_glyph(glyph: np.ndarray) -> np.ndarray:
    target_w, target_h = 24, 32
    canvas = np.zeros((target_h, target_w), dtype=np.uint8)
    h, w = glyph.shape[:2]
    scale = min((target_w - 4) / max(w, 1), (target_h - 4) / max(h, 1))
    resized_w = max(1, round(w * scale))
    resized_h = max(1, round(h * scale))
    resized = cv2.resize(
        glyph, (resized_w, resized_h), interpolation=cv2.INTER_NEAREST
    )
    x = (target_w - resized_w) // 2
    y = (target_h - resized_h) // 2
    canvas[y:y + resized_h, x:x + resized_w] = resized
    return canvas


def _select_numeric_group(detections: list, image_width: int) -> list:
    numeric = [d for d in detections if _longest_digit_run(d[1])]
    if not numeric:
        return []
    numeric.sort(key=lambda d: min(point[0] for point in d[0]))

    groups = [[numeric[0]]]
    for detection in numeric[1:]:
        previous = groups[-1][-1]
        previous_right = max(point[0] for point in previous[0])
        current_left = min(point[0] for point in detection[0])
        previous_height = max(point[1] for point in previous[0]) - min(
            point[1] for point in previous[0]
        )
        current_height = max(point[1] for point in detection[0]) - min(
            point[1] for point in detection[0]
        )
        max_internal_gap = max(4.0, min(previous_height, current_height) * 0.6)
        if current_left - previous_right <= max_internal_gap:
            groups[-1].append(detection)
        else:
            groups.append([detection])

    image_center = image_width / 2

    def group_rank(group):
        left = min(min(point[0] for point in detection[0]) for detection in group)
        right = max(max(point[0] for point in detection[0]) for detection in group)
        digit_count = sum(len(_longest_digit_run(detection[1])) for detection in group)
        confidence = min(
            (float(detection[2]) for detection in group if len(detection) > 2),
            default=0.0,
        )
        return digit_count, confidence, -abs(((left + right) / 2) - image_center)

    return max(groups, key=group_rank)
