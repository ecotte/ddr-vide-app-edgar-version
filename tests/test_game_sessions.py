"""Game-wide GUID boundaries use calibrated, edge-triggered video evidence."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event as ThreadEvent
from unittest.mock import Mock
from uuid import UUID

import numpy as np
import pytest

from app import image_utils, layout_store
from app.analysis import AnalysisSession
from app.event_bus import EventBus
from app.models import LayoutProfile, Region
from app.ocr_engine import OcrEngine, OcrResult


def _frame(ended=False):
    frame = np.zeros((30, 80, 3), dtype=np.uint8)
    if ended:
        frame[:, 40:] = 255
    return frame


def _session():
    bus = EventBus()
    engine = Mock(spec=OcrEngine)
    engine.recognize_digits.return_value = OcrResult("10", 0.99)
    region = Region(
        "Results indicator", "Unknown", "game_end", 40, 0, 40, 30,
        reference_images={"ended": [image_utils.encode_crop_to_base64_png(_frame(True)[:, 40:])]},
    )
    session = AnalysisSession(bus, engine)
    session.set_regions([Region("Combo", "P1", "combo", 0, 0, 40, 30), region])
    session.start()
    return session, bus, region


def test_missing_id_is_created_announced_once_and_precedes_gameplay():
    bus = EventBus()
    bus.session_id = ""
    delivered = []
    bus.set_delivery_sink(delivered.append)
    event = bus.make_event("arrow_hit", {"direction": "Up-Right"}, 5, 100, "P1")
    bus.record(event)
    initial, hit = bus.events
    assert UUID(initial.session_id).version == 4
    assert initial.event_type == "new_session"
    assert initial.payload == {
        "session_id": hit.session_id, "previous_session_id": None,
        "is_new_session": True, "reason": "initial",
    }
    assert (initial.frame_index, initial.video_time_ms, initial.sequence) == (5, 100, 0)
    assert hit.sequence == 1
    assert bus.ensure_session() is None
    assert delivered == bus.events


def test_game_end_rotates_once_preserves_history_and_resets_numeric_state(tmp_path):
    session, bus, _ = _session()
    delivered = []
    bus.set_delivery_sink(delivered.append)
    first = session.process_frame(_frame(), 0, 0)
    first_id = bus.session_id
    assert first.new_events[0].event_type == "new_session"
    assert first.region_results[-1].accepted_value == 10
    assert session.process_frame(_frame(True), 1, 33).new_events == []
    boundary = session.process_frame(_frame(True), 2, 66)
    ended, event = boundary.new_events
    assert ended.event_type == "session_end"
    assert ended.session_id == first_id
    assert ended.payload["next_session_id"] == event.session_id
    assert event.event_type == "new_session"
    assert UUID(event.session_id).version == 4
    assert event.session_id != first_id
    assert event.payload["previous_session_id"] == first_id
    assert event.payload["reason"] == "game_end"
    assert (event.sequence, event.frame_index, event.video_time_ms) == (0, 2, 66)
    assert session.tracker_for(session.regions[0]).accepted_value is None
    for index in range(3, 10):
        assert session.process_frame(_frame(True), index, index * 33).new_events == []
    assert len([event for event in bus.events if event.event_type == "new_session"]) == 2
    assert all(event.session_id == first_id for event in bus.events[:-1])
    assert delivered == bus.events
    assert bus.export_jsonl(str(tmp_path / "games.jsonl")) == len(bus.events)


def test_detector_rearms_only_after_confirmed_disappearance():
    session, bus, _ = _session()
    for index in range(2):
        session.process_frame(_frame(True), index, index * 33)
    second_id = bus.session_id
    session.process_frame(_frame(), 2, 66)
    session.process_frame(_frame(True), 3, 99)
    session.process_frame(_frame(True), 4, 132)
    assert bus.session_id == second_id
    session.process_frame(_frame(), 5, 165)
    resumed = session.process_frame(_frame(), 6, 198)
    combo, = resumed.new_events
    assert combo.event_type == "combo"
    assert combo.session_id == second_id
    assert combo.payload["value"] == 10
    assert "previous_value" not in combo.payload and "new_value" not in combo.payload
    session.process_frame(_frame(True), 7, 231)
    session.process_frame(_frame(True), 8, 264)
    assert bus.session_id != second_id
    assert bus.events[-1].payload["previous_session_id"] == second_id


def test_repeated_frames_and_seeking_do_not_confirm_or_repeat_a_boundary():
    session, bus, _ = _session()
    session.process_frame(_frame(True), 10, 330)
    first_id = bus.session_id
    for _ in range(3):
        session.process_game_end_frame(_frame(True), 10, 330)
    assert bus.session_id == first_id
    session.process_game_end_frame(_frame(True), 11, 363)
    second_id = bus.session_id
    session.reset_tracking()
    session.process_game_end_frame(_frame(), 0, 0)
    session.process_game_end_frame(_frame(True), 11, 363)
    session.process_game_end_frame(_frame(True), 12, 396)
    assert bus.session_id == second_id


@pytest.mark.parametrize("out_of_bounds", [False, True])
def test_missing_reference_or_invalid_crop_reports_error_without_rotating(out_of_bounds):
    session, bus, region = _session()
    if out_of_bounds:
        region.x = 1000
    else:
        region.reference_images.clear()
    session.set_regions(session.regions)
    original = bus.session_id
    for index in range(4):
        result = session.process_game_end_frame(_frame(True), index, index * 33)
        assert result.region_results[0].error
    assert bus.session_id == original
    assert [event.event_type for event in bus.events] == ["new_session"]


def test_recording_off_still_tracks_and_announces_session_boundaries():
    session, bus, _ = _session()
    session.recording_enabled = False
    session.process_frame(_frame(), 0, 0)
    session.process_frame(_frame(True), 1, 33)
    session.process_frame(_frame(True), 2, 66)
    assert [event.event_type for event in bus.events] == ["new_session", "session_end", "new_session"]


def test_game_end_references_round_trip_and_rescale(tmp_path):
    _, _, region = _session()
    profile = LayoutProfile("Game end", 80, 30, [region])
    path = str(tmp_path / "layout.json")
    layout_store.save_layout_to_path(profile, path)
    loaded = layout_store.load_layout_from_path(path)
    assert loaded.regions[0].to_dict() == region.to_dict()
    resized = layout_store.rescale_profile(loaded, 160, 60).regions[0]
    assert resized.key() == "game_end"
    image = image_utils.decode_base64_png_to_crop(resized.reference_images["ended"][0])
    assert image.shape[:2] == (60, 80)


def test_game_end_waits_for_old_ocr_and_rejects_queued_old_session_jobs():
    session, bus, _ = _session()
    entered = ThreadEvent()
    release = ThreadEvent()
    rotating = ThreadEvent()
    session.event_bus.ensure_session()
    original_id = bus.session_id
    generation = session.generation
    session.process_game_end_frame(_frame(True), 1, 33)

    def blocking_ocr(_):
        entered.set()
        assert release.wait(5)
        return OcrResult("10", 0.99)

    session.ocr_engine.recognize_digits.side_effect = blocking_ocr

    def rotate():
        rotating.set()
        return session.process_game_end_frame(_frame(True), 2, 66)

    with ThreadPoolExecutor(max_workers=2) as pool:
        old_work = pool.submit(
            session.process_background_numeric_frame, _frame(), 0, 0,
            expected_session_id=original_id, expected_generation=generation,
        )
        assert entered.wait(5)
        boundary = pool.submit(rotate)
        try:
            assert rotating.wait(5)
            assert bus.session_id == original_id
        finally:
            release.set()
        old_result = old_work.result(timeout=5)
        boundary_result = boundary.result(timeout=5)
    assert all(event.session_id == original_id for event in old_result.new_events)
    assert bus.events[-1] == boundary_result.new_events[-1]
    assert bus.session_id != original_id
    assert session.process_background_numeric_frame(
        _frame(), 1, 33, expected_session_id=original_id, expected_generation=generation,
    ) is None


def test_invalid_game_end_settings_are_explicit():
    session, _, _ = _session()
    with pytest.raises(ValueError):
        session.set_game_end_settings(1.1, 2)
    with pytest.raises(ValueError):
        session.set_game_end_settings(0.95, 0)
