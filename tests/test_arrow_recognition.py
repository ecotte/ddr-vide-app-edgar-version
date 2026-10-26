"""Tests for arrow-lane hit/miss recognition: image similarity, the
edge-triggered ArrowStateTracker, and the full AnalysisSession pipeline for
"arrow" regions. No real game footage or OCR model is needed - references
are small synthetic solid-color crops so behavior is fully deterministic.
"""
from __future__ import annotations

import os
import sys

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

def test_classify_matches_hit_reference():
    refs = {"hit": WHITE, "miss": BLACK}
    state, score = classify_arrow_state(WHITE, refs, threshold=0.9)
    assert state == "hit"
    assert score > 0.9


def test_classify_matches_miss_reference():
    refs = {"hit": WHITE, "miss": BLACK}
    state, score = classify_arrow_state(BLACK, refs, threshold=0.9)
    assert state == "miss"


def test_classify_uses_best_of_multiple_references_for_each_state():
    refs = {"hit": [BLACK, WHITE], "miss": [GREY]}
    state, score = classify_arrow_state(WHITE, refs, threshold=0.9)
    assert state == "hit"
    assert score > 0.9


def test_classify_below_threshold_is_idle_not_a_guess():
    refs = {"hit": WHITE, "miss": BLACK}
    state, score = classify_arrow_state(GREY, refs, threshold=0.9)
    assert state == "idle"


def test_classify_with_no_references_is_always_idle():
    state, score = classify_arrow_state(WHITE, {}, threshold=0.9)
    assert state == "idle"
    assert score == 0.0


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
    region.reference_images["hit"] = [image_utils.encode_crop_to_base64_png(WHITE)]
    region.reference_images["miss"] = [image_utils.encode_crop_to_base64_png(BLACK)]
    return region


def _frame_with_patch(patch, frame_w=40, frame_h=40):
    frame = np.full((frame_h, frame_w, 3), 100, dtype=np.uint8)
    h, w = patch.shape[:2]
    frame[0:h, 0:w] = patch
    return frame


@pytest.mark.parametrize("direction", config.ARROW_DIRECTIONS)
@pytest.mark.parametrize("player", ["P1", "P2"])
def test_arrow_region_emits_hit_event_through_full_pipeline(direction, player):
    bus = EventBus()
    session = AnalysisSession(bus, OcrEngine())
    region = _arrow_region_with_refs(direction, player)
    session.set_regions([region])
    session.set_arrow_min_confirm_frames(1)
    session.start()

    result = session.process_frame(_frame_with_patch(WHITE), 0, 0)
    assert [event.event_type for event in bus.events] == ["new_session", "arrow_hit"]
    assert bus.events[1].payload["direction"] == direction
    assert bus.events[1].player == player
    assert result.region_results[0].arrow_state == "hit"


@pytest.mark.parametrize("direction", config.ARROW_DIRECTIONS)
def test_arrow_region_emits_miss_event_and_does_not_repeat(direction):
    bus = EventBus()
    session = AnalysisSession(bus, OcrEngine())
    region = _arrow_region_with_refs(direction)
    session.set_regions([region])
    session.start()

    session.process_frame(_frame_with_patch(BLACK), 0, 0)
    session.process_frame(_frame_with_patch(BLACK), 1, 33)
    session.process_frame(_frame_with_patch(BLACK), 2, 66)

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


def test_new_regions_resets_arrow_tracking():
    bus = EventBus()
    session = AnalysisSession(bus, OcrEngine())
    region = _arrow_region_with_refs()
    session.set_regions([region])
    session.start()
    session.process_frame(_frame_with_patch(WHITE), 0, 0)
    assert session.tracker_for(region).status().active_state == "hit"

    session.set_regions([region])
    assert session.tracker_for(region).status().active_state is None


def test_all_ten_arrow_regions_track_independently_with_one_game_guid():
    bus = EventBus()
    session = AnalysisSession(bus, OcrEngine())
    regions = [
        _arrow_region_with_refs(direction, player)
        for player in ("P1", "P2") for direction in config.ARROW_DIRECTIONS
    ]
    session.set_regions(regions)
    session.start()
    for index, patch in enumerate((WHITE, WHITE, BLACK, BLACK)):
        session.process_frame(_frame_with_patch(patch), index, index * 33)
    gameplay = [event for event in bus.events if event.event_type != "new_session"]
    expected = {(region.player, region.direction) for region in regions}
    for event_type in ("arrow_hit", "arrow_miss"):
        matching = [event for event in gameplay if event.event_type == event_type]
        assert len(matching) == 10
        assert {(event.player, event.payload["direction"]) for event in matching} == expected
    assert {event.session_id for event in bus.events} == {bus.session_id}
