"""Simple score-entry calibration and its persistence/activation rules."""
import os
import sys
import tkinter as tk

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, image_utils, layout_store
from app.analysis import AnalysisSession
from app.event_bus import EventBus
from app.models import LayoutProfile, Region
from app.ocr_engine import OcrEngine, OcrResult, _extract_score_glyphs, score_templates_from_label
from app.ui.app_window import App
from app.ui.score_calibration_dialog import ScoreCalibrationDialog


@pytest.fixture
def segmented(monkeypatch):
    glyphs = [np.full((32, 24), i, dtype=np.uint8) for i in range(6)]
    monkeypatch.setattr(
        "app.ocr_engine._extract_score_glyphs",
        lambda _: (np.zeros((44, 315), dtype=np.uint8), [], glyphs),
    )
    monkeypatch.setattr(
        "app.ocr_engine._extract_combo_glyphs",
        lambda _: (np.zeros((44, 315), dtype=np.uint8), [], glyphs),
    )
    return glyphs


def test_score_entry_labels_every_glyph_without_ocr(segmented):
    learned = score_templates_from_label(np.zeros((44, 315, 3), dtype=np.uint8), "391,650")
    assert "".join(digit for digit, _ in learned) == "391650"
    assert all(glyph is segmented[i] for i, (_, glyph) in enumerate(learned))


@pytest.mark.parametrize("text", ["68", "3916509", "", "391.650", "391,65x", "39,1650"])
def test_invalid_or_misaligned_entry_is_rejected(segmented, text):
    with pytest.raises(ValueError):
        score_templates_from_label(np.zeros((44, 315, 3), dtype=np.uint8), text)


def test_leading_zero_is_not_silently_removed_to_force_alignment(segmented):
    with pytest.raises(ValueError, match="7 digits"):
        score_templates_from_label(np.zeros((44, 315, 3), dtype=np.uint8), "0,391,650")


def _region(reviewed=False, kind="score"):
    encoded = image_utils.encode_crop_to_base64_png(np.zeros((32, 24), dtype=np.uint8))
    return Region(
        label=kind.capitalize(), player="P1", kind=kind, x=0, y=0, w=315, h=44,
        digit_templates={digit: [encoded] for digit in "0123456789"},
        digit_templates_reviewed=reviewed,
        score_template_version=config.DIGIT_TEMPLATE_VERSIONS[kind],
    )


def test_unreviewed_templates_cannot_activate_fast_mode():
    region = _region()
    session = AnalysisSession(EventBus())
    session.set_regions([region])
    session.start()
    assert not session.has_complete_score_templates(region)
    result = session.process_fast_score_frame(np.zeros((44, 315, 3), dtype=np.uint8), 0, 0)
    assert result.region_results == []


def test_older_taught_templates_need_new_alignment_before_activation(monkeypatch):
    region = _region(reviewed=True)
    region.score_template_version = 0
    session = AnalysisSession(EventBus())
    session.set_regions([region])
    assert not session.uses_score_templates(region)
    monkeypatch.setattr(
        session.ocr_engine, "recognize_score_digits", lambda _: OcrResult("123", 0.99)
    )
    session.start()
    frame = np.zeros((44, 315, 3), dtype=np.uint8)
    result = session.process_background_numeric_frame(frame, 0, 0).region_results[0]
    assert result.validation_note is None
    assert result.candidate_value == 123
    assert result.accepted_value == 123
    assert [e.event_type for e in session.event_bus.events] == ["new_session", "score_observed", "score_changed"]


def test_digit_row_keeps_dark_zero_and_excludes_tall_border():
    import cv2

    crop = np.zeros((57, 374, 3), dtype=np.uint8)
    cv2.rectangle(crop, (1, 3), (370, 48), (255, 255, 255), 2)
    positions = [45, 99, 138, 177, 226, 265, 304]
    for i, x in enumerate(positions):
        color = 48 if i == 0 else 255
        crop[17:38, x:x + 15] = color
    _, boxes, glyphs = _extract_score_glyphs(crop)
    assert len(glyphs) == 7
    assert [box[0] + 4 for box in boxes] == positions
    learned = score_templates_from_label(crop, "0,165,924")
    assert [digit for digit, _ in learned] == list("0165924")
    with pytest.raises(ValueError):
        score_templates_from_label(crop, "165924")


