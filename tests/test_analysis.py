"""Tests for app.analysis.AnalysisSession: the per-frame pipeline glue.

Uses a fake OCR engine (no real EasyOCR/torch dependency) so these tests
are fast, deterministic, and make no claims about real OCR accuracy.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from app.analysis import AnalysisSession
from app.event_bus import EventBus
from app.models import Region
from app.ocr_engine import OcrEngine, OcrResult


class FakeOcrEngine(OcrEngine):
    """Returns canned text per call instead of running real OCR."""

    def __init__(self, script):
        super().__init__()
        self._script = list(script)  # list of raw text strings to return, in order
        self._i = 0

    def recognize_digits(self, crop_bgr):
        text = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return OcrResult(raw_text=text, confidence=0.9, error=None)

    def recognize_score_digits(self, crop_bgr):
        return self.recognize_digits(crop_bgr)


def _frame(w=100, h=50):
    return np.zeros((h, w, 3), dtype=np.uint8)


def _score_region():
    return Region(label="Score", player="P1", kind="score", x=0, y=0, w=50, h=20)


def _combo_region():
    return Region(label="Combo", player="P1", kind="combo", x=0, y=0, w=50, h=20)


def test_no_events_while_analysis_not_started():
    bus = EventBus()
    ocr = FakeOcrEngine(["100"])
    session = AnalysisSession(bus, ocr)
    session.set_regions([_score_region()])
    result = session.process_frame(_frame(), 0, 0)
    assert result is None
    assert bus.events == []


def test_default_confirmation_emits_score_event_from_one_analyzed_sample():
    bus = EventBus()
    session = AnalysisSession(bus, FakeOcrEngine(["100"]))
    session.set_regions([_score_region()])
    session.start()

    session.process_numeric_frame(_frame(), 0, 0)

    assert [event.payload["new_value"] for event in bus.events] == [100]


def test_default_confirmation_emits_combo_event_from_one_analyzed_sample():
    bus = EventBus()
    session = AnalysisSession(bus, FakeOcrEngine(["4"]))
    session.set_regions([_combo_region()])
    session.start()

    session.process_numeric_frame(_frame(), 0, 0)

    assert [event.payload["new_value"] for event in bus.events] == [4]


def test_score_never_accepts_or_emits_a_decreasing_value():
    bus = EventBus()
    session = AnalysisSession(bus, FakeOcrEngine(["100", "90", "110"]))
    region = _score_region()
    session.set_regions([region])
    session.start()

    session.process_numeric_frame(_frame(), 0, 0)
    rejected = session.process_numeric_frame(_frame(), 1, 33)
    session.process_numeric_frame(_frame(), 2, 66)

    assert [event.payload["new_value"] for event in bus.events] == [100, 110]
    assert rejected.region_results[0].accepted_value == 100
    assert rejected.region_results[0].is_unreadable is True
    assert "Ignored decreasing score reading" in rejected.region_results[0].error


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

    events = bus.events
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

    assert len(bus.events) == 1  # only the initial acceptance
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
    assert len(bus.events) == 1

    # Simulate what the controller does on a detected seek.
    session.reset_tracking()

    session.process_frame(_frame(), 50, 1650)
    session.process_frame(_frame(), 51, 1683)  # accepted again, from unknown

    assert len(bus.events) == 2
    assert bus.events[1].payload["previous_value"] is None
    assert bus.events[1].payload["new_value"] == 100


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
    assert result.region_results[0].is_unreadable is True
    assert "Ignored implausible combo reading" in result.region_results[0].error


def test_combo_allows_low_reset_after_occlusion():
    bus = EventBus()
    ocr = FakeOcrEngine(["100", "100", "4", "4"])
    session = AnalysisSession(bus, ocr)
    session.set_required_consecutive_frames(2)
    region = _combo_region()
    session.set_regions([region])
    session.start()

    for frame_index in range(4):
        session.process_frame(_frame(), frame_index, frame_index * 33)

    assert session.tracker_for(region).accepted_value == 4
    assert [event.payload["new_value"] for event in bus.events] == [100, 4]


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
