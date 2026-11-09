"""Tests for app.layout_store: saving/loading layout profiles and explicit
resolution-mismatch handling (never silently rescale)."""
from __future__ import annotations

import os
import sys
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app import config, layout_store
from app.models import LayoutProfile, Region


@pytest.fixture(autouse=True)
def _use_tmp_layouts_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LAYOUTS_DIR", str(tmp_path))
    yield


def _sample_profile(name="test_profile", width=1920, height=1080):
    return LayoutProfile(
        profile_name=name,
        source_width=width,
        source_height=height,
        regions=[
            Region(label="P1 Score", player="P1", kind="score", x=10, y=20, w=100, h=30),
            Region(label="P1 Combo", player="P1", kind="combo", x=10, y=60, w=60, h=30),
        ],
    )


def test_save_and_load_round_trip():
    profile = _sample_profile()
    layout_store.save_layout(profile)

    loaded = layout_store.load_layout("test_profile")
    assert loaded.profile_name == "test_profile"
    assert loaded.source_width == 1920
    assert loaded.source_height == 1080
    assert len(loaded.regions) == 2
    assert loaded.regions[0].label == "P1 Score"
    assert loaded.regions[0].player == "P1"
    assert loaded.regions[0].kind == "score"


@pytest.mark.parametrize("kind", ["arrow", "judgment", "score", "combo"])
def test_matching_player_sizes_preserves_centers_references_and_settings(kind):
    p1 = Region("P1", "P1", kind, 0, 0, 40, 20, direction="Center")
    p2 = Region("P2", "P2", kind, 80, 40, 30, 10, direction="Center",
                reference_images={"active": ["example"]}, digit_templates={"1": ["digit"]},
                combo_yellow_filter=True, combo_fast_mode=True)
    before = p2.to_dict()
    assert layout_store.match_player_region_sizes([p2, p1], 200, 100) == [p2]
    assert (p2.x, p2.y, p2.w, p2.h) == (75, 35, 40, 20)
    assert (p1.x, p1.y, p1.w, p1.h) == (0, 0, 40, 20)
    assert {key: value for key, value in p2.to_dict().items() if key not in ("x", "y", "w", "h")} == {
        key: value for key, value in before.items() if key not in ("x", "y", "w", "h")
    }
    assert not layout_store.match_player_region_sizes([p2, p1], 200, 100)
    profile = LayoutProfile("matched", 200, 100, [p1, p2])
    layout_store.save_layout(profile)
    restored = layout_store.load_layout("matched").regions
    assert restored[0].w == restored[1].w == 40
    assert restored[0].h == restored[1].h == 20


def test_size_matching_only_changes_corresponding_supported_player_boxes():
    regions = [
        Region("P1 arrow", "P1", "arrow", 0, 0, 40, 20, direction="Center"),
        Region("Other arrow", "P2", "arrow", 0, 0, 10, 10, direction="Up-Left"),
        Region("P2 judgment", "P2", "judgment", 0, 0, 30, 10),
        Region("Unknown", "Unknown", "arrow", 0, 0, 10, 10, direction="Center"),
        Region("End", "P2", "game_end", 0, 0, 10, 10),
    ]
    before = [region.to_dict() for region in regions]
    assert not layout_store.match_player_region_sizes(regions, 100, 100)
    assert [region.to_dict() for region in regions] == before


@pytest.mark.parametrize("x,y", [(0, 0), (90, 90)])
def test_matching_sizes_keeps_exact_dimensions_inside_frame_edges(x, y):
    p1 = Region("P1", "P1", "judgment", 0, 0, 40, 30)
    p2 = Region("P2", "P2", "judgment", x, y, 10, 10)
    layout_store.match_player_region_sizes([p1, p2], 100, 100)
    assert (p2.w, p2.h) == (40, 30)
    assert 0 <= p2.x <= 60 and 0 <= p2.y <= 70


