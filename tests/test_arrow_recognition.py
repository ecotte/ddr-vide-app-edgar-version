"""Tests for arrow activity and judgment-owned outcomes: image similarity, the
edge-triggered ArrowStateTracker, and the full AnalysisSession pipeline for
"arrow" regions. No real game footage or OCR model is needed - references
are small synthetic solid-color crops so behavior is fully deterministic.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest

from app import config, image_utils
from app.analysis import AnalysisSession
from app.event_bus import EventBus
from app.models import Region
from app.ocr_engine import OcrEngine
from app.recognition import ArrowStateTracker, classify_arrow_state


def _solid(color_bgr, w=20, h=20):
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, :] = color_bgr
    return arr


WHITE = _solid((255, 255, 255))
BLACK = _solid((0, 0, 0))
GREY = _solid((128, 128, 128))


# ---------------------------------------------------------------------
# image_utils.similarity
# ---------------------------------------------------------------------

def test_similarity_identical_images_is_high():
    assert image_utils.similarity(WHITE, WHITE) > 0.99


def test_similarity_different_images_is_lower():
    assert image_utils.similarity(WHITE, BLACK) < image_utils.similarity(WHITE, WHITE)


def test_similarity_resizes_mismatched_reference_instead_of_failing():
    small_white = _solid((255, 255, 255), w=10, h=10)
    score = image_utils.similarity(WHITE, small_white)
    assert score > 0.9  # still recognized as the same (upscaled) reference


def test_similarity_handles_empty_crop_gracefully():
    empty = np.zeros((0, 0, 3), dtype=np.uint8)
    assert image_utils.similarity(empty, WHITE) == 0.0
    assert image_utils.similarity(WHITE, empty) == 0.0


# ---------------------------------------------------------------------
# base64 encode/decode round trip
# ---------------------------------------------------------------------

def test_encode_decode_round_trip_preserves_pixels():
    encoded = image_utils.encode_crop_to_base64_png(WHITE)
    decoded = image_utils.decode_base64_png_to_crop(encoded)
    assert decoded is not None
    assert decoded.shape[:2] == WHITE.shape[:2]
    assert np.array_equal(decoded, WHITE)


def test_decode_invalid_data_returns_none():
    assert image_utils.decode_base64_png_to_crop("") is None
    assert image_utils.decode_base64_png_to_crop("not-base64!!") is None


# ---------------------------------------------------------------------
# classify_arrow_state
# ---------------------------------------------------------------------

def test_classify_matches_active_reference():
    refs = {"active": WHITE}
    state, score = classify_arrow_state(WHITE, refs, threshold=0.9)
    assert state == "active"
    assert score > 0.9


def test_classify_ignores_legacy_hit_and_miss_references():
    refs = {"hit": WHITE, "miss": BLACK}
    state, score = classify_arrow_state(BLACK, refs, threshold=0.9)
    assert state == "idle"
    assert classify_arrow_state(WHITE, refs, threshold=0.9)[0] == "idle"


def test_classify_uses_best_of_multiple_references_for_each_state():
    refs = {"active": [BLACK, WHITE]}
    state, score = classify_arrow_state(WHITE, refs, threshold=0.9)
    assert state == "active"
    assert score > 0.9


def test_classify_below_threshold_is_idle_not_a_guess():
    refs = {"active": [WHITE, BLACK]}
    state, score = classify_arrow_state(GREY, refs, threshold=0.9)
    assert state == "idle"


def test_classify_with_no_references_is_always_idle():
    state, score = classify_arrow_state(WHITE, {}, threshold=0.9)
    assert state == "idle"
    assert score == 0.0


@pytest.mark.parametrize("active_score,empty_score,expected", [
    (.81, .95, "idle"),
    (.95, .95, "idle"),
    (.91, .90, "idle"),
    (.92, .90, "idle"),
    (.93, .90, "active"),
    (.89, .10, "idle"),
    (.90, .10, "active"),
])
def test_active_must_pass_threshold_and_clearly_beat_empty_reference(monkeypatch, active_score, empty_score, expected):
    monkeypatch.setattr(
        image_utils, "similarity",
        lambda crop, reference: active_score if reference is WHITE else empty_score,
    )
    state, score = classify_arrow_state(GREY, {"active": [WHITE], "empty": [BLACK]}, .90)
    assert state == expected
    assert score == active_score


def test_empty_examples_use_the_best_match_and_do_not_replace_active_calibration():
    assert classify_arrow_state(GREY, {"active": [WHITE]}, .45)[0] == "active"
    assert classify_arrow_state(GREY, {"active": [WHITE], "empty": [BLACK, GREY]}, .45)[0] == "idle"
    assert classify_arrow_state(WHITE, {"active": [BLACK, WHITE], "empty": GREY}, .45)[0] == "active"
    assert classify_arrow_state(WHITE, {"empty": [WHITE]}, .45) == ("idle", 0.0)
    assert classify_arrow_state(WHITE, {"active": [WHITE], "idle": [WHITE]}, .9)[0] == "active"


def test_legacy_appearance_reference_is_ignored_even_when_it_matches():
    assert classify_arrow_state(GREY, {"appeared": [GREY]}, 0.99)[0] == "idle"
    assert classify_arrow_state(WHITE, {"active": [WHITE], "appeared": [WHITE]}, 0.99)[0] == "active"


def _timed_session():
    bus = EventBus()
    session = AnalysisSession(bus, OcrEngine())
    region = _arrow_region_with_refs()
    region.reference_images["appeared"] = [image_utils.encode_crop_to_base64_png(GREY)]
    session.set_regions([region, _judgment_region()])
    session.set_arrow_similarity_threshold(0.99)
    session.start()
    return session, bus, region


def test_legacy_appearance_does_not_emit_events_or_response_timing():
    from app.ui.events_tab import EventsTab

    session, bus, _ = _timed_session()
    session.set_arrow_min_confirm_frames(2)
    for index, (patch, timestamp) in enumerate([(GREY, 100), (GREY, 133), (WHITE, 300), (WHITE, 333)]):
        session.process_frame(_frame_with_patch(patch), index, timestamp)
    session.process_frame(_frame_with_patch(WHITE), 4, 950)
    assert [event.event_type for event in bus.events] == ["new_session", "arrow_hit"]
    hit = bus.events[-1]
    assert hit.video_time_ms == 950
    assert "appearance_video_time_ms" not in hit.payload
    assert "hit_video_time_ms" not in hit.payload
    assert "response_time_ms" not in hit.payload
    assert "response" not in EventsTab._format_detail(hit)


def test_repeated_processing_of_one_frame_cannot_confirm_an_appearance():
    session, bus, _ = _timed_session()
    session.set_arrow_min_confirm_frames(2)
    for _ in range(3):
        session.process_frame(_frame_with_patch(GREY), 1, 100)
    assert not any(event.event_type == "arrow_appeared" for event in bus.events)
    session.process_frame(_frame_with_patch(GREY), 2, 133)
    assert [event.event_type for event in bus.events] == ["new_session"]


def test_same_timestamp_appearance_and_hit_do_not_claim_instant_reaction():
    session, bus, _ = _timed_session()
    session.process_frame(_frame_with_patch(GREY), 1, 100)
    session.process_frame(_frame_with_patch(WHITE), 2, 100)
    session.process_frame(_frame_with_patch(WHITE), 3, 750)
    assert bus.events[-1].event_type == "arrow_hit"
    assert "response_time_ms" not in bus.events[-1].payload


@pytest.mark.parametrize("reason", ["miss", "overlap", "seek", "reset", "recording", "stop"])
def test_unmatched_or_interrupted_appearances_do_not_invent_response_times(reason):
    session, bus, _ = _timed_session()
    session.process_frame(_frame_with_patch(GREY), 10, 1000)
    if reason == "miss":
        session.process_frame(_frame_with_patch(BLACK), 11, 1100)
        session.process_frame(_frame_with_patch(BLACK), 12, 1400)
        session.process_frame(_frame_with_patch(_solid((0, 0, 255))), 13, 1450)
        session.process_frame(_frame_with_patch(_solid((0, 0, 255))), 14, 1700)
    elif reason == "overlap":
        session.process_frame(_frame_with_patch(_solid((0, 0, 255))), 11, 1100)
        session.process_frame(_frame_with_patch(GREY), 12, 1200)
    elif reason == "seek":
        session.process_frame(_frame_with_patch(_solid((0, 0, 255))), 1, 100)
    elif reason == "reset":
        session.reset_tracking()
    elif reason == "recording":
        session.set_recording_enabled(False)
        session.set_recording_enabled(True)
    elif reason == "stop":
        session.stop()
        session.start()
    session.process_frame(_frame_with_patch(WHITE), 20, 2000)
    session.process_frame(_frame_with_patch(WHITE), 21, 2650)
    assert bus.events[-1].event_type == "arrow_hit"
    assert "response_time_ms" not in bus.events[-1].payload


def test_activity_without_judgment_never_invents_a_hit():
    bus = EventBus()
    session = AnalysisSession(bus, OcrEngine())
    session.set_regions([_arrow_region_with_refs()])
    session.start()
    result = session.process_frame(_frame_with_patch(WHITE), 0, 100)
    assert [event.event_type for event in bus.events] == ["new_session"]
    assert "judgment box" in result.region_results[0].error
    assert result.region_results[0].arrow_state == "active"


def test_new_outcomes_keep_response_metrics_unavailable_even_with_legacy_appearance_refs():
    from app.live_scoreboard import LiveScoreboard

    session, bus, _ = _timed_session()
    board = LiveScoreboard(Mock)
    bus.set_live_sink(board.record)
    board.start(bus.session_id)
    try:
        for index, (patch, timestamp) in enumerate([
            (GREY, 100), (WHITE, 300), (WHITE, 600),
            (GREY, 700), (GREY, 950), (WHITE, 1001), (WHITE, 1301),
            (_solid((0, 0, 255)), 1400), (_solid((0, 0, 255)), 1650),
            (BLACK, 1700), (BLACK, 2350),
        ]):
            session.process_frame(_frame_with_patch(patch), index, timestamp)
        player = board.snapshot()["players"]["P1"]
        assert player["hits"] == 2
        assert player["misses"] == 1
        assert player["responseSamples"] == 0
        assert player["averageResponseTimeMs"] is None
    finally:
        board.stop()


# ---------------------------------------------------------------------
# ArrowStateTracker: edge-triggered hit/miss detection
# ---------------------------------------------------------------------

def test_idle_never_emits_an_event():
    tracker = ArrowStateTracker(min_confirm_frames=1)
    assert tracker.observe("idle") is None
    assert tracker.observe("idle") is None


def test_hit_emits_exactly_once_while_it_persists():
    tracker = ArrowStateTracker(min_confirm_frames=1)
    assert tracker.observe("hit") == "hit"
    assert tracker.observe("hit") is None  # same pulse, no re-fire
    assert tracker.observe("hit") is None


def test_returning_to_idle_then_hitting_again_fires_a_new_event():
    tracker = ArrowStateTracker(min_confirm_frames=1)
    assert tracker.observe("hit") == "hit"
    assert tracker.observe("idle") is None
    assert tracker.observe("hit") == "hit"  # new pulse


def test_direct_transition_from_hit_to_miss_fires_a_new_event():
    tracker = ArrowStateTracker(min_confirm_frames=1)
    assert tracker.observe("hit") == "hit"
    assert tracker.observe("miss") == "miss"


def test_unreadable_frame_does_not_reset_or_fire():
    tracker = ArrowStateTracker(min_confirm_frames=1)
    tracker.observe("hit")
    assert tracker.observe(None) is None
    assert tracker.status().active_state == "hit"  # untouched by the unreadable frame


def test_min_confirm_frames_rejects_a_single_frame_blip():
    tracker = ArrowStateTracker(min_confirm_frames=2)
    assert tracker.observe("hit") is None  # 1/2, not yet confirmed
    assert tracker.observe("idle") is None  # blip over before confirmation
    assert tracker.observe("hit") is None  # 1/2 again
    assert tracker.observe("hit") == "hit"  # 2/2 -> confirmed


def test_reset_clears_active_and_pending_state():
    tracker = ArrowStateTracker(min_confirm_frames=1)
    tracker.observe("hit")
    tracker.reset()
    status = tracker.status()
    assert status.active_state is None
    assert status.pending_state is None
    # after reset, the same state can immediately fire again
    assert tracker.observe("hit") == "hit"


# ---------------------------------------------------------------------
# AnalysisSession integration: a real arrow region end to end
# ---------------------------------------------------------------------

def _arrow_region_with_refs(direction="Down-Left", player="P1"):
    region = Region(
        label=f"{player} {direction}", player=player, kind="arrow",
        x=0, y=0, w=20, h=20, direction=direction,
    )
    region.reference_images["active"] = [
        image_utils.encode_crop_to_base64_png(patch) for patch in (WHITE, BLACK)
    ]
    return region


def _judgment_region(player="P1"):
    return Region(
        "Judgment", player, "judgment", 20, 0, 20, 20,
        reference_images={
            "perfect": [image_utils.encode_crop_to_base64_png(WHITE)],
            "miss": [image_utils.encode_crop_to_base64_png(BLACK)],
        },
    )


def _frame_with_patch(patch, frame_w=40, frame_h=40):
    frame = np.full((frame_h, frame_w, 3), 100, dtype=np.uint8)
    h, w = patch.shape[:2]
    frame[0:h, 0:w] = patch
    frame[0:h, 20:20+w] = patch
    return frame


@pytest.mark.parametrize("direction", config.ARROW_DIRECTIONS)
@pytest.mark.parametrize("player", ["P1", "P2"])
def test_arrow_region_emits_hit_event_through_full_pipeline(direction, player):
    bus = EventBus()
    session = AnalysisSession(bus, OcrEngine())
    region = _arrow_region_with_refs(direction, player)
    session.set_regions([region, _judgment_region(player)])
    session.set_arrow_min_confirm_frames(1)
    session.start()

    result = session.process_frame(_frame_with_patch(WHITE), 0, 0)
    session.process_frame(_frame_with_patch(WHITE), 1, 650)
    assert [event.event_type for event in bus.events] == ["new_session", "arrow_hit"]
    assert bus.events[1].payload["direction"] == direction
    assert bus.events[1].player == player
    assert result.region_results[0].arrow_state == "active"


@pytest.mark.parametrize("direction", config.ARROW_DIRECTIONS)
def test_arrow_region_emits_miss_event_and_does_not_repeat(direction):
    bus = EventBus()
    session = AnalysisSession(bus, OcrEngine())
    region = _arrow_region_with_refs(direction)
    session.set_regions([region, _judgment_region()])
    session.start()

    session.process_frame(_frame_with_patch(BLACK), 0, 0)
    session.process_frame(_frame_with_patch(BLACK), 1, 33)
    session.process_frame(_frame_with_patch(BLACK), 2, 66)
    session.process_frame(_frame_with_patch(BLACK), 3, 650)

    miss_events = [e for e in bus.events if e.event_type == "arrow_miss"]
    assert len(miss_events) == 1  # not one per frame
    assert miss_events[0].payload["direction"] == direction


def test_arrow_region_with_no_captured_references_never_emits_events():
    bus = EventBus()
    session = AnalysisSession(bus, OcrEngine())
    region = Region(
        label="P1 Up-Right", player="P1", kind="arrow", x=0, y=0, w=20, h=20, direction="Up-Right",
    )
    session.set_regions([region])
    session.start()

    result = session.process_frame(_frame_with_patch(WHITE), 0, 0)
    assert [event.event_type for event in bus.events] == ["new_session"]
    assert result.region_results[0].error is not None  # explains why, doesn't fail silently


def test_legacy_arrow_images_require_fresh_calibration_and_cannot_assign_a_judgment():
    bus = EventBus()
    session = AnalysisSession(bus, OcrEngine())
    region = _arrow_region_with_refs()
    region.reference_images = {
        "hit": [image_utils.encode_crop_to_base64_png(WHITE)],
        "miss": [image_utils.encode_crop_to_base64_png(BLACK)],
        "appeared": [image_utils.encode_crop_to_base64_png(GREY)],
    }
    original = region.to_dict()
    session.set_regions([region, _judgment_region()])
    session.start()
    result = session.process_frame(_frame_with_patch(WHITE), 1, 100)
    session.process_frame(_frame_with_patch(WHITE), 2, 750)
    assert "fresh Active Arrow" in result.region_results[0].error
    assert [event.event_type for event in bus.events] == ["new_session", "judgment_hit"]
    assert bus.events[-1].payload["direction"] is None
    assert region.to_dict() == original
    region.reference_images["active"] = [image_utils.encode_crop_to_base64_png(WHITE)]
    session.set_regions([region, _judgment_region()])
    session.process_frame(_frame_with_patch(WHITE), 3, 850)
    session.process_frame(_frame_with_patch(WHITE), 4, 1500)
    assert bus.events[-1].event_type == "arrow_hit"
    assert bus.events[-1].payload["direction"] == "Down-Left"


def test_all_arrow_controls_support_independent_active_and_empty_references():
    import tkinter as tk
    from tkinter import ttk
    from app.ui.video_tab import VideoTab

    root = tk.Tk()
    root.withdraw()
    controller = Mock()
    tab = VideoTab(root, controller)
    regions = [
        _arrow_region_with_refs(direction, player)
        for player in ("P1", "P2") for direction in config.ARROW_DIRECTIONS
    ]
    try:
        tab.refresh_region_list(regions)
        for region in regions:
            data = tab._region_rows[region.key()]
            card = data["thumb_label"].master
            buttons = {
                widget["text"]: widget for group in card.winfo_children() if isinstance(group, ttk.Frame)
                for widget in group.winfo_children() if isinstance(widget, ttk.Button)
            }
            assert set(buttons) == {
                "Add Active Arrow ref", "Clear Active Arrow refs", "View / delete refs...",
                "Add Empty ref", "Clear Empty refs", "View / delete Empty refs...",
            }
            assert "active_ref_label" in data
            assert "empty_ref_label" in data
            assert "hit_ref_label" not in data and "miss_ref_label" not in data
            assert "appeared_ref_label" not in data
            for title, state, action in (
                ("Add Active Arrow ref", "active", controller.capture_arrow_reference),
                ("Clear Active Arrow refs", "active", controller.clear_arrow_reference),
                ("Add Empty ref", "empty", controller.capture_arrow_reference),
                ("Clear Empty refs", "empty", controller.clear_arrow_reference),
            ):
                action.reset_mock()
                buttons[title].invoke()
                assert not buttons[title].instate(["disabled"])
                action.assert_called_once_with(region, state)
            controller.show_arrow_reference_gallery.reset_mock()
            buttons["View / delete refs..."].invoke()
            controller.show_arrow_reference_gallery.assert_called_once_with(region, "active")
            controller.show_arrow_reference_gallery.reset_mock()
            buttons["View / delete Empty refs..."].invoke()
            controller.show_arrow_reference_gallery.assert_called_once_with(region, "empty")
    finally:
        root.destroy()


@pytest.mark.parametrize("direction", config.ARROW_DIRECTIONS)
@pytest.mark.parametrize("player", ["P1", "P2"])
def test_empty_reference_suppresses_false_lane_hits_but_allows_real_activity(direction, player):
    bus = EventBus()
    session = AnalysisSession(bus, OcrEngine())
    region = _arrow_region_with_refs(direction, player)
    region.reference_images = {
        "active": [image_utils.encode_crop_to_base64_png(WHITE)],
        "empty": [image_utils.encode_crop_to_base64_png(GREY)],
    }
    session.set_regions([region, _judgment_region(player)])
    session.set_arrow_similarity_threshold(.45)
    session.start()
    empty_frame = _frame_with_patch(GREY)
    empty_frame[:20, 20:40] = WHITE
    first = session.process_frame(empty_frame, 0, 100)
    session.process_frame(empty_frame, 1, 750)
    arrow_result = first.region_results[0]
    assert arrow_result.arrow_state == "idle"
    assert arrow_result.confidence >= .45
    assert "Empty reference" in arrow_result.validation_note
    assert not session._pending_lanes
    assert [event.event_type for event in bus.events] == ["new_session", "judgment_hit"]
    assert bus.events[-1].payload["direction"] is None
    session.new_session()
    session.process_frame(_frame_with_patch(WHITE), 2, 1000)
    session.process_frame(_frame_with_patch(WHITE), 3, 1650)
    hits = [event for event in bus.events if event.event_type == "arrow_hit"]
    assert len(hits) == 1
    assert hits[0].player == player and hits[0].payload["direction"] == direction


def test_active_and_empty_matching_survives_layout_save_load_and_rescale(tmp_path):
    from app import layout_store
    from app.models import LayoutProfile

    p1 = Region("P1 Center", "P1", "arrow", 0, 0, 20, 20, direction="Center",
                reference_images={
                    "active": [image_utils.encode_crop_to_base64_png(WHITE)],
                    "empty": [image_utils.encode_crop_to_base64_png(GREY)],
                })
    p2 = Region("P2 Center", "P2", "arrow", 20, 0, 20, 20, direction="Center",
                reference_images={
                    "active": [image_utils.encode_crop_to_base64_png(BLACK)],
                    "empty": [image_utils.encode_crop_to_base64_png(GREY)],
                })
    path = str(tmp_path / "active-empty.json")
    layout_store.save_layout_to_path(LayoutProfile("active-empty", 40, 20, [p1, p2]), path)
    loaded = layout_store.load_layout_from_path(path)
    scaled = layout_store.rescale_profile(loaded, 80, 40)
    session = AnalysisSession(EventBus())
    session.set_regions(scaled.regions)
    session.set_arrow_similarity_threshold(.45)
    session.start()
    empty_frame = np.full((40, 80, 3), 128, dtype=np.uint8)
    empty_result = session.process_arrow_frame(empty_frame, 0, 0)
    assert all(result.arrow_state == "idle" for result in empty_result.region_results)
    assert all("Empty reference" in result.validation_note for result in empty_result.region_results)
    active_frame = empty_frame.copy()
    active_frame[:, :40] = 255
    active_frame[:, 40:] = 0
    active_result = session.process_arrow_frame(active_frame, 1, 17)
    assert all(result.arrow_state == "active" for result in active_result.region_results)
    for region in scaled.regions:
        assert set(region.reference_images) == {"active", "empty"}
        assert session._arrow_references[region.key()]["empty"][0].shape[:2] == (40, 40)


def test_new_regions_resets_arrow_tracking():
    bus = EventBus()
    session = AnalysisSession(bus, OcrEngine())
    region = _arrow_region_with_refs()
    session.set_regions([region])
    session.start()
    session.process_frame(_frame_with_patch(WHITE), 0, 0)
    assert session.tracker_for(region).status().active_state == "active"

    session.set_regions([region])
    assert session.tracker_for(region).status().active_state is None


def test_all_ten_arrow_regions_track_independently_with_one_game_guid():
    bus = EventBus()
    session = AnalysisSession(bus, OcrEngine())
    regions = [
        _arrow_region_with_refs(direction, player)
        for player in ("P1", "P2") for direction in config.ARROW_DIRECTIONS
    ]
    session.set_regions(regions + [_judgment_region("P1"), _judgment_region("P2")])
    session.start()
    for index, (patch, timestamp) in enumerate((
        (WHITE, 0), (WHITE, 650), (GREY, 750), (GREY, 1000), (BLACK, 1050), (BLACK, 1700),
    )):
        session.process_frame(_frame_with_patch(patch), index, timestamp)
    gameplay = [event for event in bus.events if event.event_type != "new_session"]
    expected = {(region.player, region.direction) for region in regions}
    for event_type in ("arrow_hit", "arrow_miss"):
        matching = [event for event in gameplay if event.event_type == event_type]
        assert len(matching) == 10
        assert {(event.player, event.payload["direction"]) for event in matching} == expected
    assert {event.session_id for event in bus.events} == {bus.session_id}
