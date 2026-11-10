from unittest.mock import Mock, patch
from types import SimpleNamespace

import numpy as np
import pytest

from app import config, image_utils, layout_store
from app.analysis import AnalysisSession
from app.event_bus import EventBus
from app.models import LayoutProfile, Region, share_player_references
from app.ui.app_window import App


def encoded(value, width=20, height=20):
    return image_utils.encode_crop_to_base64_png(
        np.full((height, width, 3), value, dtype=np.uint8),
    )


@pytest.mark.parametrize("kind", ["arrow", "judgment", "score", "combo"])
def test_default_calibration_is_independent_through_reload_resizing_and_save(kind, tmp_path):
    p1 = Region("P1", "P1", kind, 0, 0, 20, 20, direction="Center",
                reference_images={"perfect": [encoded(255)]},
                digit_templates={"1": [encoded(255, 24, 32)]})
    p2 = Region("P2", "P2", kind, 30, 0, 18, 18, direction="Center",
                reference_images={"perfect": [encoded(0)]},
                digit_templates={"2": [encoded(0, 24, 32)]})
    session = AnalysisSession(EventBus())
    before = [p1.to_dict(), p2.to_dict()]
    session.set_regions([p1, p2])
    layout_store.match_player_region_sizes([p1, p2], 60, 30)
    session.set_regions([p1, p2])
    for region, saved in zip((p1, p2), before):
        assert region.reference_images == saved["reference_images"]
        assert region.digit_templates == saved["digit_templates"]
    path = str(tmp_path / "independent.json")
    layout_store.save_layout_to_path(LayoutProfile("independent", 60, 30, [p1, p2]), path)
    loaded = layout_store.load_layout_from_path(path)
    session.set_regions(loaded.regions)
    assert loaded.regions[0].reference_images != loaded.regions[1].reference_images
    assert loaded.regions[0].digit_templates != loaded.regions[1].digit_templates


def test_p2_outcomes_use_its_own_different_judgment_references():
    p1 = Region("P1", "P1", "judgment", 0, 0, 20, 20,
                reference_images={"perfect": [encoded(255)]})
    p2 = Region("P2", "P2", "judgment", 20, 0, 20, 20,
                reference_images={"perfect": [encoded(0)]})
    bus = EventBus()
    session = AnalysisSession(bus)
    session.set_regions([p1, p2])
    session.start()
    pixels = np.full((20, 40, 3), 128, dtype=np.uint8)
    pixels[:, 20:] = 0
    session.process_arrow_frame(pixels, 1, 100)
    session.process_arrow_frame(pixels, 2, 750)
    outcomes = [event for event in bus.events if event.event_type == "judgment_hit"]
    assert len(outcomes) == 1
    assert outcomes[0].player == "P2" and outcomes[0].payload["judgment"] == "perfect"


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


def test_arrow_direction_matching_and_p2_only_calibration():
    p1 = Region("P1", "P1", "arrow", 0, 0, 20, 20, direction="Center",
                reference_images={"hit": [encoded(255)]})
    p2 = Region("P2", "P2", "arrow", 20, 0, 20, 20, direction="Up-Right",
                reference_images={"hit": [encoded(0)]})
    shared_end = Region("End", "Unknown", "game_end", 0, 20, 20, 20,
                        reference_images={"ended": [encoded(128)]})
    share_player_references([p1, p2, shared_end])
    assert p2.reference_images["hit"] == [encoded(0)]
    assert shared_end.reference_images["ended"] == [encoded(128)]
    p2.direction = "Center"
    share_player_references([p1, p2])
    assert p2.reference_images == p1.reference_images
    share_player_references([p2])
    assert p2.reference_images == p1.reference_images


