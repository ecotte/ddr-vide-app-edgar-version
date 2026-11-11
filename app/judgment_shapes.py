"""Cached, color-derived lettering search for judgment reference images."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app import config
from app.recognition import JudgmentMatch


@dataclass(frozen=True)
class ShapeReference:
    hue: int
    templates: tuple[np.ndarray, ...]


def foreground_mask(image: np.ndarray, hue: int) -> np.ndarray:
    h, s, v = cv2.split(cv2.cvtColor(image, cv2.COLOR_BGR2HSV))
    distance = np.abs(h.astype(np.int16) - hue)
    distance = np.minimum(distance, 180 - distance)
    return ((distance <= 12) & (s >= 65) & (v >= 65)).astype(np.uint8)


def _half_size(mask: np.ndarray) -> np.ndarray:
    return cv2.resize(
        mask, (max(2, round(mask.shape[1] / 2)), max(2, round(mask.shape[0] / 2))),
        interpolation=cv2.INTER_NEAREST,
    )


def prepare_shape_reference(
    image: np.ndarray, *,
    horizontal_scales: tuple[float, ...] = (.70, .80, .90, 1.0, 1.10, 1.20),
    vertical_scales: tuple[float, ...] = (.75, 1.0, 1.25),
) -> ShapeReference:
    if image.size == 0:
        raise ValueError("Reference image is empty.")
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    center = hsv[image.shape[0] // 5:4 * image.shape[0] // 5]
    h, s, v = cv2.split(center)
    selected = (s >= 100) & (v >= 100)
    if not selected.any():
        raise ValueError("No saturated lettering; capture a clear colored judgment word.")
    histogram = np.bincount(
        h[selected], weights=s[selected].astype(float) ** 2 * v[selected], minlength=180,
    )
    hue = int(histogram.argmax())
    mask = foreground_mask(image, hue)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    scale_x, scale_y = image.shape[1] / 286, image.shape[0] / 53
    components = [
        index for index in range(1, count)
        if stats[index, cv2.CC_STAT_AREA] >= max(4, round(40 * scale_x * scale_y))
        and stats[index, cv2.CC_STAT_HEIGHT] >= max(2, round(6 * scale_y))
    ]
    if not components:
        raise ValueError("No usable lettering components; recapture the full word.")
    cleaned = np.isin(labels, components).astype(np.uint8)
    x, y, width, height = cv2.boundingRect(cv2.findNonZero(cleaned))
    if width < max(8, image.shape[1] * .1) or height < 3:
        raise ValueError("Lettering is too small; recapture the full word.")
    template = np.pad(
        cleaned[y:y + height, x:x + width],
        ((max(1, round(2 * scale_y)),) * 2, (max(1, round(2 * scale_x)),) * 2),
    )
    templates = []
    for sx in horizontal_scales:
        for sy in vertical_scales:
            resized = cv2.resize(
                template, (max(4, round(template.shape[1] * sx)), max(4, round(template.shape[0] * sy))),
                interpolation=cv2.INTER_NEAREST,
            )
            resized = _half_size(resized)
            if resized.min() == resized.max():
                continue
            templates.append(resized)
    if not templates:
        raise ValueError("No usable shape contrast; recapture a clear word.")
    return ShapeReference(hue, tuple(templates))


def _best_shape(mask: np.ndarray, templates: tuple[np.ndarray, ...]) -> tuple[float, tuple[int, int, int, int] | None]:
    best = 0.0
    bounds = None
    if mask.min() == mask.max():
        return best, bounds
    for template in templates:
        height, width = template.shape
        if height > mask.shape[0] or width > mask.shape[1]:
            continue
        score_map = cv2.matchTemplate(mask, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, (x, y) = cv2.minMaxLoc(score_map)
        if np.isfinite(score) and score > best:
            best = min(1.0, score)
            bounds = (x, y, width, height)
    return best, bounds


class JudgmentShapeMatcher:
    """Prepare once per calibration change; each frame searches only cached masks."""

    def __init__(self, references: dict[str, list[np.ndarray]]):
        self.references: dict[str, list[ShapeReference]] = {}
        self.warnings: list[str] = []
        categories = (
            ("perfect", "great", "good", "miss")
            if any(references.get(tier) for tier in ("perfect", "great", "good"))
            else ("hit", "miss")
        )
        for category in categories:
            for index, image in enumerate(references.get(category, []), 1):
                try:
                    prepared = prepare_shape_reference(image)
                except ValueError as exc:
                    self.warnings.append(f"{category.title()} reference {index}: {exc}")
                    continue
                self.references.setdefault(category, []).append(prepared)
        self.error = None if self.references else "No usable letter-shape references. Capture a clear, saturated judgment word."
        self.empty_masks: dict[int, tuple[np.ndarray, ...]] = {}
        # Negative backgrounds are compared in the same learned-hue mask space,
        # without requiring them to contain lettering.
        for hue in {ref.hue for refs in self.references.values() for ref in refs}:
            masks = []
            for image in references.get("empty", []):
                if image.size:
                    mask = _half_size(foreground_mask(image, hue))
                    if mask.min() != mask.max():
                        masks.append(mask)
            self.empty_masks[hue] = tuple(masks)
        if references.get("empty") and not any(self.empty_masks.values()):
            self.warnings.append("No judgment samples have no contrast in the learned lettering colors; no negative veto is available.")

    def match(self, crop: np.ndarray, threshold: float) -> JudgmentMatch:
        masks = {hue: _half_size(foreground_mask(crop, hue)) for hue in self.empty_masks}
        ranked = []
        for category, references in self.references.items():
            score, bounds, hue = max(
                ((*_best_shape(masks[ref.hue], ref.templates), ref.hue) for ref in references),
                key=lambda item: item[0],
            )
            ranked.append((score, category, bounds, hue))
        if not ranked:
            return JudgmentMatch(None, 0)
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        score, category, bounds, hue = ranked[0]
        second_score, second_category = (ranked[1][0], ranked[1][1]) if len(ranked) > 1 else (None, None)
        negatives = self.empty_masks[hue]
        empty_score = _best_shape(masks[hue], negatives)[0] if negatives else None
        suppressed = (
            score >= threshold and empty_score is not None
            and score <= empty_score + config.JUDGMENT_EMPTY_REFERENCE_MARGIN
        )
        state = (
            "idle" if score < threshold or suppressed
            else None if second_score is not None and score - second_score < config.JUDGMENT_AMBIGUITY_MARGIN
            else category
        )
        if bounds is not None:
            x, y, w, h = bounds
            sx, sy = crop.shape[1] / masks[hue].shape[1], crop.shape[0] / masks[hue].shape[0]
            bounds = (round(x * sx), round(y * sy), round(w * sx), round(h * sy))
        return JudgmentMatch(state, score, category, second_category, second_score, empty_score, suppressed, bounds)
