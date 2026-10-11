"""Tests for app.analysis.AnalysisSession: the per-frame pipeline glue.

Uses a fake OCR engine (no real EasyOCR/torch dependency) so these tests
are fast, deterministic, and make no claims about real OCR accuracy.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest

from app import config, image_utils
from app.analysis import AnalysisSession, _has_complete_score_templates
from app.event_bus import EventBus
from app.models import Region
from app.ocr_engine import OcrEngine, OcrResult


class FakeOcrEngine(OcrEngine):
    """Returns canned text per call instead of running real OCR."""

    def __init__(self, script, confidence=0.9):
        super().__init__()
        self._script = list(script)  # list of raw text strings to return, in order
        self._i = 0
        self._confidence = confidence

    def recognize_digits(self, crop_bgr):
        text = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return OcrResult(raw_text=text, confidence=self._confidence, error=None)

    def recognize_score_digits(self, crop_bgr):
        return self.recognize_digits(crop_bgr)

    def recognize_score_from_templates(self, crop_bgr, templates):
        return OcrResult(self._script[max(0, self._i - 1)], self._confidence)


def _frame(w=100, h=50):
    return np.zeros((h, w, 3), dtype=np.uint8)


def _score_region():
    return Region(label="Score", player="P1", kind="score", x=0, y=0, w=50, h=20)


def _combo_region():
    return Region(label="Combo", player="P1", kind="combo", x=0, y=0, w=50, h=20)


def _confirmed_events(bus):
    return [event for event in bus.events if event.event_type != "score_observed"]


def test_no_events_while_analysis_not_started():
    bus = EventBus()
    ocr = FakeOcrEngine(["100"])
    session = AnalysisSession(bus, ocr)
    session.set_regions([_score_region()])
    result = session.process_frame(_frame(), 0, 0)
    assert result is None
    assert bus.events == []


@pytest.mark.parametrize("confidence", [0.90, 0.995])
def test_high_confidence_score_advances_without_repeated_values(confidence):
    bus = EventBus()
    session = AnalysisSession(bus, FakeOcrEngine(["100", "110", "120"], confidence))
    session.set_regions([_score_region()])
    session.start()

    for i, expected in enumerate((100, 110, 120)):
        result = session.process_numeric_frame(_frame(), i, i * 33)
        assert result.region_results[0].accepted_value == expected
        assert result.region_results[0].validation_note is None
    assert [event.payload["new_value"] for event in _confirmed_events(bus)] == [100, 110, 120]


def test_score_just_below_direct_threshold_still_requires_confirmation():
    bus = EventBus()
    session = AnalysisSession(bus, FakeOcrEngine(["100"], confidence=0.899999))
    session.set_regions([_score_region()])
    session.start()
    first = session.process_numeric_frame(_frame(), 0, 0)
    assert first.region_results[0].accepted_value is None
    assert _confirmed_events(bus) == []
    session.process_numeric_frame(_frame(), 1, 33)
    assert [event.payload["new_value"] for event in _confirmed_events(bus)] == [100]


@pytest.mark.parametrize("confidence", [0.90, 0.995])
def test_high_confidence_combo_advances_without_repeated_values(confidence):
    bus = EventBus()
    session = AnalysisSession(bus, FakeOcrEngine(["4", "5"], confidence))
    session.set_regions([_combo_region()])
    session.start()

    session.process_numeric_frame(_frame(), 0, 0)
    assert [event.payload["new_value"] for event in bus.events] == [4]
    session.process_numeric_frame(_frame(), 1, 33)
    assert [event.payload["new_value"] for event in bus.events] == [4, 5]
    session.process_numeric_frame(_frame(), 2, 66)

    assert [event.payload["new_value"] for event in bus.events] == [4, 5]


def test_low_confidence_initial_combo_does_not_poison_baseline(monkeypatch):
    bus = EventBus()
    engine = FakeOcrEngine([])
    readings = iter([
        OcrResult("900", 0.60), OcrResult("10", 0.95),
        OcrResult("11", 0.99), OcrResult("11", 0.99),
    ])
    monkeypatch.setattr(engine, "recognize_digits", lambda _: next(readings))
    session = AnalysisSession(bus, engine)
    region = _combo_region()
    session.set_regions([region])
    session.start()

    session.process_numeric_frame(_frame(), 0, 0)
    session.process_numeric_frame(_frame(), 1, 33)
    session.process_numeric_frame(_frame(), 2, 66)
    session.process_numeric_frame(_frame(), 3, 99)

    assert session.tracker_for(region).accepted_value == 11
    assert [event.payload["new_value"] for event in bus.events] == [10, 11]


def test_fast_score_templates_require_three_examples_of_every_digit():
    complete = {
        digit: [np.zeros((2, 2), dtype=np.uint8) for _ in range(3)]
        for digit in "0123456789"
    }
    incomplete = {digit: list(examples) for digit, examples in complete.items()}
    incomplete["6"] = incomplete["6"][:2]

    assert _has_complete_score_templates(complete) is True
    assert _has_complete_score_templates(incomplete) is False


def test_template_score_changes_require_independent_background_ocr():
    encoded = image_utils.encode_crop_to_base64_png(
        np.zeros((32, 24), dtype=np.uint8)
    )
    region = _score_region()
    region.digit_templates = {
        digit: [encoded, encoded, encoded] for digit in "0123456789"
    }
    region.digit_templates_reviewed = True
    region.score_template_version = config.SCORE_TEMPLATE_VERSION
    bus = EventBus()
    session = AnalysisSession(bus, FakeOcrEngine(["100", "200"]))
    session.set_regions([region])
    session.start()

    session.process_fast_score_frame(_frame(), 0, 0)
    session.process_fast_score_frame(_frame(), 1, 33)
    assert bus.events == []
    assert session.ocr_engine._i == 0
    session.process_background_numeric_frame(_frame(), 0, 0)
    session.process_background_numeric_frame(_frame(), 1, 33)

    assert [event.payload["new_value"] for event in _confirmed_events(bus)] == [100, 200]


def test_ambiguous_high_template_score_does_not_poison_later_valid_readings(monkeypatch):
    encoded = image_utils.encode_crop_to_base64_png(
        np.zeros((32, 24), dtype=np.uint8)
    )
    region = _score_region()
    region.digit_templates = {
        digit: [encoded] * 3 for digit in "0123456789"
    }
    region.digit_templates_reviewed = True
    region.score_template_version = config.SCORE_TEMPLATE_VERSION
    engine = FakeOcrEngine(["391650", "391650"])
    results = iter([
        OcrResult("18866400", 0.98, error="Ambiguous score templates"),
        OcrResult("391650", 0.98),
    ])
    monkeypatch.setattr(
        engine, "recognize_score_from_templates", lambda *_: next(results)
    )
    bus = EventBus()
    session = AnalysisSession(bus, engine)
    session.set_regions([region])
    session.start()

    result = session.process_background_numeric_frame(_frame(), 0, 0)
    assert result.region_results[0].accepted_value == 391650
    session.process_background_numeric_frame(_frame(), 1, 33)

    assert [event.payload["new_value"] for event in _confirmed_events(bus)] == [391650]


def test_score_never_accepts_or_emits_a_decreasing_value():
    bus = EventBus()
    session = AnalysisSession(bus, FakeOcrEngine(["100", "100", "90", "110", "110"]))
    region = _score_region()
    session.set_regions([region])
    session.start()

    session.process_numeric_frame(_frame(), 0, 0)
    session.process_numeric_frame(_frame(), 1, 33)
    rejected = session.process_numeric_frame(_frame(), 2, 66)
    session.process_numeric_frame(_frame(), 3, 99)
    session.process_numeric_frame(_frame(), 4, 132)

    assert [event.payload["new_value"] for event in _confirmed_events(bus)] == [100, 110]
    assert rejected.region_results[0].accepted_value == 100
    assert rejected.region_results[0].is_unreadable is False
    assert rejected.region_results[0].candidate_value == 90
    assert rejected.region_results[0].error is None
    assert "Ignored decreasing score reading" in rejected.region_results[0].validation_note


def test_lower_confidence_score_requires_repeated_evidence():
    bus = EventBus()
    session = AnalysisSession(bus, FakeOcrEngine(["999650", "999650"], confidence=0.54))
    region = _score_region()
    session.set_regions([region])
    session.start()

    first = session.process_numeric_frame(_frame(), 0, 1000)
    session.process_numeric_frame(_frame(), 1, 1200)

    assert first.region_results[0].accepted_value is None
    assert first.region_results[0].candidate_value == 999650
    assert not first.region_results[0].is_unreadable
    assert first.region_results[0].error is None
    assert "Waiting for repeated evidence" in first.region_results[0].validation_note
    assert session.tracker_for(region).accepted_value == 999650
    assert [event.payload["new_value"] for event in _confirmed_events(bus)] == [999650]


def test_single_lower_confidence_transition_error_is_not_accepted():
    bus = EventBus()
    session = AnalysisSession(
        bus, FakeOcrEngine(["997580", "999622", "999650", "999650"], confidence=0.54)
    )
    region = _score_region()
    session.set_regions([region])
    session.start()

    for frame_index, time_ms in enumerate((1000, 1200, 1400, 1600)):
        session.process_numeric_frame(_frame(), frame_index, time_ms)

    assert session.tracker_for(region).accepted_value == 999650
    assert [event.payload["new_value"] for event in _confirmed_events(bus)] == [999650]


def test_genuine_change_emits_exactly_one_event():
    bus = EventBus()
    ocr = FakeOcrEngine(["100", "100", "200", "200"])
    session = AnalysisSession(bus, ocr)
    session.set_required_consecutive_frames(2)
    session.set_regions([_score_region()])
    session.start()

    session.process_frame(_frame(), 0, 0)
    session.process_frame(_frame(), 1, 33)  # "100" accepted here
    session.process_frame(_frame(), 2, 66)
    session.process_frame(_frame(), 3, 99)  # "200" accepted here

    events = _confirmed_events(bus)
    assert len(events) == 2
    assert events[0].event_type == "score_changed"
    assert events[0].payload == {
        "field": "score", "region_label": "Score", "previous_value": None, "new_value": 100
    }
    assert events[1].payload["previous_value"] == 100
    assert events[1].payload["new_value"] == 200


def test_unreadable_ocr_text_does_not_emit_event_or_change_accepted_value():
    bus = EventBus()
    ocr = FakeOcrEngine(["100", "100", "", "", ""])
    session = AnalysisSession(bus, ocr)
    session.set_required_consecutive_frames(2)
    session.set_regions([_score_region()])
    session.start()

    session.process_frame(_frame(), 0, 0)
    session.process_frame(_frame(), 1, 33)  # accepted 100
    r = session.process_frame(_frame(), 2, 66)  # unreadable
    session.process_frame(_frame(), 3, 99)  # unreadable
    session.process_frame(_frame(), 4, 132)  # unreadable

    assert len(_confirmed_events(bus)) == 1  # only the initial acceptance
    assert r.region_results[0].is_unreadable is True
    assert r.region_results[0].accepted_value == 100  # previous value retained, not zeroed


def test_seek_reset_causes_next_value_to_report_as_change_from_unknown():
    bus = EventBus()
    ocr = FakeOcrEngine(["100", "100", "100", "100"])
    session = AnalysisSession(bus, ocr)
    session.set_required_consecutive_frames(2)
    session.set_regions([_score_region()])
    session.start()

    session.process_frame(_frame(), 0, 0)
    session.process_frame(_frame(), 1, 33)  # accepted 100 -> 1 event so far
    assert len(_confirmed_events(bus)) == 1

    # Simulate what the controller does on a detected seek.
    session.reset_tracking()

    session.process_frame(_frame(), 50, 1650)
    session.process_frame(_frame(), 51, 1683)  # accepted again, from unknown

    assert len(_confirmed_events(bus)) == 2
    assert _confirmed_events(bus)[1].payload["previous_value"] is None
    assert _confirmed_events(bus)[1].payload["new_value"] == 100


def test_recording_disabled_suppresses_events_but_keeps_tracking_state():
    bus = EventBus()
    ocr = FakeOcrEngine(["100", "100", "200", "200"])
    session = AnalysisSession(bus, ocr)
    session.set_required_consecutive_frames(2)
    session.set_regions([_score_region()])
    session.start()
    session.recording_enabled = False

    session.process_frame(_frame(), 0, 0)
    r = session.process_frame(_frame(), 1, 33)  # would accept 100, but recording is off

    assert bus.events == []
    assert r.region_results[0].accepted_value == 100  # tracker still updated internally


def test_new_regions_resets_tracking():
    bus = EventBus()
    ocr = FakeOcrEngine(["100", "100"])
    session = AnalysisSession(bus, ocr)
    session.set_required_consecutive_frames(2)
    region = _score_region()
    session.set_regions([region])
    session.start()
    session.process_frame(_frame(), 0, 0)
    session.process_frame(_frame(), 1, 33)
    assert session.tracker_for(region).accepted_value == 100

    session.set_regions([region])  # re-applying regions should reset trackers
    assert session.tracker_for(region).accepted_value is None


def test_combo_rejects_large_overlay_jump_and_keeps_accepted_value():
    bus = EventBus()
    ocr = FakeOcrEngine(["100", "100", "7100", "7100"])
    session = AnalysisSession(bus, ocr)
    session.set_required_consecutive_frames(2)
    region = _combo_region()
    session.set_regions([region])
    session.start()

    session.process_frame(_frame(), 0, 0)
    session.process_frame(_frame(), 1, 33)
    result = session.process_frame(_frame(), 2, 66)
    session.process_frame(_frame(), 3, 99)

    assert session.tracker_for(region).accepted_value == 100
    assert len(bus.events) == 1
    assert result.region_results[0].is_unreadable is False
    assert result.region_results[0].candidate_value == 7100
    assert result.region_results[0].error is None
    assert "Ignored implausible combo reading" in result.region_results[0].validation_note


def test_combo_rejects_decrease_after_occlusion():
    bus = EventBus()
    ocr = FakeOcrEngine(["100", "100", "4", "4"])
    session = AnalysisSession(bus, ocr)
    session.set_required_consecutive_frames(2)
    region = _combo_region()
    session.set_regions([region])
    session.start()

    for frame_index in range(4):
        session.process_frame(_frame(), frame_index, frame_index * 33)

    assert session.tracker_for(region).accepted_value == 100
    assert [event.payload["new_value"] for event in bus.events] == [100]


def test_split_frame_processing_keeps_arrow_and_numeric_regions_separate():
    bus = EventBus()
    ocr = FakeOcrEngine(["100"])
    session = AnalysisSession(bus, ocr)
    score = _score_region()
    arrow = Region(
        label="P1 Up", player="P1", kind="arrow",
        x=50, y=0, w=20, h=20, direction="Up",
    )
    session.set_regions([score, arrow])
    session.start()

    arrow_result = session.process_arrow_frame(_frame(), 0, 0)
    numeric_result = session.process_numeric_frame(_frame(), 0, 0)

    assert [result.region.kind for result in arrow_result.region_results] == ["arrow"]
    assert [result.region.kind for result in numeric_result.region_results] == ["score"]


def test_wrong_template_does_not_override_correct_score_ocr(monkeypatch):
    region = _score_region()
    region.digit_templates = {
        "0": [image_utils.encode_crop_to_base64_png(np.zeros((32, 24), dtype=np.uint8))]
    }
    region.digit_templates_reviewed = True
    region.score_template_version = config.SCORE_TEMPLATE_VERSION
    engine = FakeOcrEngine(["0237052"], confidence=0.995)
    monkeypatch.setattr(
        engine, "recognize_score_from_templates", lambda *_: OcrResult("0333059", 0.99)
    )
    bus = EventBus()
    session = AnalysisSession(bus, engine)
    session.set_regions([region])
    session.start()
    first = session.process_background_numeric_frame(_frame(), 100, 1000)
    assert first.region_results[0].candidate_value == 237052
    assert first.region_results[0].accepted_value == 237052
    session.process_background_numeric_frame(_frame(), 101, 1033)
    assert [e.payload["new_value"] for e in _confirmed_events(bus)] == [237052]


def test_duplicate_or_out_of_order_frame_cannot_confirm_score():
    bus = EventBus()
    session = AnalysisSession(bus, FakeOcrEngine(["237052"], confidence=0.5))
    session.set_regions([_score_region()])
    session.start()
    for index, timestamp in ((10, 1000), (10, 1000), (9, 900), (11, 1000)):
        session.process_numeric_frame(_frame(), index, timestamp)
    assert _confirmed_events(bus) == []
    session.process_numeric_frame(_frame(), 12, 1033)
    assert [e.payload["new_value"] for e in _confirmed_events(bus)] == [237052]


def test_combo_low_confidence_and_occlusion_break_confirmation(monkeypatch):
    engine = FakeOcrEngine([])
    readings = iter([
        OcrResult("601", 0.99),
        OcrResult("104", 0.627),
        OcrResult("601", 0.99),
        OcrResult("", None),
        OcrResult("101", 0.99),
        OcrResult("101", 0.99),
    ])
    monkeypatch.setattr(engine, "recognize_digits", lambda _: next(readings))
    bus = EventBus()
    session = AnalysisSession(bus, engine)
    session.set_required_consecutive_frames(2)
    session.set_regions([_combo_region()])
    session.start()
    for i in range(5):
        result = session.process_numeric_frame(_frame(), i, i * 33)
        assert result.new_events == []
    result = session.process_numeric_frame(_frame(), 5, 165)
    assert result.region_results[0].accepted_value == 101
    assert [e.payload["new_value"] for e in bus.events] == [101]


def test_immediate_combo_acceptance_still_rejects_jumps_and_decreases():
    bus = EventBus()
    session = AnalysisSession(bus, FakeOcrEngine(["65", "654", "64", "66"], confidence=0.99))
    session.set_regions([_combo_region()])
    session.start()
    for i in range(4):
        session.process_numeric_frame(_frame(), i, i * 33)
    assert [e.payload["new_value"] for e in bus.events] == [65, 66]


def test_combo_just_below_confidence_floor_is_not_accepted():
    bus = EventBus()
    session = AnalysisSession(bus, FakeOcrEngine(["65"], confidence=0.899999))
    session.set_regions([_combo_region()])
    session.start()
    for i in range(2):
        result = session.process_numeric_frame(_frame(), i, i * 33)
        assert result.region_results[0].accepted_value is None
        assert "low-confidence combo" in result.region_results[0].validation_note
    assert bus.events == []


def test_confirmation_expires_and_respects_consistency_setting():
    bus = EventBus()
    session = AnalysisSession(bus, FakeOcrEngine(["100"]))
    session.set_required_consecutive_frames(3)
    session.set_regions([_score_region()])
    session.start()
    for i, timestamp in enumerate((0, 33, 2100, 2133)):
        session.process_numeric_frame(_frame(), i, timestamp)
    assert _confirmed_events(bus) == []
    session.process_numeric_frame(_frame(), 4, 2166)
    assert [e.payload["new_value"] for e in _confirmed_events(bus)] == [100]


def test_weak_score_ocr_requires_both_template_agreement_and_repeated_frames(monkeypatch):
    region = _score_region()
    region.digit_templates = {
        "6": [image_utils.encode_crop_to_base64_png(np.zeros((32, 24), dtype=np.uint8))]
    }
    region.digit_templates_reviewed = True
    region.score_template_version = config.SCORE_TEMPLATE_VERSION
    engine = FakeOcrEngine(["999050", "999650", "999650"], confidence=0.284)
    monkeypatch.setattr(
        engine, "recognize_score_from_templates", lambda *_: OcrResult("999650", 0.99)
    )
    bus = EventBus()
    session = AnalysisSession(bus, engine)
    session.set_regions([region])
    session.start()
    for i in range(2):
        session.process_background_numeric_frame(_frame(), i, i * 33)
        assert _confirmed_events(bus) == []
    session.process_background_numeric_frame(_frame(), 2, 66)
    assert [e.payload["new_value"] for e in _confirmed_events(bus)] == [999650]
    assert _confirmed_events(bus)[0].confidence == 0.284