@pytest.mark.parametrize("kind", ["score", "combo"])
@pytest.mark.parametrize("player", ["P1", "P2"])
def test_teaching_either_player_updates_both_numeric_caches_without_resetting_trackers(kind, player):
    p1 = Region("P1", "P1", kind, 0, 0, 20, 20)
    p2 = Region("P2", "P2", kind, 20, 0, 30, 30)
    session = AnalysisSession(EventBus(), shared_player_references=True)
    session.set_regions([p1, p2])
    trackers = [session.tracker_for(region) for region in (p1, p2)]
    taught = p1 if player == "P1" else p2
    taught.digit_templates = {"1": [encoded(255, 24, 32)]}
    taught.digit_templates_reviewed = True
    taught.score_template_version = config.DIGIT_TEMPLATE_VERSIONS[kind]
    session.update_score_templates(taught)
    for region, tracker in zip((p1, p2), trackers):
        assert session.uses_score_templates(region)
        assert session.tracker_for(region) is tracker
        assert "1" in session._score_templates[region.key()]
    taught.digit_templates = {}
    session.update_score_templates(taught)
    assert not session.uses_score_templates(p1)
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
    session.process_arrow_frame(pixels, 2, 750)
    hits = [e for e in bus.events if e.event_type == "arrow_hit"]
    assert len(hits) == 1 and hits[0].player == "P2"
    assert hits[0].payload["direction"] == "Center"


@pytest.mark.parametrize("kind,state", [("arrow", "active"), ("arrow", "empty"), ("judgment", "perfect")])
@pytest.mark.parametrize("player", ["P1", "P2"])
def test_capture_and_clear_change_only_the_selected_player(kind, state, player):
    p1 = Region("P1", "P1", kind, 0, 0, 20, 20, direction="Center")
    p2 = Region("P2", "P2", kind, 20, 0, 20, 20, direction="Center")
    session = AnalysisSession(EventBus())
    session.set_regions([p1, p2])
    app = SimpleNamespace(
        regions=[p1, p2], analysis=session, _analysis_generation=0,
        _last_frame_bgr=np.concatenate([
            np.full((20, 20, 3), 64, dtype=np.uint8),
            np.full((20, 20, 3), 255, dtype=np.uint8),
        ], axis=1),
        video_tab=Mock(), _refresh_arrow_reference_thumbnails=Mock(),
    )
    selected = p1 if player == "P1" else p2
    other = p2 if player == "P1" else p1
    App.capture_arrow_reference(app, selected, state)
    assert not other.reference_images
    assert np.all(session._arrow_references[selected.key()][state][0] == (64 if player == "P1" else 255))
    assert state not in session._arrow_references[other.key()]
    app._refresh_arrow_reference_thumbnails.assert_called_once()
    App.capture_arrow_reference(app, other, state)
    assert len(p1.reference_images[state]) == len(p2.reference_images[state]) == 1
    other_references = other.to_dict()
    App.clear_arrow_reference(app, selected, state)
    assert not selected.reference_images
    assert other.to_dict() == other_references
    assert not session._arrow_references[selected.key()]
    assert state in session._arrow_references[other.key()]
    app.video_tab.set_arrow_reference_thumbnail.assert_called_once_with(selected.key(), state, None, 0)


@pytest.mark.parametrize("kind", ["arrow", "judgment", "score", "combo"])
def test_p2_only_calibration_survives_save_load_and_adding_p1(kind, tmp_path):
    p2 = Region("P2", "P2", kind, 20, 0, 20, 20, direction="Center",
                reference_images={"active": [encoded(255)]},
                digit_templates={"1": [encoded(255, 24, 32)]},
                digit_templates_reviewed=True,
                score_template_version=config.DIGIT_TEMPLATE_VERSIONS.get(kind, 0))
    session = AnalysisSession(EventBus())
    session.set_regions([p2])
    path = str(tmp_path / "p2-only.json")
    layout_store.save_layout_to_path(LayoutProfile("P2 only", 40, 20, [p2]), path)
    loaded = layout_store.load_layout_from_path(path)
    p2 = loaded.regions[0]
    session.set_regions([p2])
    app = SimpleNamespace(
        regions=[p2], analysis=session, _analysis_generation=0, video_tab=Mock(),
        video_source=SimpleNamespace(width=40, height=20),
        wait_window=Mock(), _refresh_arrow_reference_thumbnails=Mock(),
        _refresh_score_calibration_status=Mock(),
    )
    with patch("app.ui.app_window.RegionDialog", return_value=SimpleNamespace(
        result=("P1", "P1", kind, "Center"),
    )):
        App.on_region_drawn(app, 0, 0, 20, 20)
    p1 = app.regions[1]
    assert not p1.reference_images and p2.reference_images == {"active": [encoded(255)]}
    assert not p1.digit_templates and p2.digit_templates == {"1": [encoded(255, 24, 32)]}
    assert not p1.digit_templates_reviewed and p2.digit_templates_reviewed
    session.set_regions([p2])
    assert p2.reference_images and p2.digit_templates


