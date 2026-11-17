"""Tests for app.event_bus: session/sequence bookkeeping and JSONL export."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config
from app.event_bus import EventBus
from app.eventstream import Outbox


def test_sequence_increments_and_session_id_stable_within_session():
    bus = EventBus()
    session_id = bus.session_id
    e1 = bus.make_event("score_changed", {"previous_value": None, "new_value": 100}, 0, 0)
    e2 = bus.make_event("combo_changed", {"previous_value": None, "new_value": 1}, 1, 33)
    assert e1.session_id == session_id
    assert e2.session_id == session_id
    assert bus.events[0].event_type == "new_session"
    assert e1.sequence == 1
    assert e2.sequence == 2


def test_new_session_resets_sequence_and_session_id_and_clears_events():
    bus = EventBus()
    event = bus.make_event("score_changed", {"previous_value": None, "new_value": 5}, 0, 0)
    bus.record(event)
    old_session_id = bus.session_id

    session_event = bus.new_session()

    assert bus.session_id != old_session_id
    ended, started = bus.events
    assert started == session_event
    assert ended.event_type == "session_end"
    assert ended.session_id == old_session_id
    assert ended.sequence == event.sequence + 1
    assert ended.payload["next_session_id"] == session_event.session_id
    new_event = bus.make_event("score_changed", {"previous_value": None, "new_value": 5}, 0, 0)
    assert session_event.sequence == 0
    assert new_event.sequence == 1


def test_reset_delivers_end_before_start_and_does_not_end_an_unannounced_session():
    bus = EventBus()
    delivered = []
    bus.set_delivery_sink(delivered.append)
    bus.new_session()
    assert [event.event_type for event in delivered] == ["new_session"]
    previous = bus.session_id
    bus.new_session()
    assert [event.event_type for event in delivered] == ["new_session", "session_end", "new_session"]
    assert delivered[-2].session_id == previous
    assert delivered[-1].payload["previous_session_id"] == previous


def test_clear_events_does_not_reset_session_or_sequence():
    bus = EventBus()
    e1 = bus.make_event("score_changed", {"previous_value": None, "new_value": 5}, 0, 0)
    bus.record(e1)
    session_id_before = bus.session_id

    bus.clear_events()

    assert bus.events == []
    assert bus.session_id == session_id_before
    e2 = bus.make_event("score_changed", {"previous_value": 5, "new_value": 10}, 1, 33)
    assert e2.sequence == 2  # continues after the lifecycle event and first score


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
    assert count == 4

    lines = out_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 4
    for i, line in enumerate(lines):
        obj = json.loads(line)  # must be valid standalone JSON per line
        assert obj["sequence"] == i


@pytest.mark.parametrize("offset_hours", [-8, -7, 0, 2, 5.5])
def test_event_times_are_utc_on_the_wire_regardless_of_local_timezone(monkeypatch, tmp_path, offset_hours):
    local_now = datetime(2026, 11, 16, 16, 40, 58, 123456, tzinfo=timezone(timedelta(hours=offset_hours)))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return local_now.replace(tzinfo=None) if tz is None else local_now.astimezone(tz)

    monkeypatch.setattr("app.event_bus.datetime", Clock)
    bus = EventBus()
    delivered = []
    bus.set_delivery_sink(delivered.append)
    event = bus.make_event("judgement", {"judgment": "bad", "outcome": "hit"}, 10, 333)
    bus.record(event)
    bus.new_session(clear_events=False)
    expected = local_now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    assert [row.event_type for row in delivered] == ["new_session", "judgement", "session_end", "new_session"]
    assert all(row.observed_at_utc == expected for row in delivered)
    assert event.video_time_ms == 333
    path = tmp_path / "events.jsonl"
    bus.export_jsonl(str(path))
    assert all(json.loads(line)["observed_at_utc"] == expected for line in path.read_text().splitlines())
    with_outbox = Outbox(str(tmp_path / "outbox.sqlite3"), "test")
    try:
        for row in delivered:
            with_outbox.enqueue(row)
        while with_outbox.count():
            row_id, _, body = with_outbox.next_event()
            assert json.loads(body)["observed_at_utc"] == expected
            with_outbox.acknowledge(row_id)
    finally:
        with_outbox.close()
