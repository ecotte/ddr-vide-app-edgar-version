"""Unconfirmed score readings must stay distinct from accepted changes."""
import json
from unittest.mock import Mock

import numpy as np

from app.analysis import AnalysisSession
from app.event_bus import EventBus
from app.models import Region
from app.ocr_engine import OcrEngine, OcrResult
from app.ui.events_tab import EventsTab


def _session(readings):
    engine = Mock(spec=OcrEngine)
    engine.recognize_score_digits.side_effect = readings
    bus = EventBus()
    session = AnalysisSession(bus, engine)
    session.set_regions([Region("Score", "P1", "score", 0, 0, 80, 30)])
    session.start()
    return session, bus, np.zeros((30, 80, 3), dtype=np.uint8)


def test_changing_score_emits_unconfirmed_observations_and_exports_them(tmp_path):
    session, bus, frame = _session([
        OcrResult("0428008", 0.52), OcrResult("0428508", 0.61),
    ])
    emitted = []
    for i in range(2):
        result = session.process_background_numeric_frame(frame, i, i * 17)
        assert result.region_results[0].accepted_value is None
        emitted.extend(result.new_events)
    assert emitted == bus.events
    assert [event.event_type for event in emitted] == ["score_observed"] * 2
    assert [event.payload["observed_value"] for event in emitted] == [428008, 428508]
    assert all(event.payload["confirmed"] is False for event in emitted)
    assert all("new_value" not in event.payload for event in emitted)
    assert emitted[0].payload["raw_text"] == "0428008"
    assert emitted[0].confidence == 0.52
    assert emitted[0].frame_index == 0
    assert emitted[1].video_time_ms == 17
    assert EventsTab._format_detail(emitted[0]) == "UNCONFIRMED: 428008"
    path = tmp_path / "events.jsonl"
    assert bus.export_jsonl(str(path)) == 2
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["payload"]["confirmed"] is False
    assert [row["sequence"] for row in rows] == [0, 1]


def test_observation_deduplicates_without_suppressing_confirmation():
    session, bus, frame = _session([OcrResult("100", 0.99)] * 3)
    for i in range(3):
        session.process_background_numeric_frame(frame, i, i * 17)
    assert [event.event_type for event in bus.events] == ["score_observed", "score_changed"]
    assert bus.events[1].payload["new_value"] == 100
    assert bus.events[1].payload["previous_value"] is None


def test_recording_toggle_and_reset_apply_to_observations():
    session, bus, frame = _session([OcrResult("100", 0.99)] * 3)
    session.recording_enabled = False
    session.process_background_numeric_frame(frame, 0, 0)
    assert bus.events == []
    session.recording_enabled = True
    session.process_background_numeric_frame(frame, 1, 17)
    assert [event.event_type for event in bus.events] == ["score_observed"]
    assert bus.events[0].payload["accepted_value"] == 100
    session.reset_tracking()
    session.process_background_numeric_frame(frame, 0, 0)
    assert [event.event_type for event in bus.events] == [
        "score_observed", "score_observed", "score_changed",
    ]
    assert bus.events[-2].payload["accepted_value"] is None


def test_duplicate_frame_and_unreadable_text_do_not_emit_observations():
    session, bus, frame = _session([
        OcrResult("", None), OcrResult("68", 0.2, error="Ambiguous glyph"),
    ])
    session.process_background_numeric_frame(frame, 0, 0)
    session.process_background_numeric_frame(frame, 0, 0)
    session.process_background_numeric_frame(frame, 1, 17)
    assert bus.events == []
