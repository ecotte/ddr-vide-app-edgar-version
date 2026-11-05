"""Tests for aspect-ratio-preserving video display fitting."""
from __future__ import annotations

import os
import sys
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Region
from app.ui.app_window import App
from app.ui.theme import apply_dark_theme
from app.ui.video_tab import VideoTab, fit_display_size, next_playback_delay_ms


@pytest.fixture(scope="module")
def tk_root():
    root = tk.Tk()
    root.withdraw()
    apply_dark_theme(root)
    yield root
    root.destroy()


@pytest.fixture
def display(tk_root):
    view = SimpleNamespace(
        canvas=tk.Canvas(tk_root), display_scale=1.0, _photo_image=None,
        _frame_image_id=None, _frame_image_size=None,
        _overlay_signature=None, _overlay_ids=[],
    )
    try:
        yield tk_root, view
    finally:
        view.canvas.destroy()
        view._photo_image = None


def test_video_image_reuses_objects_but_updates_pixels_and_handles_resize(display):
    root, view = display
    VideoTab.show_frame_image(view, Image.new("RGB", (40, 30), "red"), 1.0)
    original = view._photo_image
    item = view._frame_image_id
    overlay = view.canvas.create_rectangle(1, 1, 20, 20)
    for _ in range(20):
        VideoTab.show_frame_image(view, Image.new("RGB", (40, 30), "blue"), 1.0)
    assert view._photo_image is original
    assert view._frame_image_id == item
    assert root.tk.call(str(original), "get", 10, 10) == (0, 0, 255)
    assert view.canvas.find_all() == (item, overlay)

    VideoTab.show_frame_image(view, Image.new("RGB", (20, 15), "red"), 0.5)
    assert view._photo_image is not original
    assert view._frame_image_id == item
    assert view.canvas.find_all() == (item, overlay)
    assert view.display_scale == 0.5
    assert (int(view.canvas["width"]), int(view.canvas["height"])) == (20, 15)
    assert root.tk.call(str(view._photo_image), "get", 10, 10) == (255, 0, 0)


def test_overlays_reuse_unchanged_regions_and_refresh_after_edits(display):
    _, view = display
    region = Region("Combo", "P1", "combo", 10, 20, 40, 30)
    VideoTab.redraw_region_overlays(view, [region])
    original = tuple(view._overlay_ids)
    selection = view.canvas.create_rectangle(0, 0, 5, 5)
    for _ in range(20):
        VideoTab.redraw_region_overlays(view, [region])
    assert tuple(view._overlay_ids) == original
    assert len(view.canvas.find_all()) == 3

    region.x = 15
    VideoTab.redraw_region_overlays(view, [region])
    assert tuple(view._overlay_ids) != original
    assert view.canvas.coords(view._overlay_ids[0]) == [15, 20, 55, 50]
    view.display_scale = 0.5
    VideoTab.redraw_region_overlays(view, [region])
    assert view.canvas.coords(view._overlay_ids[0]) == [7.5, 10, 27.5, 25]
    region.player = "P2"
    VideoTab.redraw_region_overlays(view, [region])
    assert view.canvas.itemcget(view._overlay_ids[1], "text") == "P2:combo"
    VideoTab.redraw_region_overlays(view, [])
    assert view.canvas.find_all() == (selection,)
    VideoTab.redraw_region_overlays(view, [region])
    assert len(view._overlay_ids) == 2


def test_recognition_thumbnail_reuses_image_without_stale_pixels(display):
    root, _ = display
    crop = np.full((30, 150, 3), (0, 0, 255), dtype=np.uint8)
    photo = App._make_thumbnail_photo(crop)
    crop[:] = (255, 0, 0)
    updated = App._make_thumbnail_photo(crop, photo)
    assert updated is photo
    assert root.tk.call(str(photo), "get", 10, 10) == (0, 0, 255)
    resized = App._make_thumbnail_photo(crop[:20], photo)
    assert resized is not photo
    assert (resized.width(), resized.height()) == (150, 20)
    assert App._make_thumbnail_photo(None, photo) is None
    assert App._make_thumbnail_photo(np.zeros((0, 0, 3), dtype=np.uint8), photo) is None


@pytest.mark.parametrize("width,height", [(374, 57), (392, 180), (80, 200), (40, 20)])
def test_compact_thumbnails_fit_card_and_keep_aspect_ratio(display, width, height):
    crop = np.zeros((height, width, 3), dtype=np.uint8)
    photo = App._make_thumbnail_photo(crop)
    assert photo.width() <= 240
    assert photo.height() <= 96
    scale = photo.width() / width
    assert abs(photo.height() - height * scale) <= 2
    ref = App._make_thumbnail_photo(crop, max_width=96, max_height=64)
    assert ref.width() <= 96
    assert ref.height() <= 64