@pytest.mark.parametrize("wide_digit_width", [31, 32, 36])
def test_wide_flat_digits_are_taught_without_border_or_commas(wide_digit_width):
    import cv2

    crop = np.zeros((36, 374, 3), dtype=np.uint8)
    cv2.rectangle(crop, (1, 1), (372, 34), (200, 200, 200), 1)
    positions = [20, 75, 115, 155, 210, 250, 290]
    for i, x in enumerate(positions):
        width = 21 if i == 1 else wide_digit_width
        crop[10:28, x:x + width] = 48 if i == 0 else 255
    crop[23:28, 60:65] = 48
    crop[23:28, 197:202] = 255
    _, boxes, glyphs = _extract_score_glyphs(crop)
    assert len(boxes) == len(glyphs) == 7
    assert [box[0] + 4 for box in boxes] == positions
    learned = score_templates_from_label(crop, "0,149,837")
    assert "".join(digit for digit, _ in learned) == "0149837"
    assert all(glyph.shape == (32, 24) for _, glyph in learned)
    with pytest.raises(ValueError, match="7 digit shapes"):
        score_templates_from_label(crop, "149837")


@pytest.mark.parametrize("bridge_brightness", [48, 96])
def test_recovers_digit_joined_to_border_without_losing_dim_zeros(bridge_brightness):
    import cv2

    crop = np.zeros((36, 374, 3), dtype=np.uint8)
    cv2.rectangle(crop, (1, 1), (372, 34), (200, 200, 200), 1)
    positions = [20, 75, 115, 155, 210, 250, 290]
    for i, x in enumerate(positions):
        width = 21 if i == 4 else 31
        crop[10:28, x:x + width] = 48 if i < 2 else 255
    crop[2:10, 125:128] = bridge_brightness
    crop[23:28, 60:65] = 48
    crop[23:28, 197:202] = 255
    _, boxes, glyphs = _extract_score_glyphs(crop)
    assert len(boxes) == len(glyphs) == 7
    assert [box[0] + 4 for box in boxes] == positions
    learned = score_templates_from_label(crop, "0,032,197")
    assert "".join(digit for digit, _ in learned) == "0032197"


def test_tight_crop_and_single_digit_are_supported():
    crop = np.zeros((24, 80, 3), dtype=np.uint8)
    crop[1:23, 30:46] = 255
    _, boxes, glyphs = _extract_score_glyphs(crop)
    assert len(boxes) == len(glyphs) == 1
    assert score_templates_from_label(crop, "1")[0][0] == "1"


def test_dim_and_bright_glyphs_have_same_normalized_shape():
    crop = np.zeros((40, 100, 3), dtype=np.uint8)
    for x, intensity in ((10, 48), (50, 240)):
        crop[8:30, x:x + 15] = intensity
        crop[11:27, x + 5:x + 10] = 0
    _, _, glyphs = _extract_score_glyphs(crop)
    assert len(glyphs) == 2
    np.testing.assert_array_equal(glyphs[0], glyphs[1])

@pytest.mark.parametrize("kind", ["score", "combo"])
def test_teaching_preserves_accepted_value_and_events(kind):
    region = _region(kind=kind)
    bus = EventBus()
    session = AnalysisSession(bus)
    session.set_regions([region])
    session.tracker_for(region).observe(391650)
    region.digit_templates_reviewed = True
    session.update_score_templates(region)
    assert session.has_complete_score_templates(region)
    assert session.tracker_for(region).accepted_value == 391650
    assert bus.events == []


