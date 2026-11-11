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

    def recognize_digits(
        self, crop_bgr: np.ndarray, *, yellow_filter: bool = False, fast_mode: bool = False,
    ) -> OcrResult:
        """Read combo digits while ignoring nearby words instead of forcing them into digits."""
        self.ensure_ready()
        if self._init_error is not None:
            return OcrResult(raw_text="", confidence=None, error=self._init_error)
        if crop_bgr is None or crop_bgr.size == 0:
            return OcrResult(raw_text="", confidence=None, error="Empty crop (region out of frame bounds?)")

        try:
            processed = _preprocess_for_ocr(crop_bgr, yellow_filter=yellow_filter)
            if yellow_filter and not np.any(processed):
                return OcrResult(
                    raw_text="", confidence=None,
                    error="No yellow digit signal. Use a clearer frame or disable the yellow-digit filter.",
                )
            _, boxes, _ = _extract_combo_glyphs(crop_bgr)
            wide_digits = _has_wide_combo_digits(boxes, crop_bgr.shape[0])
            with self._recognition_lock:
                # Outlined HUD digits can be too wide for the text model. Correct
                # their proportions for OCR only, without forcing letters into digits.
                direct_images = [
                    cv2.resize(processed, None, fx=1, fy=scale, interpolation=cv2.INTER_CUBIC)
                    for scale in (2, 1.5)
                ] if wide_digits else []
                if yellow_filter or fast_mode:
                    direct_images.append(processed)
                for image in direct_images:
                    height, width = image.shape
                    direct = self._reader.recognize(
                        image,
                        horizontal_list=[[0, width, 0, height]],
                        free_list=[],
                        decoder="beamsearch",
                        detail=1,
                    )
                    if len(direct) == 1:
                        text, confidence = direct[0][1:3]
                        text = _combo_digit_text(text)
                        if (
                            0 < len(text) <= config.FIELD_MAX_DIGITS["combo"]
                            and all(digit in config.OCR_DIGIT_ALLOWLIST for digit in text)
                            and confidence is not None
                            and (
                                (fast_mode and image is processed)
                                or confidence >= config.COMBO_MIN_CONFIDENCE
                            )
                        ):
                            return OcrResult(raw_text=text, confidence=float(confidence))
                if fast_mode:
                    return OcrResult(
                        raw_text="", confidence=None,
                        error="Fast combo OCR could not isolate a number; slow detection is disabled.",
                    )
                detections = self._reader.readtext(
                    processed,
                    detail=1,
                    paragraph=False,
                )
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI, not crashed
            return OcrResult(raw_text="", confidence=None, error=f"OCR error: {exc}")

        if not detections:
            return OcrResult(raw_text="", confidence=None, error=None)

        selected = _select_numeric_group(detections, processed.shape[1])
        text = "".join(_combo_digit_text(d[1]) for d in selected)
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

        # Some HUDs squash digits horizontally; restore their vertical proportion
        # for OCR only, leaving teaching templates and displayed crops unchanged.
        if np.median([(right - left) / (bottom - top) for left, right, top, bottom in boxes]) >= 1.3:
            gray = cv2.resize(gray, None, fx=1, fy=3, interpolation=cv2.INTER_CUBIC)
            boxes = [[left, right, top * 3, bottom * 3] for left, right, top, bottom in boxes]

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

        text = "".join(detection[1] for detection in detections)
        confidences = [float(detection[2]) for detection in detections]
        confidence = min(confidences) if confidences else None
        if len(detections) != len(boxes) or any(
            len(detection[1]) != 1 or detection[1] not in config.OCR_DIGIT_ALLOWLIST
            for detection in detections
        ):
            return OcrResult(
                raw_text=text,
                confidence=confidence,
                error="Ambiguous score glyph: expected exactly one digit per box.",
            )
        return OcrResult(
            raw_text=text,
            confidence=confidence,
            error=None,
        )

    def recognize_score_from_templates(
        self,
        crop_bgr: np.ndarray,
        templates: dict[str, list[np.ndarray]],
        *,
        kind: str = "score",
    ) -> OcrResult:
        if crop_bgr is None or crop_bgr.size == 0:
            return OcrResult(
                raw_text="",
                confidence=None,
                error="Empty crop (region out of frame bounds?)",
            )
        _, _, glyphs = (
            _extract_combo_glyphs(crop_bgr) if kind == "combo" else _extract_score_glyphs(crop_bgr)
        )
        if not glyphs:
            return OcrResult(raw_text="", confidence=None, error=None)

        digits = []
        confidences = []
        ambiguous = False
        for glyph in glyphs:
            ranked = []
            for digit, examples in templates.items():
                if not examples:
                    continue
                score = max(_score_glyph_similarity(glyph, example) for example in examples)
                ranked.append((score, digit))
            ranked.sort(reverse=True)
            if len(ranked) < 2:
                return OcrResult(
                    raw_text="", confidence=None,
                    error="Score templates need at least two digit classes to assess ambiguity.",
                )
            best_score, best_digit = ranked[0]
            if best_score < 0.72 or best_score - ranked[1][0] < 0.02:
                ambiguous = True
            digits.append(best_digit)
            confidences.append(best_score)
        return OcrResult(
            raw_text="".join(digits),
            confidence=min(confidences),
            error=(
                "Ambiguous score templates: digit matches are too close or too weak; "
                "reading rejected."
                if ambiguous else None
            ),
        )