@pytest.mark.parametrize("window_width", [1200, 1500])
def test_two_player_columns_fit_and_route_controls_independently(window_width, tk_root):
    from tkinter import ttk

    root = tk.Toplevel(tk_root)
    root.geometry(f"{window_width}x900")
    controller = Mock()
    tab = VideoTab(root, controller)
    tab.pack(fill="both", expand=True)
    regions = [
        Region("Score", player, "score", 0, 0, 374, 57)
        for player in ("P1", "P2")
    ] + [
        Region("Combo", player, "combo", 0, 0, 392, 180)
        for player in ("P1", "P2")
    ] + [
        Region(direction, player, "arrow", 0, 0, 100, 100, direction=direction)
        for player in ("P1", "P2")
        for direction in ("Up-Right", "Up-Left", "Down-Right", "Down-Left", "Center")
    ] + [Region("Unassigned score", "Unknown", "score", 0, 0, 100, 30)]
    try:
        tab.refresh_region_list(regions)
        for region in regions:
            photo = App._make_thumbnail_photo(np.zeros((region.h, region.w, 3), dtype=np.uint8))
            if region.kind == "arrow":
                tab.update_arrow_recognition_row(region.key(), photo, "hit", 0.99, "active: hit")
                for state in ("hit", "miss", "appeared"):
                    ref = App._make_thumbnail_photo(
                        np.zeros((30, 374, 3), dtype=np.uint8), max_width=96, max_height=64,
                    )
                    tab.set_arrow_reference_thumbnail(region.key(), state, ref, 12)
            else:
                tab.update_recognition_row(
                    region.key(), photo, "454279", 454279, 479820,
                    "Waiting for repeated evidence to lower score from 479820 to 454279 (1/2).",
                    frame_index=123, video_time_ms=2050,
                )
                assert tab._region_rows[region.key()]["source_var"].get() == "OCR frame: 123 | 2050 ms"
        root.update()
        headers = [w for w in tab.recognition_container.winfo_children() if isinstance(w, ttk.Label)]
        unknown, = [w for w in tab.recognition_container.winfo_children() if isinstance(w, ttk.LabelFrame)]
        assert [header["text"] for header in headers] == ["Player 1", "Player 2"]
        assert headers[0].grid_info()["column"] == 0
        assert headers[1].grid_info()["column"] == 1
        assert unknown["text"] == "Unknown player"
        assert unknown.grid_info()["columnspan"] == 2
        for region in regions:
            row_data = tab._region_rows[region.key()]
            card = row_data["thumb_label"].master
            expected_parent = unknown if region.player == "Unknown" else tab.recognition_container
            assert card.master is expected_parent
            if region.player in ("P1", "P2"):
                assert card.grid_info()["column"] == ("P1", "P2").index(region.player)
                assert card.winfo_x() + card.winfo_width() <= tab.recognition_container.winfo_width()
                if region.player == "P1":
                    counterpart = tab._region_rows[region.key().replace("P1:", "P2:", 1)]["thumb_label"].master
                    assert card.winfo_y() == counterpart.winfo_y()
                    assert card.winfo_height() == counterpart.winfo_height()
                    assert abs(card.winfo_width() - counterpart.winfo_width()) <= 1
            assert row_data["thumb_label"].grid_info()["row"] == 1
            for child in card.winfo_children():
                assert child.winfo_x() + child.winfo_width() <= card.winfo_width()
            if region.kind == "score":
                button, = [w for w in card.winfo_children() if isinstance(w, ttk.Button)]
                button.invoke()
                controller.teach_score.assert_called_with(region)
            elif region.kind == "combo":
                teach, = [w for w in card.winfo_children() if isinstance(w, ttk.Button)]
                assert teach["text"] == "Teach combo..."
                teach.invoke()
                controller.teach_score.assert_called_with(region)
                checkbox, = [
                    w for w in card.winfo_children()
                    if isinstance(w, ttk.Checkbutton) and w["text"] == "Yellow-digit filter"
                ]
                checkbox.invoke()
                controller.set_combo_yellow_filter.assert_called_with(region, True)
                fast, = [
                    w for w in card.winfo_children()
                    if isinstance(w, ttk.Checkbutton) and w["text"] == "Fast live combo"
                ]
                fast.invoke()
                controller.set_combo_fast_mode.assert_called_with(region, True)
                assert "(unconfirmed)" in row_data["candidate_var"].get()
            else:
                buttons = [
                    button for frame in card.winfo_children() if isinstance(frame, ttk.Frame)
                    for button in frame.winfo_children() if isinstance(button, ttk.Button)
                ]
                assert len(buttons) == 6
                buttons[2].invoke()
                controller.capture_arrow_reference.assert_called_with(region, "miss")
                buttons[3].invoke()
                controller.clear_arrow_reference.assert_called_with(region, "miss")
                buttons[4].invoke()
                controller.capture_arrow_reference.assert_called_with(region, "appeared")
                buttons[5].invoke()
                controller.clear_arrow_reference.assert_called_with(region, "appeared")
        tab.refresh_region_list([regions[0]])
        root.update()
        assert len(tab.recognition_container.winfo_children()) == 3
        assert list(tab._region_rows) == ["P1:score"]
    finally:
        root.destroy()