def test_partial_templates_require_ocr_and_do_not_veto_correct_ocr(monkeypatch):
    region = _region(reviewed=True)
    region.digit_templates = {}
    glyphs = []
    for index, digit in enumerate("63890"):
        glyph = np.zeros((32, 24), dtype=np.uint8)
        glyph[:, index * 4:index * 4 + 4] = 255
        glyphs.append(glyph)
        region.digit_templates[digit] = [image_utils.encode_crop_to_base64_png(glyph)]
    monkeypatch.setattr(
        "app.ocr_engine._extract_score_glyphs",
        lambda _: (np.zeros((44, 315), dtype=np.uint8), [], glyphs),
    )
    engine = OcrEngine()
    monkeypatch.setattr(engine, "recognize_score_digits", lambda _: OcrResult("63890", 0.99))
    bus = EventBus()
    session = AnalysisSession(bus, engine)
    session.set_regions([region])
    session.start()
    assert not session.has_complete_score_templates(region)
    assert session.uses_score_templates(region)
    frame = np.zeros((44, 315, 3), dtype=np.uint8)
    assert session.process_fast_score_frame(frame, 0, 0).region_results == []
    result = session.process_background_numeric_frame(frame, 0, 0).region_results[0]
    assert result.candidate_value == result.accepted_value == 63890
    assert [e.payload["new_value"] for e in bus.events if e.event_type == "score_changed"] == [63890]

    glyphs[-1] = np.full((32, 24), 255, dtype=np.uint8)
    result = session.process_background_numeric_frame(frame, 1, 16).region_results[0]
    assert result.error is None
    assert result.validation_note is None
    assert result.accepted_value == 63890
    assert len(bus.events) == 3


def test_waiting_reading_is_visible_and_not_reported_as_ocr_error(monkeypatch):
    from app.ocr_engine import OcrResult

    region = _region()
    engine = OcrEngine()
    monkeypatch.setattr(
        engine, "recognize_score_digits", lambda _: OcrResult("83890", 0.5)
    )
    bus = EventBus()
    session = AnalysisSession(bus, engine)
    session.set_regions([region])
    session.tracker_for(region).observe(47388)
    session.start()
    result = session.process_numeric_frame(
        np.zeros((44, 315, 3), dtype=np.uint8), 0, 0
    ).region_results[0]
    assert result.candidate_value == 83890
    assert result.accepted_value == 47388
    assert not result.is_unreadable
    assert result.error is None
    status = App._format_numeric_status(result, session.tracker_for(region).status())
    assert status == "Waiting for repeated evidence for score 83890 (1/2)."
    assert [e.event_type for e in bus.events] == ["new_session", "score_observed"]


@pytest.mark.parametrize("kind", ["score", "combo"])
def test_reviewed_samples_survive_save_load_and_rescale(tmp_path, kind):
    profile = LayoutProfile("manual", 1280, 720, [_region(reviewed=True, kind=kind)])
    path = str(tmp_path / "manual.json")
    layout_store.save_layout_to_path(profile, path)
    loaded = layout_store.load_layout_from_path(path)
    scaled = layout_store.rescale_profile(loaded, 640, 360)
    assert scaled.regions[0].digit_templates_reviewed
    assert scaled.regions[0].score_template_version == config.DIGIT_TEMPLATE_VERSIONS[kind]
    assert scaled.regions[0].digit_templates == profile.regions[0].digit_templates


@pytest.fixture(scope="module")
def root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.mark.parametrize("kind", ["score", "combo"])
def test_dialog_has_single_score_input_and_replaces_auto_samples(root, segmented, kind):
    region = _region(kind=kind)
    original = region.to_dict()
    dialog = ScoreCalibrationDialog(root, region, np.zeros((44, 315, 3), dtype=np.uint8))
    assert dialog.title() == f"Teach {kind} - {region.label}"
    if kind == "combo":
        segmented.pop()
    dialog.score.set("39165" if kind == "combo" else "391,650")
    dialog._confirm()
    assert set(dialog.result) == set("39165" if kind == "combo" else "391650")
    assert region.to_dict() == original


