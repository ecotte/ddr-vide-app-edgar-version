"""Tests for app.event_bus: session/sequence bookkeeping and JSONL export."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config
from app.event_bus import EventBus


def test_sequence_increments_and_session_id_stable_within_session():
    bus = EventBus()
    session_id = bus.session_id
    e1 = bus.make_event("score_changed", {"previous_value": None, "new_value": 100}, 0, 0)
    e2 = bus.make_event("combo_changed", {"previous_value": None, "new_value": 1}, 1, 33)
    assert e1.session_id == session_id
    assert e2.session_id == session_id
    assert e1.sequence == 0
    assert e2.sequence == 1


def test_new_session_resets_sequence_and_session_id_and_clears_events():
    bus = EventBus()
    event = bus.make_event("score_changed", {"previous_value": None, "new_value": 5}, 0, 0)
    bus.record(event)
    old_session_id = bus.session_id

    bus.new_session()

    assert bus.session_id != old_session_id
    assert bus.events == []
    new_event = bus.make_event("score_changed", {"previous_value": None, "new_value": 5}, 0, 0)
    assert new_event.sequence == 0


def test_clear_events_does_not_reset_session_or_sequence():
    bus = EventBus()
    e1 = bus.make_event("score_changed", {"previous_value": None, "new_value": 5}, 0, 0)
    bus.record(e1)
    session_id_before = bus.session_id

    bus.clear_events()

    assert bus.events == []
    assert bus.session_id == session_id_before
    e2 = bus.make_event("score_changed", {"previous_value": 5, "new_value": 10}, 1, 33)
    assert e2.sequence == 1  # continues, does not restart at 0


def test_event_envelope_has_required_fields():
    bus = EventBus(source_mode=config.SOURCE_MODE_SYNTHETIC)
    event = bus.make_event(
        event_type="score_changed",
        payload={"field": "score", "previous_value": None, "new_value": 42},
        frame_index=10,
        video_time_ms=333,
        player="P1",
        confidence=0.91,
    )
    d = event.to_dict()
    for field_name in [
        "schema_version", "event_id", "session_id", "sequence", "source_mode",
        "frame_index", "video_time_ms", "observed_at_utc", "event_type",
        "player", "payload", "confidence",
    ]:
        assert field_name in d
    assert d["source_mode"] == config.SOURCE_MODE_SYNTHETIC
    assert d["player"] == "P1"
    assert d["schema_version"] == config.SCHEMA_VERSION


def test_export_jsonl_writes_one_json_object_per_line(tmp_path):
    bus = EventBus()
    for i in range(3):
        e = bus.make_event(
            "score_changed", {"previous_value": i, "new_value": i + 1}, i, i * 33
        )
        bus.record(e)

    out_path = tmp_path / "events.jsonl"
    count = bus.export_jsonl(str(out_path))
    assert count == 3

    lines = out_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 3
    for i, line in enumerate(lines):
        obj = json.loads(line)  # must be valid standalone JSON per line
        assert obj["sequence"] == i
