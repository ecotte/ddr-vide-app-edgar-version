"""Cached colored-shape search for arrow activity, never judgment outcomes."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app import config
from app.judgment_shapes import ShapeReference, _best_shape, _half_size, foreground_mask, prepare_shape_reference


@dataclass(frozen=True)
class ArrowShapeMatch:
    state: str
    similarity: float
    empty_similarity: float | None = None
    suppressed_by_empty: bool = False
    match_bounds: tuple[int, int, int, int] | None = None


class ArrowShapeMatcher:
    def __init__(self, references: dict[str, list[np.ndarray]]):
        self.references: list[ShapeReference] = []
        self.warnings: list[str] = []
        for index, image in enumerate(references.get("active", []), 1):
            try:
                prepared = prepare_shape_reference(
                    image, horizontal_scales=(.9, 1.0, 1.1), vertical_scales=(1.0,),
                )
            except ValueError as exc:
                self.warnings.append(
                    f"Active reference {index} cannot provide a colored arrow shape: {exc} "
                    "Capture a clear colored note rather than the white receptor flash."
                )
                continue
            # Use only near-native widths and native height for a calibrated lane.
            self.references.append(prepared)
        self.error = None if self.references else "No usable arrow-shape references. Capture a clear colored active note."
        self.empty_masks: dict[int, tuple[np.ndarray, ...]] = {}
        for hue in {reference.hue for reference in self.references}:
            negatives = []
            for image in references.get("empty", []):
                if image.size:
                    mask = _half_size(foreground_mask(image, hue))
                    if mask.min() != mask.max():
                        negatives.append(mask)
            self.empty_masks[hue] = tuple(negatives)
        if references.get("empty") and not any(self.empty_masks.values()):
            self.warnings.append(
                "Empty samples contain no contrast in the learned arrow colors; "
                "no negative veto is available. Colorless receptors cannot match an active shape."
            )

    def match(self, crop: np.ndarray, threshold: float) -> ArrowShapeMatch:
        if not self.references or crop.size == 0:
            return ArrowShapeMatch("idle", 0.0)
        masks = {hue: _half_size(foreground_mask(crop, hue)) for hue in self.empty_masks}
        score, bounds, hue = max(
            ((*_best_shape(masks[reference.hue], reference.templates), reference.hue)
             for reference in self.references),
            key=lambda result: result[0],
        )
        negatives = self.empty_masks[hue]
        empty_score = _best_shape(masks[hue], negatives)[0] if negatives else None
        suppressed = (
            score >= threshold and empty_score is not None
            and score <= empty_score + config.ARROW_EMPTY_REFERENCE_MARGIN
        )
        state = "active" if score >= threshold and not suppressed else "idle"
        if bounds is not None:
            x, y, width, height = bounds
            sx, sy = crop.shape[1] / masks[hue].shape[1], crop.shape[0] / masks[hue].shape[0]
            bounds = (round(x * sx), round(y * sy), round(width * sx), round(height * sy))
        return ArrowShapeMatch(state, score, empty_score, suppressed, bounds)
