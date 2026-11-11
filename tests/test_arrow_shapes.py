from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from app import config, image_utils, layout_store
from app.analysis import AnalysisSession
from app.arrow_shapes import ArrowShapeMatcher
from app.event_bus import EventBus
from app.live_scoreboard import LiveScoreboard
from app.models import LayoutProfile, Region, arrow_search_bounds
from app.ui.app_window import App


def arrow_image(direction="Down-Right", color=(255, 120, 0)):
    image = np.full((80, 80, 3), 30, dtype=np.uint8)
    if direction == "Center":
        cv2.ellipse(image, (28, 38), (8, 20), 0, 0, 360, color, -1)
        cv2.ellipse(image, (51, 38), (8, 20), 0, 0, 360, color, -1)
    else:
        start, end = {
            "Down-Right": ((18, 18), (62, 62)),
            "Down-Left": ((62, 18), (18, 62)),
            "Up-Right": ((18, 62), (62, 18)),
            "Up-Left": ((62, 62), (18, 18)),
        }[direction]
        cv2.arrowedLine(image, start, end, color, 9, cv2.LINE_8, tipLength=.5)
    return image


def encode(image):
    return image_utils.encode_crop_to_base64_png(image)


@pytest.mark.parametrize("direction", config.ARROW_DIRECTIONS)
def test_shape_search_finds_colored_arrow_below_receptor_and_rejects_gray(direction):
    ref = arrow_image(direction)
    matcher = ArrowShapeMatcher({"active": [ref]})
    assert matcher.error is None
    assert len(matcher.references[0].templates) == 3
    search = np.full((120, 80, 3), 90, dtype=np.uint8)
    search[40:120] = ref
    match = matcher.match(search, .55)
    assert match.state == "active"
    assert match.match_bounds is not None
    assert match.match_bounds[1] > 40
    gray = cv2.cvtColor(cv2.cvtColor(search, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    assert matcher.match(gray, .55).state == "idle"
    assert matcher.match(np.full_like(search, (255, 120, 0)), .55).state == "idle"


def test_empty_examples_veto_in_same_shape_score_space():
    ref = arrow_image()
    matcher = ArrowShapeMatcher({"active": [ref], "empty": [ref]})
    result = matcher.match(ref, .55)
    assert result.similarity >= .55
    assert result.empty_similarity is not None
    assert result.suppressed_by_empty
    assert result.state == "idle"
    flat = ArrowShapeMatcher({"active": [ref], "empty": [np.zeros_like(ref)]})
    assert "no contrast" in " ".join(flat.warnings)
    assert flat.match(ref, .55).state == "active"


def test_invalid_active_samples_report_actionable_errors_without_grayscale_fallback(monkeypatch):
    gray = np.full((80, 80, 3), 128, dtype=np.uint8)
    matcher = ArrowShapeMatcher({"active": [gray], "empty": [arrow_image()]})
    assert matcher.error and "clear colored active note" in matcher.error
    assert "white receptor flash" in matcher.warnings[0]
    matcher = ArrowShapeMatcher({"active": [gray, arrow_image()]})
    assert matcher.error is None and matcher.warnings
    monkeypatch.setattr(image_utils, "similarity", Mock(side_effect=AssertionError("No grayscale fallback")))
    assert matcher.match(arrow_image(), .55).state == "active"


@pytest.mark.parametrize("player", ["P1", "P2"])
@pytest.mark.parametrize("direction", config.ARROW_DIRECTIONS)
def test_activity_is_immediate_and_judgment_still_owns_outcome(player, direction):
    ref = arrow_image(direction)
    arrow = Region(direction, player, "arrow", 0, 0, 80, 80,
                   direction=direction, reference_images={"active": [encode(ref)]})
    text = np.full((53, 286, 3), 30, dtype=np.uint8)
    cv2.putText(text, "MISS", (50, 38), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 3)
    judgment = Region("Word", player, "judgment", 100, 30, 286, 53,
                      reference_images={"miss": [encode(text)]})
    bus = EventBus()
    session = AnalysisSession(bus)
    session.set_regions([arrow, judgment])
    assert session.arrow_similarity_threshold == .55
    session.start()
    board = LiveScoreboard(lambda: Mock())
    board.start(bus.session_id)
    bus.set_live_sink(board.record)
    try:
        frame = np.full((130, 430, 3), 30, dtype=np.uint8)
        frame[40:120, :80] = ref
        initial = session.process_arrow_frame(frame, 0, 100)
        activity, = [e for e in initial.new_events if e.event_type == "arrow"]
        assert activity.video_time_ms == 100
        assert activity.payload["direction"] == direction
        assert activity.payload["state"] == "active" and "outcome" not in activity.payload
        assert "Arrow shape" in initial.region_results[0].arrow_diagnostics
        assert "Search: (0, 0) 80x120 pixels." in initial.region_results[0].arrow_diagnostics
        assert board.snapshot()["players"][player]["hits"] == 0
        frame[30:83, 100:386] = text
        session.process_arrow_frame(frame, 1, 200)
        session.process_arrow_frame(frame, 2, 233)
        session.process_arrow_frame(frame, 3, 850)
        words = [e for e in bus.events if e.event_type == "judgement"]
        assert len(words) == 1 and words[0].payload["step_directions"] == [direction]
        assert words[0].payload["outcome"] == "miss"
        assert len([e for e in bus.events if e.event_type == "arrow"]) == 1
        stats = board.snapshot()["players"][player]
        assert stats["misses"] == stats["missesByDirection"][direction] == 1
        assert stats["hits"] == stats["runningScore"] == 0
    finally:
        bus.set_live_sink(None)
        board.stop()


def test_cache_refreshes_on_capture_clear_delete_load_and_rescale(tmp_path, monkeypatch):
    region = Region("Center", "P1", "arrow", 0, 0, 80, 80, direction="Center",
                    reference_images={"active": [encode(arrow_image("Center"))]})
    other = Region("Center", "P2", "arrow", 80, 0, 80, 80, direction="Center",
                   reference_images={"active": [encode(arrow_image("Center", (0, 255, 255)))]})
    session = AnalysisSession(EventBus())
    session.set_regions([region, other])
    cached = session._arrow_shapes[region.key()]
    prepare = Mock(side_effect=AssertionError("Frame processing must reuse templates"))
    with monkeypatch.context() as context:
        context.setattr("app.arrow_shapes.prepare_shape_reference", prepare)
        session.start()
        frame = np.full((120, 160, 3), 30, dtype=np.uint8)
        frame[:80, :80] = arrow_image("Center")
        for i in range(3):
            session.process_arrow_frame(frame, i, i * 33)
        prepare.assert_not_called()
    app = SimpleNamespace(
        regions=[region, other], analysis=session, _analysis_generation=0,
        _last_frame_bgr=frame, video_tab=Mock(), _refresh_arrow_reference_thumbnails=Mock(),
    )
    App.capture_arrow_reference(app, region, "active")
    assert session._arrow_shapes[region.key()] is not cached
    assert len(session._arrow_shapes[region.key()].references) == 2
    assert len(session._arrow_shapes[other.key()].references) == 1
    App.delete_arrow_reference(app, region, "active", 0)
    assert len(session._arrow_shapes[region.key()].references) == 1
    path = str(tmp_path / "arrows.json")
    layout_store.save_layout_to_path(LayoutProfile("arrows", 160, 120, app.regions), path)
    scaled = layout_store.rescale_profile(layout_store.load_layout_from_path(path), 320, 240)
    session.set_regions(scaled.regions)
    for r in scaled.regions:
        assert session._arrow_shapes[r.key()].match(session._arrow_references[r.key()]["active"][0], .55).state == "active"
    app.regions = scaled.regions
    App.clear_arrow_reference(app, scaled.regions[0], "active")
    assert session._arrow_shapes[scaled.regions[0].key()].error
    assert session._arrow_shapes[scaled.regions[1].key()].error is None


def test_arrow_search_bounds_do_not_expand_across_lanes_and_clamp_to_source():
    region = Region("Center", "P1", "arrow", 10, 50, 80, 80, direction="Center")
    assert arrow_search_bounds(region, 100, 150) == (10, 50, 90, 150)
    assert arrow_search_bounds(region, 100, 200) == (10, 50, 90, 170)
    region.x, region.y = -10, -5
    assert arrow_search_bounds(region, 100, 200) == (0, 0, 70, 115)
    region.x = 200
    session = AnalysisSession(EventBus())
    region.reference_images = {"active": [encode(arrow_image("Center"))]}
    session.set_regions([region])
    session.start()
    result = session.process_arrow_frame(np.zeros((200, 100, 3), np.uint8), 0, 0)
    assert result.region_results[0].is_unreadable
    assert "out of frame" in result.region_results[0].error
    assert not any(e.event_type == "arrow" for e in session.event_bus.events)


def test_missing_and_invalid_shape_calibration_is_visible():
    region = Region("Center", "P1", "arrow", 0, 0, 80, 80, direction="Center",
                    reference_images={"active": ["invalid", encode(np.zeros((80, 80, 3), np.uint8))]})
    session = AnalysisSession(EventBus())
    session.set_regions([region])
    session.start()
    result = session.process_arrow_frame(np.zeros((120, 80, 3), np.uint8), 0, 0).region_results[0]
    assert result.error and "No usable arrow-shape" in result.error
    assert "could not be decoded" in result.arrow_diagnostics
    assert "colored note" in result.arrow_diagnostics


def test_shape_activity_respects_confirmation_recording_and_rearming():
    region = Region("Center", "P1", "arrow", 0, 0, 80, 80, direction="Center",
                    reference_images={"active": [encode(arrow_image("Center"))]})
    session = AnalysisSession(EventBus())
    session.set_regions([region])
    session.set_arrow_min_confirm_frames(2)
    session.start()
    active = np.full((120, 80, 3), 30, dtype=np.uint8)
    active[40:] = arrow_image("Center")
    idle = np.full_like(active, 30)
    session.process_arrow_frame(active, 0, 0)
    session.process_arrow_frame(active, 0, 0)
    assert len(session.event_bus.events) == 1
    session.process_arrow_frame(active, 1, 33)
    assert session.event_bus.events[-1].event_type == "arrow"
    for i in range(2, 5):
        session.process_arrow_frame(active, i, i * 33)
    assert len(session.event_bus.events) == 2
    session.process_arrow_frame(idle, 5, 165)
    session.process_arrow_frame(idle, 6, 198)
    session.set_recording_enabled(False)
    session.process_arrow_frame(active, 7, 231)
    session.process_arrow_frame(active, 8, 264)
    session.set_recording_enabled(True)
    session.process_arrow_frame(active, 9, 297)
    assert len(session.event_bus.events) == 2
    session.process_arrow_frame(idle, 10, 330)
    session.process_arrow_frame(idle, 11, 363)
    session.process_arrow_frame(active, 12, 396)
    session.process_arrow_frame(active, 13, 429)
    assert len([e for e in session.event_bus.events if e.event_type == "arrow"]) == 2
