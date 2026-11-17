from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from app import config, image_utils, layout_store
from app.analysis import AnalysisSession
from app.event_bus import EventBus
from app.judgment_shapes import JudgmentShapeMatcher, prepare_shape_reference
from app.models import LayoutProfile, Region, judgment_search_bounds
from app.ui.app_window import App


def word(text="MISS", color=(0, 0, 255)):
    image = np.full((53, 286, 3), 30, dtype=np.uint8)
    cv2.putText(image, text, (50, 38), cv2.FONT_HERSHEY_SIMPLEX, 1, color, 3, cv2.LINE_8)
    return image


def encode(image):
    return image_utils.encode_crop_to_base64_png(image)


def judgment(player="P1", x=40, reference=None):
    return Region(
        player, player, "judgment", x, 20, 286, 53,
        reference_images={"miss": [encode(word() if reference is None else reference)]},
    )


def make_frame(region, image=None, width=800):
    frame = np.full((130, width, 3), 30, dtype=np.uint8)
    if image is not None:
        frame[region.y:region.y + region.h, region.x:region.x + region.w] = image
    return frame


def test_default_engine_learns_hue_and_matches_shifted_scaled_letters_not_background():
    reference = word()
    matcher = JudgmentShapeMatcher({"miss": [reference]})
    assert matcher.error is None
    assert len(matcher.references["miss"][0].templates) == 18
    search = np.full((77, 366, 3), 80, dtype=np.uint8)
    enlarged = cv2.resize(reference, (315, 66), interpolation=cv2.INTER_NEAREST)
    mask = enlarged[:, :, 2] > 100
    patch = search[5:71, 35:350]
    patch[mask] = enlarged[mask]
    result = matcher.match(search, .65)
    assert result.state == "miss"
    assert result.match_bounds is not None
    assert matcher.match(np.full_like(search, 80), .65).state == "idle"
    assert matcher.match(np.full_like(search, (0, 0, 255)), .65).state == "idle"


def test_category_is_learned_from_reference_not_hardcoded_color():
    cyan_miss = word(color=(255, 255, 0))
    matcher = JudgmentShapeMatcher({"miss": [cyan_miss]})
    assert matcher.match(cyan_miss, .65).state == "miss"
    assert matcher.match(word(), .65).state == "idle"


def test_shape_engine_never_uses_whole_crop_grayscale_similarity(monkeypatch):
    matcher = JudgmentShapeMatcher({"miss": [word()], "empty": [word("GOOD", (0, 255, 255))]})
    grayscale = Mock(side_effect=AssertionError("Shape mode must not fall back to grayscale"))
    monkeypatch.setattr(image_utils, "similarity", grayscale)
    assert matcher.match(word(), .65).state == "miss"
    grayscale.assert_not_called()


def test_search_can_find_word_outside_capture_box_without_moving_it():
    region = judgment(x=100)
    session = AnalysisSession(EventBus())
    session.set_regions([region])
    session.start()
    # The reference word starts at x=50 within its crop. Move it left so some
    # lettering is outside the capture box but inside the visible search bounds.
    frame = make_frame(region)
    reference = word()
    pixels = reference[10:45, 48:125]
    frame[30:65, 75:152] = pixels
    result = session.process_arrow_frame(frame, 0, 0).region_results[0]
    assert result.arrow_state == "miss"
    assert region.x == 100
    assert result.crop.shape[:2] == (53, 286)
    assert "Search: (60, 8) 366x77 pixels." in result.judgment_diagnostics


def test_identical_category_shapes_are_ambiguous():
    matcher = JudgmentShapeMatcher({"perfect": [word()], "great": [word()]})
    result = matcher.match(word(), .65)
    assert result.state is None
    assert result.similarity >= .65
    assert result.runner_up_similarity == pytest.approx(result.similarity)