def test_matching_sizes_rejects_impossible_dimensions_without_partial_changes():
    regions = [
        Region("P1 score", "P1", "score", 0, 0, 40, 20),
        Region("P2 score", "P2", "score", 80, 40, 30, 10),
        Region("P1 judgment", "P1", "judgment", 0, 0, 201, 20),
        Region("P2 judgment", "P2", "judgment", 0, 0, 30, 10),
    ]
    before = [region.to_dict() for region in regions]
    with pytest.raises(ValueError, match="cannot fit"):
        layout_store.match_player_region_sizes(regions, 200, 100)
    assert [region.to_dict() for region in regions] == before
    with pytest.raises(ValueError, match="positive"):
        layout_store.match_player_region_sizes(regions, 0, 100)


@pytest.mark.parametrize("first_player", ["P1", "P2"])
def test_drawing_matching_box_uses_p1_size_regardless_of_drawing_order(first_player):
    from app.ui.app_window import App

    first = Region("First", first_player, "judgment", 0, 0, 40, 20)
    second_player = "P2" if first_player == "P1" else "P1"
    app = SimpleNamespace(
        regions=[first], video_source=SimpleNamespace(width=200, height=100),
        wait_window=Mock(), analysis=Mock(), video_tab=Mock(), _analysis_generation=0,
        _refresh_arrow_reference_thumbnails=Mock(), _refresh_score_calibration_status=Mock(),
    )
    with patch("app.ui.app_window.RegionDialog", return_value=SimpleNamespace(
        result=("Second", second_player, "judgment", None),
    )):
        App.on_region_drawn(app, 80, 40, 30, 10)
    assert len(app.regions) == 2
    p1 = next(region for region in app.regions if region.player == "P1")
    p2 = next(region for region in app.regions if region.player == "P2")
    expected = (40, 20) if first_player == "P1" else (30, 10)
    assert (p1.w, p1.h) == (p2.w, p2.h) == expected
    app.analysis.set_regions.assert_called_once_with(app.regions)
    app.video_tab.set_processing_status.assert_called_once()


def test_drawing_p1_does_not_create_p2_automatically():
    from app.ui.app_window import App

    app = SimpleNamespace(
        regions=[], video_source=SimpleNamespace(width=200, height=100),
        wait_window=Mock(), analysis=Mock(), video_tab=Mock(), _analysis_generation=0,
        _refresh_arrow_reference_thumbnails=Mock(), _refresh_score_calibration_status=Mock(),
    )
    with patch("app.ui.app_window.RegionDialog", return_value=SimpleNamespace(
        result=("P1", "P1", "judgment", None),
    )):
        App.on_region_drawn(app, 0, 0, 40, 20)
    assert len(app.regions) == 1 and app.regions[0].player == "P1"


def test_match_sizes_action_refreshes_analysis_and_previews():
    from app.ui.app_window import App

    p1 = Region("P1", "P1", "judgment", 0, 0, 40, 20)
    p2 = Region("P2", "P2", "judgment", 80, 40, 30, 10)
    app = SimpleNamespace(
        regions=[p1, p2], video_source=SimpleNamespace(width=200, height=100),
        analysis=Mock(), video_tab=Mock(), _analysis_generation=0,
        _refresh_arrow_reference_thumbnails=Mock(), _refresh_score_calibration_status=Mock(),
    )
    App.match_player_region_sizes(app)
    assert (p2.w, p2.h) == (40, 20)
    app.analysis.set_regions.assert_called_once_with(app.regions)
    app.video_tab.refresh_region_list.assert_called_once_with(app.regions)
    app.video_tab.redraw_region_overlays.assert_called_once_with(app.regions)
    app._refresh_arrow_reference_thumbnails.assert_called_once()
    app._refresh_score_calibration_status.assert_called_once()
    App.match_player_region_sizes(app)
    app.analysis.set_regions.assert_called_once()


