"""Tests for app.layout_store: saving/loading layout profiles and explicit
resolution-mismatch handling (never silently rescale)."""
from __future__ import annotations

import os
import sys

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
        label="P1 Left", player="P1", kind="arrow", x=0, y=0, w=40, h=40, direction="Left",
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