def _has_wide_combo_digits(boxes: list[list[int]], height: int) -> bool:
    return (
        2 <= len(boxes) <= config.FIELD_MAX_DIGITS["combo"]
        # Narrow ones must not hide a wide-font row such as 011.
        and any((right - left) / (bottom - top) >= .75 for left, right, top, bottom in boxes)
        and np.median([bottom - top for _, _, top, bottom in boxes]) >= height * .6
    )


def combo_frame_quality(crop_bgr: np.ndarray) -> float:
    """Cheap frame-selection evidence only; never recognizes or accepts a number."""
    if crop_bgr.size == 0:
        return 0.0
    _, boxes, _ = _extract_combo_glyphs(crop_bgr)
    if not _has_wide_combo_digits(boxes, crop_bgr.shape[0]):
        return 0.0
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    return min(
        float(gray[top:bottom, left:right].std()) / 127.5
        for left, right, top, bottom in boxes
    )


def _preprocess_for_ocr(crop_bgr: np.ndarray, *, yellow_filter: bool = False) -> np.ndarray:
    if yellow_filter:
        blue, green, red = cv2.split(crop_bgr)
        # Yellow has both red and green, unlike white glow or red/pink arrows.
        gray = cv2.subtract(np.minimum(red, green), blue)
    else:
        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    scale = 3 if max(h, w) < 200 else 1
    if scale != 1:
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    return gray


def _combo_digit_text(text: str) -> str:
    compact = "".join(text.split())
    return compact if re.fullmatch(r"[0-9]+", compact) else ""


def _extract_score_glyphs(
    crop_bgr: np.ndarray,
) -> tuple[np.ndarray, list[list[int]], list[np.ndarray]]:
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    crop_h, crop_w = gray.shape[:2]
    candidates = []
    # Keep dim zeroes, then recover bright digits joined to HUD borders by shadows.
    for threshold in (32, 64, 128):
        _, binary = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
        _, _, stats, _ = cv2.connectedComponentsWithStats(binary)
        for stat in stats[1:]:
            x, y, w, h, _ = stat
            if h < 8 or not 3 <= w <= h * 2:
                continue
            duplicate = any(
                abs(int(other[3]) - int(h)) <= max(other[3], h) * 0.2
                and abs(int(other[1] + other[3]) - int(y + h)) <= max(other[3], h) * 0.2
                and max(int(other[0]), int(x)) < min(int(other[0] + other[2]), int(x + w))
                for other in candidates
            )
            if not duplicate:
                candidates.append(stat)
    rows = [
        [
            other for other in candidates
            if abs(int(other[3]) - int(anchor[3])) <= anchor[3] * 0.2
            and abs(int(other[1] + other[3]) - int(anchor[1] + anchor[3])) <= anchor[3] * 0.2
        ]
        for anchor in candidates
    ]
    row = max(rows, key=len, default=[])
    boxes = []
    glyphs = []
    for x, y, w, h, _ in sorted(row, key=lambda stat: stat[0]):
        boxes.append([
            max(0, int(x) - 4),
            min(crop_w, int(x + w) + 4),
            max(0, int(y) - 4),
            min(crop_h, int(y + h) + 4),
        ])
        ink = gray[y:y + h, x:x + w]
        # The low detection threshold finds dim zeroes but also JPEG shadows.
        # Normalize contrast per digit, then trim shadows before fitting.
        _, ink = cv2.threshold(ink, float(ink.max()) * 0.4, 255, cv2.THRESH_BINARY)
        left, top, width, height = cv2.boundingRect(ink)
        glyphs.append(_normalize_score_glyph(ink[top:top + height, left:left + width]))
    return gray, boxes, glyphs