def test_negative_masks_veto_same_colored_shape_without_deleting_outcomes():
    matcher = JudgmentShapeMatcher({"miss": [word()], "empty": [word()]})
    result = matcher.match(word(), .65)
    assert result.state == "idle"
    assert result.suppressed_by_empty
    assert result.empty_similarity is not None
    no_color = JudgmentShapeMatcher({"miss": [word()], "empty": [np.zeros((53, 286, 3), np.uint8)]})
    assert "no contrast" in " ".join(no_color.warnings)
    assert no_color.match(word(), .65).state == "miss"


def test_invalid_references_are_explicit_and_partial_valid_calibration_still_works():
    invalid = np.full((53, 286, 3), 128, np.uint8)
    matcher = JudgmentShapeMatcher({"miss": [invalid]})
    assert matcher.error and "No saturated lettering" in matcher.warnings[0]
    partial = JudgmentShapeMatcher({"miss": [invalid, word()]})
    assert partial.error is None
    assert partial.warnings
    assert partial.match(word(), .65).state == "miss"
    assert JudgmentShapeMatcher({"empty": [word()]}).error


def test_shape_engine_caches_reference_work_and_reports_bounds(monkeypatch):
    region = judgment()
    session = AnalysisSession(EventBus())
    session.set_regions([region])
    assert session.judgment_similarity_threshold == .65
    assert session.judgment_min_confirm_frames == 2
    assert session.arrow_similarity_threshold == .55
    assert session.arrow_min_confirm_frames == 1
    cached = session._judgment_shapes[region.key()]
    prepare = Mock(side_effect=AssertionError("Per-frame reference compilation"))
    monkeypatch.setattr("app.judgment_shapes.prepare_shape_reference", prepare)
    session.start()
    for index in range(3):
        result = session.process_arrow_frame(make_frame(region, word()), index, index * 33)
    assert session._judgment_shapes[region.key()] is cached
    prepare.assert_not_called()
    details = result.region_results[0].judgment_diagnostics
    assert "Letter shape" in details and "threshold 0.650" in details
    assert "Search: (0, 8) 366x77 pixels." in details
    assert "Best lettering:" in details
    assert "accuracy probability" in details


def test_default_engine_emits_only_judgement_after_confirmation_and_settling():
    bus = EventBus()
    region = judgment()
    session = AnalysisSession(bus)
    session.set_regions([region])
    session.start()
    for index, timestamp in enumerate((0, 33, 499)):
        session.process_arrow_frame(make_frame(region, word()), index, timestamp)
        assert not any(e.event_type == "judgement" for e in bus.events)
    session.process_arrow_frame(make_frame(region, word()), 3, 500)
    event, = [e for e in bus.events if e.event_type == "judgement"]
    assert event.payload["judgment"] == "miss"
    assert event.payload["outcome"] == "miss"
    assert event.payload["judgment_video_time_ms"] == 0
    assert event.payload["step_directions"] == []


def test_search_bounds_clamp_and_scale_without_changing_capture_geometry():
    region = judgment(x=10)
    before = region.to_dict()
    assert judgment_search_bounds(region, 320, 80) == (0, 8, 320, 80)
    assert region.to_dict() == before
    region = Region("Word", "P1", "judgment", 100, 100, 572, 106)
    assert judgment_search_bounds(region, 1000, 500) == (20, 76, 752, 230)


def test_out_of_bounds_capture_does_not_match_nearby_search_content():
    region = judgment(x=810)
    session = AnalysisSession(EventBus())
    session.set_regions([region])
    session.start()
    result = session.process_arrow_frame(make_frame(judgment(), word()), 0, 0).region_results[0]
    assert result.error == "Empty judgment crop (region out of frame bounds?)"
    assert not session._pending_judgments