@pytest.mark.parametrize("kind", ["score", "combo"])
def test_teach_p2_dialog_uses_p2_crop_and_preserves_p1_digits(kind):
    p1 = Region("P1", "P1", kind, 0, 0, 20, 20)
    p2 = Region("P2", "P2", kind, 20, 0, 20, 20)
    session = AnalysisSession(EventBus())
    session.set_regions([p1, p2])
    frame = np.zeros((20, 40, 3), dtype=np.uint8)
    frame[:, 20:] = 255
    app = SimpleNamespace(
        regions=[p1, p2], analysis=session, _analysis_generation=0,
        _last_frame_bgr=frame, _stop_playing=Mock(), wait_window=Mock(),
        _refresh_score_calibration_status=Mock(), video_tab=Mock(), video_source=None,
    )
    result = {"1": [encoded(255, 24, 32)]}
    with patch("app.ui.app_window.ScoreCalibrationDialog", return_value=SimpleNamespace(result=result)) as dialog:
        App.teach_score(app, p2)
    assert dialog.call_args.args[1] is p2
    assert np.all(dialog.call_args.args[2] == 255)
    assert not p1.digit_templates and p2.digit_templates == result
    assert not session.uses_score_templates(p1) and session.uses_score_templates(p2)


@pytest.mark.parametrize("player", ["P1", "P2"])
@pytest.mark.parametrize("state", ["active", "empty"])
def test_deleting_one_reference_preserves_other_samples_and_player_and_round_trips(player, state, tmp_path):
    examples = [encoded(value) for value in (64, 128, 192)]
    other_state = "empty" if state == "active" else "active"
    p1 = Region("P1", "P1", "arrow", 0, 0, 20, 20, direction="Center",
                reference_images={state: list(examples), other_state: [encoded(255)]})
    p2 = Region("P2", "P2", "arrow", 20, 0, 20, 20, direction="Center",
                reference_images={state: list(examples), other_state: [encoded(255)]})
    selected, other = (p1, p2) if player == "P1" else (p2, p1)
    session = AnalysisSession(EventBus())
    session.set_regions([p1, p2])
    before = other.to_dict()
    app = SimpleNamespace(
        regions=[p1, p2], analysis=session, _analysis_generation=0,
        video_tab=Mock(), _refresh_arrow_reference_thumbnails=Mock(),
    )
    assert App.delete_arrow_reference(app, selected, state, 1)
    assert selected.reference_images[state] == [examples[0], examples[2]]
    assert other.to_dict() == before
    decoded = session._arrow_references[selected.key()][state]
    assert len(decoded) == 2 and np.all(decoded[0] == 64) and np.all(decoded[1] == 192)
    path = str(tmp_path / "deleted.json")
    layout_store.save_layout_to_path(LayoutProfile("deleted", 40, 20, [p1, p2]), path)
    restored = {region.key(): region for region in layout_store.load_layout_from_path(path).regions}
    assert restored[selected.key()].reference_images == selected.reference_images
    assert restored[other.key()].to_dict() == before
    assert App.delete_arrow_reference(app, selected, state, 0)
    assert App.delete_arrow_reference(app, selected, state, 0)
    assert state not in selected.reference_images
    assert state not in session._arrow_references[selected.key()]
    assert selected.reference_images[other_state] == [encoded(255)]
    assert other_state in session._arrow_references[selected.key()]
    assert other.to_dict() == before
    app.video_tab.set_arrow_reference_thumbnail.assert_called_once_with(selected.key(), state, None, 0)