def test_loading_layout_matches_p2_sizes_and_reports_changes():
    from app.ui.app_window import App

    p1 = Region("P1", "P1", "judgment", 0, 0, 40, 20)
    p2 = Region("P2", "P2", "judgment", 80, 40, 30, 10)
    profile = LayoutProfile("loaded", 200, 100, [p1, p2])
    app = SimpleNamespace(
        regions=[], video_source=SimpleNamespace(width=200, height=100),
        analysis=Mock(), video_tab=Mock(), _analysis_generation=0,
        _refresh_arrow_reference_thumbnails=Mock(), _refresh_score_calibration_status=Mock(),
    )
    App._apply_loaded_profile(app, lambda width, height: profile)
    assert (p2.x, p2.y, p2.w, p2.h) == (75, 35, 40, 20)
    assert app.regions == [p1, p2]
    app.analysis.set_regions.assert_called_once_with(app.regions)
    assert "matched 1" in app.video_tab.set_processing_status.call_args.args[0]


def test_loading_unmatchable_layout_keeps_current_layout():
    from app.ui.app_window import App

    current = [Region("Current", "P1", "judgment", 0, 0, 40, 20)]
    profile = LayoutProfile("invalid", 200, 100, [
        Region("P1", "P1", "judgment", 0, 0, 201, 20),
        Region("P2", "P2", "judgment", 80, 40, 30, 10),
    ])
    app = SimpleNamespace(
        regions=current, video_source=SimpleNamespace(width=200, height=100),
        analysis=Mock(), video_tab=Mock(), _analysis_generation=0,
    )
    with patch("app.ui.app_window.messagebox.showerror") as error:
        App._apply_loaded_profile(app, lambda width, height: profile)
    error.assert_called_once()
    assert app.regions is current
    app.analysis.set_regions.assert_not_called()


@pytest.mark.parametrize("x,y,expected", [(85, 40, (85, 40)), (-10, 120, (0, 80)), (190, -5, (160, 0))])
def test_moving_region_preserves_size_and_other_player_and_stays_in_bounds(x, y, expected):
    from app.ui.app_window import App

    p1 = Region("P1", "P1", "judgment", 0, 0, 40, 20)
    p2 = Region("P2", "P2", "judgment", 80, 40, 40, 20)
    app = SimpleNamespace(
        regions=[p1, p2], video_source=SimpleNamespace(width=200, height=100),
        analysis=Mock(), video_tab=Mock(), _analysis_generation=0,
        _refresh_arrow_reference_thumbnails=Mock(), _refresh_score_calibration_status=Mock(),
    )
    App.move_region(app, p2, x, y)
    assert (p2.x, p2.y) == expected
    assert (p2.w, p2.h) == (40, 20)
    assert (p1.x, p1.y, p1.w, p1.h) == (0, 0, 40, 20)
    app.analysis.set_regions.assert_called_once_with(app.regions)
    app.video_tab.redraw_region_overlays.assert_called_once_with(app.regions)


def test_list_layout_names_returns_saved_profiles():
    layout_store.save_layout(_sample_profile("alpha"))
    layout_store.save_layout(_sample_profile("beta"))
    names = layout_store.list_layout_names()
    assert set(names) == {"alpha", "beta"}


def test_combo_yellow_filter_round_trip_and_rescale():
    profile = _sample_profile()
    profile.regions[1].combo_yellow_filter = True
    profile.regions[1].combo_fast_mode = True
    layout_store.save_layout(profile)
    loaded = layout_store.load_layout(profile.profile_name)
    assert not loaded.regions[0].combo_yellow_filter
    assert loaded.regions[1].combo_yellow_filter
    assert loaded.regions[1].combo_fast_mode
    scaled = layout_store.rescale_profile(loaded, 1280, 720)
    assert not scaled.regions[0].combo_yellow_filter
    assert scaled.regions[1].combo_yellow_filter
    assert scaled.regions[1].combo_fast_mode
    layout_store.save_layout(scaled)
    assert layout_store.load_layout(profile.profile_name).regions[1].combo_yellow_filter


def test_legacy_layouts_default_to_unfiltered_combo():
    data = _sample_profile().to_dict()
    for region in data["regions"]:
        region.pop("combo_yellow_filter")
        region.pop("combo_fast_mode")
    loaded = LayoutProfile.from_dict(data)
    assert all(not region.combo_yellow_filter for region in loaded.regions)
    assert all(not region.combo_fast_mode for region in loaded.regions)


