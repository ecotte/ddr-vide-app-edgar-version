"""Outlined combo digits use the same segmentation for teaching and verification."""
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from app import config, image_utils
from app.analysis import AnalysisSession
from app.event_bus import EventBus
from app.models import Region
from app.ocr_engine import (
    OcrEngine, OcrResult, _extract_combo_glyphs, _extract_score_glyphs, score_templates_from_label,
)
from app.ui.score_calibration_dialog import ScoreCalibrationDialog


@pytest.fixture
def combo():
    crop = cv2.imread(str(Path(__file__).parent / "fixtures" / "combo_008.png"))
    assert crop is not None
    return crop


@pytest.fixture(scope="module")
def tk_root():
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


def region(version=None):
    return Region(
        "Combo", "P1", "combo", 0, 0, 294, 91,
        digit_templates_reviewed=True,
        score_template_version=config.DIGIT_TEMPLATE_VERSIONS["combo"] if version is None else version,
    )


@pytest.mark.parametrize("scale", [0.65, 1, 1.5])
def test_real_008_finds_three_whole_digits_not_holes(combo, scale):
    crop = cv2.resize(combo, None, fx=scale, fy=scale)
    _, boxes, glyphs = _extract_combo_glyphs(crop)
    assert len(boxes) == len(glyphs) == 3
    for box, expected_left in zip(boxes, [18, 106, 189]):
        assert abs(box[0] - expected_left * scale) < 6
        assert box[3] - box[2] > crop.shape[0] * 0.6
    learned = score_templates_from_label(crop, "008", kind="combo")
    assert [digit for digit, _ in learned] == list("008")
    assert all(glyph.shape == (32, 24) for _, glyph in learned)


def test_score_segmentation_is_unchanged_for_the_regression_example(combo):
    assert len(_extract_score_glyphs(combo)[1]) == 1


@pytest.mark.parametrize("text", ["1", "08", "008", "1234", "12345"])
def test_outlined_combo_lengths_and_narrow_ones(text):
    crop = np.full((90, len(text) * 60 + 20, 3), 220, dtype=np.uint8)
    cv2.putText(crop, text, (10, 65), cv2.FONT_HERSHEY_DUPLEX, 2, (30, 30, 30), 7)
    cv2.putText(crop, text, (10, 65), cv2.FONT_HERSHEY_DUPLEX, 2, (245, 245, 245), 3)
    assert len(_extract_combo_glyphs(crop)[1]) == len(text)
    assert "".join(digit for digit, _ in score_templates_from_label(crop, text, kind="combo")) == text


def test_taught_008_matches_in_live_verification_but_still_requires_ocr_evidence(combo, monkeypatch):
    templates = {}
    for digit, glyph in score_templates_from_label(combo, "008", kind="combo"):
        templates.setdefault(digit, []).append(glyph)
    verified = OcrEngine().recognize_score_from_templates(combo, templates, kind="combo")
    assert verified.raw_text == "008"
    assert verified.error is None
    taught = region()
    taught.digit_templates = {
        digit: [image_utils.encode_crop_to_base64_png(glyph) for glyph in samples]
        for digit, samples in templates.items()
    }
    session = AnalysisSession(EventBus())
    session.set_regions([taught])
    monkeypatch.setattr(session.ocr_engine, "recognize_digits", lambda crop: OcrResult("008", 0.5))
    session.start()
    first = session.process_numeric_frame(combo, 0, 0).region_results[0]
    assert first.accepted_value is None
    assert first.needs_confirmation
    second = session.process_numeric_frame(combo, 1, 100).region_results[0]
    assert second.accepted_value == 8
    assert session.event_bus.events[-1].event_type == "combo"


def test_old_combo_templates_need_reteaching_without_invalidating_score_templates(combo):
    taught = region(version=config.SCORE_TEMPLATE_VERSION)
    taught.digit_templates = {
        digit: [image_utils.encode_crop_to_base64_png(glyph)]
        for digit, glyph in score_templates_from_label(combo, "008", kind="combo")
    }
    session = AnalysisSession(EventBus())
    session.set_regions([taught])
    assert not session.uses_score_templates(taught)
    taught.kind = "score"
    session.set_regions([taught])
    assert session.uses_score_templates(taught)