@pytest.mark.parametrize("index", [-1, 3])
def test_invalid_reference_index_is_reported_without_mutating_samples(index):
    region = Region("P1", "P1", "arrow", 0, 0, 20, 20, direction="Center",
                    reference_images={"active": [encoded(255)]})
    app = SimpleNamespace(_analysis_generation=0)
    with patch("app.ui.app_window.messagebox.showerror") as error:
        assert not App.delete_arrow_reference(app, region, "active", index)
    error.assert_called_once()
    assert region.reference_images == {"active": [encoded(255)]}
    assert app._analysis_generation == 0


@pytest.mark.parametrize("state", ["active", "empty"])
def test_gallery_displays_every_reference_and_deletes_individually_with_live_preview_updates(state):
    import tkinter as tk
    from tkinter import ttk
    from app.ui.video_tab import ReferenceGalleryDialog, VideoTab

    root = tk.Tk()
    root.withdraw()
    examples = [encoded(value) for value in (64, 128, 192)]
    p1 = Region("P1", "P1", "arrow", 0, 0, 20, 20, direction="Center",
                reference_images={"active": [encoded(255)]})
    p2 = Region("P2", "P2", "arrow", 20, 0, 20, 20, direction="Center",
                reference_images={state: list(examples)})
    controller = Mock()
    controller.regions = [p1, p2]
    controller.analysis = AnalysisSession(EventBus())
    controller.analysis.set_regions(controller.regions)
    controller._analysis_generation = 0
    controller._make_thumbnail_photo = App._make_thumbnail_photo
    controller._refresh_arrow_reference_thumbnails = lambda: App._refresh_arrow_reference_thumbnails(controller)
    controller.delete_arrow_reference = lambda region, state, index: App.delete_arrow_reference(controller, region, state, index)
    controller.video_tab = VideoTab(root, controller)
    controller.video_tab.refresh_region_list(controller.regions)
    controller._refresh_arrow_reference_thumbnails()
    gallery = ReferenceGalleryDialog(root, controller, p2, state)
    try:
        assert gallery.count_var.get() == "3 references"
        assert len(gallery._photos) == 3
        cards = gallery.images.inner.winfo_children()
        assert len(cards) == 3
        button, = [widget for widget in cards[1].winfo_children() if isinstance(widget, ttk.Button)]
        button.invoke()
        assert p2.reference_images[state] == [examples[0], examples[2]]
        assert gallery.count_var.get() == "2 references" and len(gallery._photos) == 2
        assert controller.video_tab._region_rows[p2.key()][f"{state}_ref_label"]["text"] == "2 refs"
        for expected in (1, 0):
            card = gallery.images.inner.winfo_children()[0]
            button, = [widget for widget in card.winfo_children() if isinstance(widget, ttk.Button)]
            button.invoke()
            assert gallery.count_var.get() == f"{expected} reference{'s' if expected != 1 else ''}"
        assert not gallery._photos
        assert gallery.images.inner.winfo_children()[0]["text"] == "No references captured for this box."
        assert controller.video_tab._region_rows[p2.key()][f"{state}_ref_label"]["text"] == "(none)"
        assert p1.reference_images["active"] == [encoded(255)]
    finally:
        gallery.destroy()
        root.destroy()


def test_gallery_surfaces_undecodable_reference_instead_of_hiding_it():
    import tkinter as tk
    from app.ui.video_tab import ReferenceGalleryDialog

    root = tk.Tk()
    root.withdraw()
    region = Region("P1", "P1", "arrow", 0, 0, 20, 20, direction="Center",
                    reference_images={"active": ["invalid-image"]})
    gallery = ReferenceGalleryDialog(root, Mock(), region, "active")
    try:
        card = gallery.images.inner.winfo_children()[0]
        assert card.winfo_children()[0]["text"] == "Cannot decode this reference image."
        assert gallery.count_var.get() == "1 reference"
    finally:
        gallery.destroy()
        root.destroy()