def test_load_layout_checked_raises_on_resolution_mismatch():
    layout_store.save_layout(_sample_profile("hd", width=1920, height=1080))
    with pytest.raises(layout_store.ResolutionMismatchError) as exc_info:
        layout_store.load_layout_checked("hd", current_width=1280, current_height=720)
    err = exc_info.value
    assert err.profile.source_width == 1920
    assert err.current_width == 1280


def test_load_layout_checked_succeeds_on_matching_resolution():
    layout_store.save_layout(_sample_profile("hd", width=1920, height=1080))
    profile = layout_store.load_layout_checked("hd", current_width=1920, current_height=1080)
    assert profile.profile_name == "hd"


def test_rescale_profile_scales_region_coordinates_proportionally():
    profile = _sample_profile(width=1920, height=1080)
    rescaled = layout_store.rescale_profile(profile, new_width=960, new_height=540)
    assert rescaled.source_width == 960
    assert rescaled.source_height == 540
    original = profile.regions[0]
    scaled = rescaled.regions[0]
    assert scaled.x == original.x // 2
    assert scaled.w == original.w // 2


def test_rescale_profile_preserves_regions_when_new_video_is_taller():
    profile = LayoutProfile(
        profile_name="wide-to-square",
        source_width=800,
        source_height=400,
        regions=[
            Region(label="P1 Score", player="P1", kind="score", x=100, y=50, w=200, h=100),
        ],
    )

    rescaled = layout_store.rescale_profile(profile, new_width=400, new_height=400)

    region = rescaled.regions[0]
    assert (region.x, region.y, region.w, region.h) == (50, 125, 100, 50)


def test_rescale_profile_preserves_regions_when_new_video_is_wider():
    profile = LayoutProfile(
        profile_name="tall-to-square",
        source_width=400,
        source_height=800,
        regions=[
            Region(label="P1 Score", player="P1", kind="score", x=50, y=100, w=100, h=200),
        ],
    )

    rescaled = layout_store.rescale_profile(profile, new_width=400, new_height=400)

    region = rescaled.regions[0]
    assert (region.x, region.y, region.w, region.h) == (125, 50, 50, 100)


@pytest.mark.parametrize(
    ("source_width", "source_height", "new_width", "new_height"),
    [
        (0, 1080, 960, 540),
        (1920, 0, 960, 540),
        (1920, 1080, 0, 540),
        (1920, 1080, 960, 0),
    ],
)
def test_rescale_profile_rejects_non_positive_dimensions(
    source_width, source_height, new_width, new_height
):
    profile = _sample_profile(width=source_width, height=source_height)
    with pytest.raises(ValueError):
        layout_store.rescale_profile(profile, new_width=new_width, new_height=new_height)


def test_rescale_profile_resizes_arrow_reference_images_too():
    from app import image_utils
    import numpy as np

    ref_crop = np.full((40, 40, 3), 200, dtype="uint8")
    arrow_region = Region(
        label="P1 Down-Left", player="P1", kind="arrow", x=0, y=0, w=40, h=40, direction="Down-Left",
        reference_images={"hit": [
            image_utils.encode_crop_to_base64_png(ref_crop),
            image_utils.encode_crop_to_base64_png(ref_crop),
        ]},
    )
    profile = LayoutProfile(
        profile_name="arrows", source_width=1920, source_height=1080, regions=[arrow_region]
    )

    rescaled = layout_store.rescale_profile(profile, new_width=960, new_height=540)
    rescaled_region = rescaled.regions[0]
    assert rescaled_region.w == 20 and rescaled_region.h == 20

    assert len(rescaled_region.reference_images["hit"]) == 2
    decoded = image_utils.decode_base64_png_to_crop(rescaled_region.reference_images["hit"][0])
    assert decoded.shape[:2] == (20, 20)  # every reference image is resized with its region


# ---------------------------------------------------------------------
# Arbitrary-path save/load (Save Layout As... / Load Layout From...)
# ---------------------------------------------------------------------