def _extract_combo_glyphs(
    crop_bgr: np.ndarray,
) -> tuple[np.ndarray, list[list[int]], list[np.ndarray]]:
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    original_height, original_width = gray.shape
    if original_height < 128:
        gray = cv2.resize(
            gray, (max(1, round(original_width * 128 / original_height)), 128),
            interpolation=cv2.INTER_CUBIC,
        )
    height, width = gray.shape
    mask = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 5,
    )
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if (
            h >= max(8, height * 0.35) and 3 <= w <= h * 1.8
            and x > 0 and y > 0 and x + w < width and y + h < height
        ):
            candidates.append((x, y, w, h))
    rows = []
    for anchor in candidates:
        aligned = [
            box for box in candidates
            if abs(box[3] - anchor[3]) <= anchor[3] * 0.2
            and abs(box[1] + box[3] - anchor[1] - anchor[3]) <= anchor[3] * 0.2
        ]
        selected = []
        # Reject overlapping contours, including partial background overlaps,
        # before creating the non-overlapping boxes required by glyph extraction.
        for box in sorted(aligned, key=lambda box: box[2] * box[3], reverse=True):
            if not any(
                min(box[0] + box[2], other[0] + other[2]) - max(box[0], other[0])
                > 0 for other in selected
            ):
                selected.append(box)
        rows.append(selected)
    row = max(rows, key=lambda boxes: (len(boxes), sum(box[3] for box in boxes)), default=[])
    boxes = [
        [round(x * original_width / width), round((x + w) * original_width / width),
         round(y * original_height / height), round((y + h) * original_height / height)]
        for x, y, w, h in sorted(row)
    ]
    mask = cv2.resize(mask, (original_width, original_height), interpolation=cv2.INTER_NEAREST)
    boxes = [
        box for box in boxes
        if np.any(mask[box[2]:box[3], box[0]:box[1]])
    ]
    return mask, boxes, _combo_glyphs_from_boxes(mask, boxes)


def _combo_glyphs_from_boxes(mask: np.ndarray, boxes: list[list[int]]) -> list[np.ndarray]:
    glyphs = []
    previous_right = 0
    for left, right, top, bottom in boxes:
        if not (previous_right <= left < right <= mask.shape[1] and 0 <= top < bottom <= mask.shape[0]):
            raise ValueError("Digit boxes must be inside the image, non-overlapping, and ordered left to right.")
        ink = mask[top:bottom, left:right]
        if not np.any(ink):
            raise ValueError("A digit box contains no outline. Adjust it around a visible digit.")
        glyphs.append(_normalize_score_glyph(ink))
        previous_right = right
    return glyphs


def score_templates_from_label(
    crop_bgr: np.ndarray, score: str, *, kind: str = "score",
    boxes: list[list[int]] | None = None,
) -> list[tuple[str, np.ndarray]]:
    text = score.strip()
    if not re.fullmatch(r"[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+", text):
        raise ValueError("Enter digits only, optionally with thousands separators.")
    digits = text.replace(",", "")
    if crop_bgr is None or crop_bgr.size == 0:
        raise ValueError("No number image is available. Display a clear number frame first.")
    if kind == "combo":
        mask, _, glyphs = _extract_combo_glyphs(crop_bgr)
        if boxes is not None:
            glyphs = _combo_glyphs_from_boxes(mask, boxes)
        if len(digits) > config.FIELD_MAX_DIGITS["combo"]:
            raise ValueError("A combo can contain at most five digits.")
    else:
        _, _, glyphs = _extract_score_glyphs(crop_bgr)
    if not glyphs:
        raise ValueError("No digit shapes found. Try a clearer frame or a tighter number region.")
    if len(digits) != len(glyphs):
        raise ValueError(
            f"Found {len(glyphs)} digit shapes, but the entered number has {len(digits)} digits. "
            "Include visible leading zeros, or choose a clearer frame/tighter region."
        )
    return list(zip(digits, glyphs))


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


def _score_glyph_similarity(glyph: np.ndarray, example: np.ndarray) -> float:
    foreground = glyph > 0
    reference = example > 0
    union = np.count_nonzero(foreground | reference)
    if union == 0:
        return 1.0
    return float(np.count_nonzero(foreground & reference) / union)


def _select_numeric_group(detections: list, image_width: int) -> list:
    numeric = [d for d in detections if _combo_digit_text(d[1])]
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
        digit_count = sum(len(_combo_digit_text(detection[1])) for detection in group)
        confidence = min(
            (float(detection[2]) for detection in group if len(detection) > 2),
            default=0.0,
        )
        return digit_count, confidence, -abs(((left + right) / 2) - image_center)

    return max(groups, key=group_rank)