def test_numeric_teaching_buttons_enabled_and_routed_for_both_players():
    import tkinter as tk
    from tkinter import ttk
    from app.ui.video_tab import VideoTab

    root = tk.Tk()
    root.withdraw()
    controller = Mock()
    regions = [
        Region(player, player, kind, 0, 0, 20, 20)
        for player in ("P1", "P2") for kind in ("score", "combo")
    ]
    tab = VideoTab(root, controller)
    try:
        tab.refresh_region_list(regions)
        for region in regions:
            card = tab._region_rows[region.key()]["thumb_label"].master
            button, = [
                widget for widget in card.winfo_children()
                if isinstance(widget, ttk.Button) and widget["text"] == f"Teach {region.kind}..."
            ]
            controller.teach_score.reset_mock()
            button.invoke()
            assert not button.instate(["disabled"])
            controller.teach_score.assert_called_once_with(region)
    finally:
        root.destroy()


def test_active_reference_capture_refreshes_real_cards_with_legacy_images(tmp_path):
    import tkinter as tk
    from tkinter import ttk
    from app.ui.video_tab import VideoTab

    root = tk.Tk()
    root.withdraw()
    p1 = Region("P1", "P1", "arrow", 0, 0, 20, 20, direction="Center",
                reference_images={"hit": [encoded(255)], "miss": [encoded(0)], "appeared": [encoded(128)]})
    p2 = Region("P2", "P2", "arrow", 20, 0, 20, 20, direction="Center")
    judgment = Region("Judgment", "P1", "judgment", 40, 0, 20, 20,
                      reference_images={"hit": [encoded(255)], "idle": [encoded(128)]})
    controller = Mock()
    controller.regions = [p1, p2, judgment]
    controller.analysis = AnalysisSession(EventBus())
    controller.analysis.set_regions(controller.regions)
    controller._analysis_generation = 0
    controller._last_frame_bgr = np.full((20, 60, 3), 255, dtype=np.uint8)
    controller._make_thumbnail_photo = App._make_thumbnail_photo
    controller._refresh_arrow_reference_thumbnails = lambda: App._refresh_arrow_reference_thumbnails(controller)
    controller.capture_arrow_reference = lambda region, state: App.capture_arrow_reference(controller, region, state)
    tab = VideoTab(root, controller)
    controller.video_tab = tab
    try:
        tab.refresh_region_list(controller.regions)
        controller._refresh_arrow_reference_thumbnails()
        card = tab._region_rows[p2.key()]["thumb_label"].master
        button, = [
            widget for group in card.winfo_children() if isinstance(group, ttk.Frame)
            for widget in group.winfo_children()
            if isinstance(widget, ttk.Button) and widget["text"] == "Add Active Arrow ref"
        ]
        for expected_count in (1, 2):
            button.invoke()
            for region in (p2,):
                row = tab._region_rows[region.key()]
                assert len(region.reference_images["active"]) == expected_count
                assert row["active_ref_label"]["image"]
                assert row["active_ref_label"]["text"] == f"{expected_count} ref{'s' if expected_count > 1 else ''}"
                assert "Reference ready" in row["status_var"].get()
                assert len(controller.analysis._arrow_references[region.key()]["active"]) == expected_count
        path = str(tmp_path / "captured.json")
        layout_store.save_layout_to_path(LayoutProfile("captured", 60, 20, controller.regions), path)
        restored = layout_store.load_layout_from_path(path)
        assert "active" not in restored.regions[0].reference_images
        assert len(restored.regions[1].reference_images["active"]) == 2
        assert "hit" in restored.regions[0].reference_images
        App.clear_arrow_reference(controller, p2, "active")
        for region in (p2,):
            assert tab._region_rows[region.key()]["active_ref_label"]["text"] == "(none)"
    finally:
        root.destroy()