def test_save_and_load_to_arbitrary_path_outside_layouts_dir(tmp_path):
    custom_dir = tmp_path / "some" / "other" / "folder"
    custom_path = str(custom_dir / "my_layout.json")
    profile = _sample_profile(name="my_layout")

    saved_path = layout_store.save_layout_to_path(profile, custom_path)
    assert saved_path == custom_path
    assert os.path.isfile(custom_path)
    # Must not also land in the default named-profile folder.
    assert "my_layout" not in layout_store.list_layout_names()

    loaded = layout_store.load_layout_from_path(custom_path)
    assert loaded.profile_name == "my_layout"
    assert loaded.source_width == 1920
    assert len(loaded.regions) == 2


def test_load_layout_from_path_checked_raises_on_resolution_mismatch(tmp_path):
    path = str(tmp_path / "hd.json")
    layout_store.save_layout_to_path(_sample_profile("hd", width=1920, height=1080), path)
    with pytest.raises(layout_store.ResolutionMismatchError):
        layout_store.load_layout_from_path_checked(path, current_width=1280, current_height=720)


def test_load_layout_from_path_checked_succeeds_on_matching_resolution(tmp_path):
    path = str(tmp_path / "hd.json")
    layout_store.save_layout_to_path(_sample_profile("hd", width=1920, height=1080), path)
    profile = layout_store.load_layout_from_path_checked(path, current_width=1920, current_height=1080)
    assert profile.profile_name == "hd"


def test_load_layout_from_path_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        layout_store.load_layout_from_path(str(tmp_path / "does_not_exist.json"))


@pytest.mark.parametrize("by_path", [False, True])
def test_ui_save_and_load_embeds_all_references_and_digit_samples(tmp_path, monkeypatch, by_path):
    import numpy as np

    from app import image_utils
    from app.analysis import AnalysisSession
    from app.event_bus import EventBus
    from app.ui.app_window import App

    reference = image_utils.encode_crop_to_base64_png(np.full((40, 40, 3), 200, dtype=np.uint8))
    digit = image_utils.encode_crop_to_base64_png(np.full((32, 24), 255, dtype=np.uint8))
    regions = []
    for player in ("P1", "P2"):
        for kind in ("score", "combo"):
            regions.append(Region(
                kind, player, kind, 0, 0, 100, 40,
                digit_templates={"1": [digit]}, digit_templates_reviewed=True,
                score_template_version=config.DIGIT_TEMPLATE_VERSIONS[kind],
            ))
        for direction in config.ARROW_DIRECTIONS:
            regions.append(Region(
                direction, player, "arrow", 0, 0, 40, 40, direction=direction,
                reference_images={state: [reference, reference] for state in config.ARROW_STATES},
            ))
    regions.append(Region(
        "Results", "Unknown", "game_end", 0, 0, 40, 40,
        reference_images={"ended": [reference]},
    ))
    confirmation = Mock()
    monkeypatch.setattr("app.ui.app_window.messagebox.showinfo", confirmation)
    controller = SimpleNamespace(
        video_source=SimpleNamespace(width=1920, height=1080), regions=regions,
        video_tab=Mock(), analysis=AnalysisSession(EventBus()), _analysis_generation=0,
        _refresh_arrow_reference_thumbnails=Mock(), _refresh_score_calibration_status=Mock(),
    )
    controller._build_current_profile = MethodType(App._build_current_profile, controller)
    controller._apply_loaded_profile = MethodType(App._apply_loaded_profile, controller)
    path = str(tmp_path / "portable" / "all_references.json")
    if by_path:
        App.save_layout_to_path(controller, path)
    else:
        App.save_layout(controller, "all_references")
        path = str(tmp_path / "all_references.json")
    assert "Includes all" in confirmation.call_args.args[1]
    saved = layout_store.load_layout_from_path(path)
    assert [region.to_dict() for region in saved.regions] == [region.to_dict() for region in regions]
    assert list((tmp_path / "portable" if by_path else tmp_path).iterdir()) == [
        tmp_path / "portable" / "all_references.json" if by_path else tmp_path / "all_references.json"
    ]
    controller.regions = []
    App.load_layout_from_path(controller, path)
    for region in controller.regions:
        if region.kind in ("score", "combo"):
            assert controller.analysis.uses_score_templates(region)
        elif region.kind == "arrow":
            assert set(controller.analysis._arrow_references[region.key()]) == set(config.ARROW_STATES)
        else:
            assert len(controller.analysis._arrow_references[region.key()]["ended"]) == 1
    controller._refresh_arrow_reference_thumbnails.assert_called_once()