def test_matching_regions_stay_aligned_with_missing_regions_shuffled_order_and_unequal_content(tk_root):
    root = tk.Toplevel(tk_root)
    root.geometry("1200x900")
    tab = VideoTab(root, Mock())
    tab.pack(fill="both", expand=True)
    regions = [
        Region("P2 left", "P2", "arrow", 0, 0, 100, 100, direction="Up-Left"),
        Region("P1 score", "P1", "score", 0, 0, 374, 57),
        Region("P2 right", "P2", "arrow", 0, 0, 100, 100, direction="Up-Right"),
        Region("P1 combo", "P1", "combo", 0, 0, 392, 180),
        Region("P1 right", "P1", "arrow", 0, 0, 100, 100, direction="Up-Right"),
        Region("P2 score", "P2", "score", 0, 0, 374, 57),
        Region("P1 left", "P1", "arrow", 0, 0, 100, 100, direction="Up-Left"),
        Region("Game over", "Unknown", "game_end", 0, 0, 100, 100),
    ]
    try:
        tab.refresh_region_list(regions)
        tab._region_rows["P1:score"]["status_var"].set("Long status\n" * 8)
        photo = App._make_thumbnail_photo(np.zeros((96, 100, 3), dtype=np.uint8))
        tab.set_arrow_reference_thumbnail("P2:arrow:Up-Right", "appeared", photo, 7)
        root.update()
        cards = {key: data["thumb_label"].master for key, data in tab._region_rows.items()}
        for suffix in ("score", "arrow:Up-Right", "arrow:Up-Left"):
            p1, p2 = cards[f"P1:{suffix}"], cards[f"P2:{suffix}"]
            assert p1.grid_info()["row"] == p2.grid_info()["row"]
            assert p1.winfo_y() == p2.winfo_y()
            assert p1.winfo_height() == p2.winfo_height()
        assert cards["P1:combo"].winfo_y() < cards["P2:arrow:Up-Right"].winfo_y()
        assert cards["P2:arrow:Up-Right"].winfo_y() < cards["P2:arrow:Up-Left"].winfo_y()
        assert cards["game_end"].master.grid_info()["columnspan"] == 2
        assert cards["game_end"].master.winfo_y() > cards["P1:arrow:Up-Left"].winfo_y()
        tab.refresh_region_list([regions[2], regions[4]])
        root.update()
        assert set(tab._region_rows) == {"P1:arrow:Up-Right", "P2:arrow:Up-Right"}
        assert len(tab.recognition_container.winfo_children()) == 4
    finally:
        root.destroy()


@pytest.mark.parametrize(
    ("source_width", "source_height", "max_width", "max_height"),
    [
        (1920, 1080, 700, 540),
        (1080, 1920, 700, 540),
        (1024, 768, 700, 540),
        (640, 480, 700, 540),
    ],
)
def test_fit_display_size_never_crops_or_upscales(
    source_width, source_height, max_width, max_height
):
    width, height, scale = fit_display_size(
        source_width, source_height, max_width, max_height
    )

    assert width <= max_width
    assert height <= max_height
    assert scale <= 1.0
    assert width == round(source_width * scale)
    assert height == round(source_height * scale)


def test_fit_display_size_uses_uniform_scale_for_wide_video():
    width, height, scale = fit_display_size(1920, 1080, 700, 540)

    assert (width, height) == (700, 394)
    assert scale == pytest.approx(700 / 1920)


def test_playback_delay_subtracts_time_already_spent_processing():
    assert next_playback_delay_ms(60.0, processing_seconds=0.010) == 7


def test_playback_delay_never_becomes_zero_when_processing_exceeds_interval():
    assert next_playback_delay_ms(60.0, processing_seconds=0.030) == 1
