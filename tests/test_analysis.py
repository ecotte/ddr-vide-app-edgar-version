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


def _frame(w=100, h=50):
    return np.zeros((h, w, 3), dtype=np.uint8)


def _score_region():
    return Region(label="Score", player="P1", kind="score", x=0, y=0, w=50, h=20)


def test_no_events_while_analysis_not_started():
    bus = EventBus()
    ocr = FakeOcrEngine(["100"])
    session = AnalysisSession(bus, ocr)
    session.set_regions([_score_region()])
    result = session.process_frame(_frame(), 0, 0)
    assert result is None
    assert bus.events == []


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