@pytest.mark.parametrize("by_path", [False, True])
@pytest.mark.parametrize("rescale", [False, True])
@pytest.mark.parametrize("legacy_arrows", [False, True])
def test_ui_load_preserves_supported_regions_and_warns_before_recalibration(
    tmp_path, monkeypatch, by_path, rescale, legacy_arrows,
):
    import numpy as np

    from app import image_utils
    from app.ui.app_window import App

    profile = _sample_profile("five_arrows")
    profile.regions[0].digit_templates = {"9": ["score-template"]}
    profile.regions[0].digit_templates_reviewed = True
    profile.regions[1].combo_yellow_filter = True
    profile.regions.append(Region("Results", "Unknown", "game_end", 0, 0, 40, 40))
    reference = image_utils.encode_crop_to_base64_png(np.full((40, 40, 3), 200, dtype=np.uint8))
    profile.regions.extend(
        Region(direction, "P1", "arrow", 0, 0, 40, 40, direction=direction,
               reference_images={"hit": [reference]})
        for direction in config.ARROW_DIRECTIONS
    )
    if legacy_arrows:
        profile.regions.extend(
            Region(direction, "P1", "arrow", 0, 0, 40, 40, direction=direction)
            for direction in ("Up", "Down", "Left", "Right")
        )
    path = layout_store.save_layout(profile)
    original_file = (tmp_path / "five_arrows.json").read_bytes()
    warning = Mock()
    monkeypatch.setattr("app.ui.app_window.messagebox.showwarning", warning)
    monkeypatch.setattr("app.ui.app_window.messagebox.askyesno", Mock(return_value=True))
    controller = SimpleNamespace(
        video_source=SimpleNamespace(width=960 if rescale else 1920, height=540 if rescale else 1080),
        regions=[], _analysis_generation=0, analysis=Mock(), video_tab=Mock(),
        _refresh_arrow_reference_thumbnails=Mock(), _refresh_score_calibration_status=Mock(),
    )
    controller._apply_loaded_profile = MethodType(App._apply_loaded_profile, controller)
    if by_path:
        App.load_layout_from_path(controller, path)
    else:
        App.load_layout(controller, "five_arrows")

    assert len(controller.regions) == 8
    assert {region.direction for region in controller.regions if region.kind == "arrow"} == set(
        config.ARROW_DIRECTIONS
    )
    assert controller.regions[0].digit_templates == {"9": ["score-template"]}
    assert controller.regions[0].digit_templates_reviewed
    assert controller.regions[1].combo_yellow_filter
    assert controller.regions[2].kind == "game_end"
    assert controller.regions[0].w == (50 if rescale else 100)
    for region in controller.regions[3:]:
        crop = image_utils.decode_base64_png_to_crop(region.reference_images["hit"][0])
        assert crop.shape[:2] == ((20, 20) if rescale else (40, 40))
    assert controller._analysis_generation == 1
    controller.analysis.set_regions.assert_called_once_with(controller.regions)
    controller.video_tab.refresh_region_list.assert_called_once_with(controller.regions)
    controller.video_tab.redraw_region_overlays.assert_called_once_with(controller.regions)
    assert (tmp_path / "five_arrows.json").read_bytes() == original_file
    if legacy_arrows:
        warning.assert_called_once()
        assert "Removed 4" in warning.call_args.args[1]
        assert "saved layout file is unchanged" in warning.call_args.args[1]
    else:
        warning.assert_not_called()