@pytest.mark.parametrize("boxes", [
    [[-1, 30, 0, 50]], [[0, 400, 0, 50]], [[30, 20, 0, 50]],
    [[20, 70, 5, 75], [60, 110, 5, 75]], [],
])
def test_manual_boxes_reject_invalid_bounds_overlap_and_empty_selection(combo, boxes):
    with pytest.raises(ValueError):
        score_templates_from_label(combo, "008", kind="combo", boxes=boxes)


def test_manual_boxes_require_one_box_per_entered_digit(combo):
    with pytest.raises(ValueError, match="entered number has 3"):
        score_templates_from_label(combo, "008", kind="combo", boxes=[[18, 90, 7, 71]])


def test_manual_box_dialog_learns_without_modifying_region_and_restores_auto(combo, tk_root):
    taught = region()
    original = taught.to_dict()
    dialog = ScoreCalibrationDialog(tk_root, taught, combo)
    try:
        dialog.score.set("008")
        dialog._reset_boxes()
        dialog._confirm()
        assert dialog.result is None
        assert dialog.error.get()

        def point(x, y):
            return SimpleNamespace(
                x=x * dialog._preview_size[0] / combo.shape[1],
                y=(y + 20) * dialog._preview_size[1] / (combo.shape[0] + 20),
            )

        for left, right, top, bottom in [[18, 90, 7, 71], [106, 178, 7, 71], [189, 269, 8, 77]]:
            dialog._start_box(point(left, top))
            dialog._finish_box(point(right, bottom))
        assert len(dialog._manual_boxes) == 3
        dialog._start_box(point(25, 10))
        dialog._finish_box(point(55, 55))
        assert "non-overlapping" in dialog.error.get()
        assert len(dialog._manual_boxes) == 3
        dialog._automatic_boxes()
        assert dialog._manual_boxes is None
        dialog._reset_boxes()
        for left, right, top, bottom in [[18, 90, 7, 71], [106, 178, 7, 71], [189, 269, 8, 77]]:
            dialog._start_box(point(left, top))
            dialog._finish_box(point(right, bottom))
        dialog._confirm()
        assert set(dialog.result) == {"0", "8"}
        assert taught.to_dict() == original
    finally:
        if dialog.winfo_exists():
            dialog.destroy()


@pytest.mark.parametrize("scale", [1, 3])
def test_manual_box_drag_shows_live_scaled_rectangle_before_release(combo, scale, tk_root):
    crop = cv2.resize(combo, None, fx=scale, fy=scale)
    dialog = ScoreCalibrationDialog(tk_root, region(), crop)
    try:
        canvas = dialog._image_label
        assert canvas.bind("<B1-Motion>")
        dialog._reset_boxes()
        assert canvas["cursor"] == "crosshair"
        h, w = crop.shape[:2]

        def point(x, y):
            return SimpleNamespace(
                x=x * dialog._preview_size[0] / w,
                y=(y + 20) * dialog._preview_size[1] / (h + 20),
            )

        start, middle, end = [point(x * scale, y * scale) for x, y in [(18, 7), (50, 40), (90, 71)]]
        dialog._start_box(start)
        rectangle, = canvas.find_withtag("digit-drag")
        dialog._drag_box(middle)
        assert canvas.coords(rectangle) == pytest.approx([start.x, start.y, middle.x, middle.y])
        assert dialog._manual_boxes == []
        dialog._drag_box(end)
        assert canvas.find_withtag("digit-drag") == (rectangle,)
        assert canvas.coords(rectangle) == pytest.approx([start.x, start.y, end.x, end.y])
        dialog._finish_box(end)
        assert not canvas.find_withtag("digit-drag")
        assert dialog._manual_boxes == [[18 * scale, 90 * scale, 7 * scale, 71 * scale]]

        dialog._start_box(end)
        dialog._drag_box(start)
        dialog._finish_box(start)
        assert "non-overlapping" in dialog.error.get()
        assert not canvas.find_withtag("digit-drag")
        assert len(dialog._manual_boxes) == 1

        dialog._start_box(start)
        dialog._drag_box(end)
        dialog._reset_boxes()
        assert not canvas.find_withtag("digit-drag")
        assert dialog._drag_start is None
        dialog._start_box(start)
        dialog._drag_box(end)
        dialog._automatic_boxes()
        assert not canvas.find_withtag("digit-drag")
        assert canvas["cursor"] == ""
        dialog._start_box(start)
        dialog._drag_box(end)
        assert not canvas.find_withtag("digit-drag")
    finally:
        dialog.destroy()