def test_dialog_replaces_outdated_manual_samples(root, segmented):
    region = _region(reviewed=True)
    region.score_template_version = 0
    original = region.to_dict()
    dialog = ScoreCalibrationDialog(root, region, np.zeros((44, 315, 3), dtype=np.uint8))
    dialog.score.set("391650")
    dialog._confirm()
    assert set(dialog.result) == set("391650")
    assert region.to_dict() == original


def test_teaching_preserves_previous_distinct_examples(root, segmented):
    region = _region(reviewed=True)
    previous = [
        image_utils.encode_crop_to_base64_png(np.full((32, 24), i, dtype=np.uint8))
        for i in range(100, 104)
    ]
    region.digit_templates = {"5": previous}
    dialog = ScoreCalibrationDialog(root, region, np.zeros((44, 315, 3), dtype=np.uint8))
    dialog.score.set("391650")
    dialog._confirm()
    assert dialog.result["5"][:4] == previous
    assert len(dialog.result["5"]) == 5
    assert region.digit_templates["5"] == previous


def test_dialog_cancel_leaves_calibration_unchanged(root):
    region = _region(reviewed=True)
    original = region.to_dict()
    dialog = ScoreCalibrationDialog(root, region, np.zeros((44, 315, 3), dtype=np.uint8))
    dialog.destroy()
    assert dialog.result is None
    assert region.to_dict() == original


def test_dialog_rejects_bad_input_without_closing(root, segmented):
    dialog = ScoreCalibrationDialog(root, _region(), np.zeros((44, 315, 3), dtype=np.uint8))
    dialog.score.set("wrong")
    dialog._confirm()
    assert dialog.result is None
    assert dialog.winfo_exists()
    assert dialog.error.get()
    dialog.destroy()


@pytest.mark.parametrize("yellow_filter,fast_mode", [(False, False), (True, False), (False, True), (True, True)])
def test_combo_references_verify_ocr_but_still_require_repeated_low_confidence_evidence(
    monkeypatch, yellow_filter, fast_mode,
):
    region = _region(reviewed=True, kind="combo")
    region.combo_yellow_filter = yellow_filter
    region.combo_fast_mode = fast_mode
    session = AnalysisSession(EventBus())
    session.set_regions([region])
    assert session.uses_score_templates(region)
    calls = []

    def recognize(crop, **kwargs):
        calls.append(kwargs)
        return OcrResult("65", 0.5)

    monkeypatch.setattr(session.ocr_engine, "recognize_digits", recognize)
    monkeypatch.setattr(
        session.ocr_engine, "recognize_score_from_templates",
        lambda crop, templates, **kwargs: OcrResult("65", 0.99),
    )
    session.start()
    frame = np.zeros((44, 315, 3), dtype=np.uint8)
    first = session.process_numeric_frame(frame, 0, 0).region_results[0]
    assert first.accepted_value is None
    assert first.needs_confirmation
    second = session.process_numeric_frame(frame, 1, 100).region_results[0]
    assert second.accepted_value == 65
    assert [event.event_type for event in session.event_bus.events] == ["new_session", "combo"]
    assert calls[0].get("yellow_filter", False) == yellow_filter
    assert calls[0].get("fast_mode", False) == fast_mode


@pytest.mark.parametrize("ocr_text,confidence,template_text,accepted", [
    ("65", 0.5, "66", None),
    ("65", 0.99, "66", 65),
    ("", None, "65", None),
])
def test_combo_templates_neither_replace_ocr_nor_veto_confident_ocr(
    monkeypatch, ocr_text, confidence, template_text, accepted,
):
    region = _region(reviewed=True, kind="combo")
    session = AnalysisSession(EventBus())
    session.set_regions([region])
    monkeypatch.setattr(
        session.ocr_engine, "recognize_digits", lambda crop: OcrResult(ocr_text, confidence),
    )
    monkeypatch.setattr(
        session.ocr_engine, "recognize_score_from_templates",
        lambda crop, templates, **kwargs: OcrResult(template_text, 0.99),
    )
    session.start()
    result = session.process_numeric_frame(
        np.zeros((44, 315, 3), dtype=np.uint8), 0, 0,
    ).region_results[0]
    assert result.accepted_value == accepted