def test_capture_delete_reload_and_rescale_refresh_cached_templates(tmp_path):
    p1, p2 = judgment(), judgment("P2", 400, word("GOOD", (0, 255, 255)))
    session = AnalysisSession(EventBus())
    session.set_regions([p1, p2])
    initial = session._judgment_shapes[p1.key()]
    controller = SimpleNamespace(
        regions=[p1, p2], analysis=session, _analysis_generation=0,
        _last_frame_bgr=make_frame(p1, word("GREAT", (0, 255, 0))),
        video_tab=Mock(), _refresh_arrow_reference_thumbnails=Mock(),
    )
    App.capture_arrow_reference(controller, p1, "great")
    assert session._judgment_shapes[p1.key()] is not initial
    assert "great" in session._judgment_shapes[p1.key()].references
    assert "great" not in session._judgment_shapes[p2.key()].references
    App.delete_arrow_reference(controller, p1, "great", 0)
    assert "great" not in session._judgment_shapes[p1.key()].references
    path = str(tmp_path / "shapes.json")
    layout_store.save_layout_to_path(LayoutProfile("shapes", 800, 130, [p1, p2]), path)
    profile = layout_store.load_layout_from_path(path)
    scaled = layout_store.rescale_profile(profile, 1600, 260)
    session.set_regions(scaled.regions)
    for region in scaled.regions:
        references = session._arrow_references[region.key()]
        assert session._judgment_shapes[region.key()].match(references["miss"][0], .65).state == "miss"
    assert profile.regions[0].to_dict() == p1.to_dict()


def test_invalid_loaded_reference_is_reported_in_diagnostics():
    region = judgment()
    region.reference_images["miss"].append("invalid image")
    session = AnalysisSession(EventBus())
    session.set_regions([region])
    session.start()
    result = session.process_arrow_frame(make_frame(region, word()), 0, 0).region_results[0]
    assert "could not be decoded" in result.validation_note
    region.reference_images = {"miss": [encode(np.full((53, 286, 3), 128, np.uint8))]}
    session.set_regions([region])
    result = session.process_arrow_frame(make_frame(region), 1, 33).region_results[0]
    assert "No usable letter-shape references" in result.error
    assert "No saturated lettering" in result.validation_note


def test_bad_only_calibration_is_a_graded_hit_and_survives_layout_reload(tmp_path):
    bad = word("BAD", (0, 255, 255))
    region = judgment()
    region.reference_images = {"bad": [encode(bad)]}
    path = str(tmp_path / "bad-layout.json")
    layout_store.save_layout_to_path(LayoutProfile("bad", 800, 130, [region]), path)
    loaded = layout_store.load_layout_from_path(path)
    assert loaded.regions[0].reference_images["bad"] == region.reference_images["bad"]
    bus = EventBus()
    session = AnalysisSession(bus)
    session.set_regions(loaded.regions)
    session.start()
    for index, timestamp in enumerate((0, 33, 500, 700)):
        session.process_arrow_frame(make_frame(region, bad), index, timestamp)
    event, = [row for row in bus.events if row.event_type == "judgement"]
    assert event.payload["judgment"] == "bad"
    assert event.payload["outcome"] == event.payload["state"] == "hit"
    assert event.payload["step_directions"] == []


def test_bad_reference_capture_clear_and_player_isolation():
    p1, p2 = judgment(), judgment("P2", 400)
    session = AnalysisSession(EventBus())
    session.set_regions([p1, p2])
    controller = SimpleNamespace(
        regions=[p1, p2], analysis=session, _analysis_generation=0,
        _last_frame_bgr=make_frame(p1, word("BAD", (0, 255, 255))),
        video_tab=Mock(), _refresh_arrow_reference_thumbnails=Mock(),
    )
    App.capture_arrow_reference(controller, p1, "bad")
    assert "bad" in session._judgment_shapes[p1.key()].references
    assert "bad" not in session._judgment_shapes[p2.key()].references
    assert p1.reference_images["bad"]
    App.clear_arrow_reference(controller, p1, "bad")
    assert "bad" not in session._judgment_shapes[p1.key()].references
    assert p2.reference_images.get("bad") is None
