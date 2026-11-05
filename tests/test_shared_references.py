from unittest.mock import Mock, patch
from types import SimpleNamespace

import numpy as np
import pytest

from app import config, image_utils, layout_store
from app.analysis import AnalysisSession
from app.event_bus import EventBus
from app.models import LayoutProfile, Region, share_p1_references
from app.ui.app_window import App


def encoded(value, width=20, height=20):
    return image_utils.encode_crop_to_base64_png(
        np.full((height, width, 3), value, dtype=np.uint8),
    )


@pytest.mark.parametrize("kind", ["arrow", "judgment", "score", "combo"])
def test_p1_replaces_p2_references_without_changing_boxes_or_settings(kind, tmp_path):
    p1 = Region("P1", "P1", kind, 0, 0, 20, 20, direction="Center",
                reference_images={"hit": [encoded(255)]},
                digit_templates={"1": [encoded(255, 24, 32)]},
                digit_templates_reviewed=True, score_template_version=2)
    p2 = Region("P2", "P2", kind, 100, 30, 40, 35, direction="Center",
                reference_images={"miss": [encoded(0)]}, combo_yellow_filter=True)
    regions = [p2, p1]
    session = AnalysisSession(EventBus(), shared_player_references=True)
    session.set_regions(regions)
    assert p2.reference_images == p1.reference_images
    assert p2.digit_templates == p1.digit_templates
    assert p2.digit_templates_reviewed and p2.score_template_version == 2
    assert (p2.x, p2.y, p2.w, p2.h) == (100, 30, 40, 35)
    assert p2.combo_yellow_filter
    assert session.tracker_for(p1) is not session.tracker_for(p2)
    assert p2.reference_images["hit"] is not p1.reference_images["hit"]
    path = str(tmp_path / "shared.json")
    layout_store.save_layout_to_path(LayoutProfile("shared", 200, 100, regions), path)
    loaded = layout_store.load_layout_from_path(path)
    assert loaded.regions[0].reference_images == loaded.regions[1].reference_images
    scaled = layout_store.rescale_profile(loaded, 400, 200)
    session.set_regions(scaled.regions)
    assert scaled.regions[0].reference_images == scaled.regions[1].reference_images
    p1.reference_images.clear()
    session.set_regions(regions)
    assert p2.reference_images == {}


def test_arrow_direction_matching_and_missing_p1_never_reuse_p2_calibration():
    p1 = Region("P1", "P1", "arrow", 0, 0, 20, 20, direction="Center",
                reference_images={"hit": [encoded(255)]})
    p2 = Region("P2", "P2", "arrow", 20, 0, 20, 20, direction="Up-Right",
                reference_images={"hit": [encoded(0)]})
    shared_end = Region("End", "Unknown", "game_end", 0, 20, 20, 20,
                        reference_images={"ended": [encoded(128)]})
    share_p1_references([p1, p2, shared_end])
    assert not p2.reference_images
    assert shared_end.reference_images["ended"] == [encoded(128)]
    p2.direction = "Center"
    share_p1_references([p1, p2])
    assert p2.reference_images == p1.reference_images
    share_p1_references([p2])
    assert not p2.reference_images


@pytest.mark.parametrize("kind", ["score", "combo"])
def test_teaching_p1_updates_both_numeric_caches_without_resetting_trackers(kind):
    p1 = Region("P1", "P1", kind, 0, 0, 20, 20)
    p2 = Region("P2", "P2", kind, 20, 0, 30, 30)
    session = AnalysisSession(EventBus(), shared_player_references=True)
    session.set_regions([p1, p2])
    trackers = [session.tracker_for(region) for region in (p1, p2)]
    p1.digit_templates = {"1": [encoded(255, 24, 32)]}
    p1.digit_templates_reviewed = True
    p1.score_template_version = config.DIGIT_TEMPLATE_VERSIONS[kind]
    session.update_score_templates(p1)
    for region, tracker in zip((p1, p2), trackers):
        assert session.uses_score_templates(region)
        assert session.tracker_for(region) is tracker
        assert "1" in session._score_templates[region.key()]
    p1.digit_templates = {}
    session.update_score_templates(p1)
    assert not session.uses_score_templates(p2)


def test_p2_recognizes_p1_reference_on_its_own_differently_sized_crop():
    p1 = Region("P1", "P1", "arrow", 0, 0, 20, 20, direction="Center",
                reference_images={"active": [encoded(255)]})
    p2 = Region("P2", "P2", "arrow", 20, 0, 30, 30, direction="Center",
                reference_images={"hit": [encoded(0)]})
    j1 = Region("Judgment", "P1", "judgment", 50, 0, 20, 20,
                reference_images={"perfect": [encoded(255)]})
    j2 = Region("Judgment", "P2", "judgment", 70, 0, 20, 20)
    bus = EventBus()
    session = AnalysisSession(bus, shared_player_references=True)
    session.set_regions([p1, p2, j1, j2])
    session.start()
    pixels = np.full((30, 90, 3), 128, dtype=np.uint8)
    pixels[:, 20:50] = 255
    pixels[:, 70:] = 255
    session.process_arrow_frame(pixels, 1, 100)
    session.process_arrow_frame(pixels, 2, 400)
    hits = [e for e in bus.events if e.event_type == "arrow_hit"]
    assert len(hits) == 1 and hits[0].player == "P2"
    assert hits[0].payload["direction"] == "Center"


@pytest.mark.parametrize("method,args", [
    ("capture_arrow_reference", ("active",)), ("clear_arrow_reference", ("active",)),
    ("teach_score", ()),
])
def test_p2_calibration_actions_cannot_modify_shared_p1(method, args):
    p1 = Region("P1", "P1", "score", 0, 0, 20, 20)
    p2 = Region("P2", "P2", "score", 20, 0, 20, 20)
    app = Mock(regions=[p1, p2])
    with patch("app.ui.app_window.messagebox.showinfo") as info:
        getattr(App, method)(app, p2, *args)
    info.assert_called_once()
    app.analysis.set_regions.assert_not_called()


def test_p1_capture_and_clear_refresh_p2_cache_and_previews():
    p1 = Region("P1", "P1", "arrow", 0, 0, 20, 20, direction="Center")
    p2 = Region("P2", "P2", "arrow", 20, 0, 20, 20, direction="Center")
    session = AnalysisSession(EventBus(), shared_player_references=True)
    session.set_regions([p1, p2])
    app = SimpleNamespace(
        regions=[p1, p2], analysis=session, _analysis_generation=0,
        _last_frame_bgr=np.full((20, 40, 3), 255, dtype=np.uint8),
        video_tab=Mock(), _refresh_arrow_reference_thumbnails=Mock(),
    )
    App.capture_arrow_reference(app, p1, "active")
    assert p2.reference_images == p1.reference_images
    assert "active" in session._arrow_references[p2.key()]
    app._refresh_arrow_reference_thumbnails.assert_called_once()
    App.clear_arrow_reference(app, p1, "active")
    assert not p1.reference_images and not p2.reference_images
    assert not session._arrow_references[p2.key()]
    app.video_tab.set_arrow_reference_thumbnail.assert_any_call(p2.key(), "active", None, 0)
