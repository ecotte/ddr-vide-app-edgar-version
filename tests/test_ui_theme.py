"""Exercise the real Tk theme, widget states, and event table."""
from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ui.events_tab import COLUMNS, EventsTab
from app.ui.theme import ACCENT, BACKGROUND, ERROR, FIELD, MUTED, SURFACE, TEXT, apply_dark_theme
from app.ui.video_tab import VideoTab


@pytest.fixture(scope="module")
def root():
    window = tk.Tk()
    window.withdraw()
    apply_dark_theme(window)
    yield window
    window.destroy()


def test_theme_covers_classic_widgets_popups_and_ttk_states(root):
    style = ttk.Style(root)
    assert style.theme_use() == "clam"
    assert root.cget("background") == BACKGROUND
    assert tk.Toplevel(root).cget("background") == BACKGROUND
    assert tk.Canvas(root).cget("background") == SURFACE
    choices = tk.Listbox(root)
    assert choices.cget("background") == FIELD
    assert choices.cget("foreground") == TEXT
    assert choices.cget("selectforeground") == TEXT
    combo = ttk.Combobox(root, values=["P1", "P2"], state="readonly")
    popup = root.tk.call("ttk::combobox::PopdownWindow", combo)
    assert root.tk.call(f"{popup}.f.l", "cget", "-background") == FIELD
    for widget in ("TEntry", "TCombobox", "TSpinbox"):
        assert style.lookup(widget, "fieldbackground", ("readonly",)) == FIELD
        assert style.lookup(widget, "foreground", ("readonly",)) == TEXT
        assert style.lookup(widget, "bordercolor", ("focus",)) == ACCENT
    assert style.lookup("Accent.TButton", "background", ("disabled",)) != ACCENT
    assert style.lookup("Error.TLabel", "foreground") == ERROR
    assert style.lookup("Treeview", "foreground", ("selected",)) == TEXT


def _contrast(foreground: str, background: str) -> float:
    def luminance(color: str) -> float:
        channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                  for value in channels]
        return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_notebook_tabs_are_flat_and_selected_state_is_distinct_without_expanding(root):
    style = ttk.Style(root)
    assert style.layout("TNotebook.Tab")[0][0] == "FlatNotebook.tab"
    assert style.lookup("TNotebook.Tab", "relief", ("selected",)) == "flat"
    assert style.lookup("TNotebook.Tab", "foreground", ("selected",)) == ACCENT
    assert style.lookup("TNotebook.Tab", "foreground", ("selected", "active")) == ACCENT
    assert style.lookup("TNotebook.Tab", "foreground", ()) == MUTED
    for state in ((), ("selected",), ("active",), ("selected", "active")):
        assert str(style.lookup("TNotebook.Tab", "expand", state)) == "0 0 0 0"
        assert str(style.lookup("TNotebook.Tab", "padding", state)) == "18 9"
    normal, hover, selected = root._notebook_tab_images
    assert normal.get(0, 0) != selected.get(0, 0)
    assert hover.get(0, 0) != selected.get(0, 0)
    assert selected.get(0, 7) == (121, 170, 255)
    assert _contrast(ACCENT, "#253349") >= 4.5


@pytest.mark.parametrize("foreground,background", [
    (TEXT, SURFACE), (TEXT, FIELD), (MUTED, SURFACE), (MUTED, BACKGROUND),
    (ACCENT, SURFACE), (FIELD, ACCENT), (ERROR, SURFACE), (TEXT, "#284c7d"),
])
def test_text_colors_meet_normal_text_contrast(foreground, background):
    assert _contrast(foreground, background) >= 4.5


def test_video_empty_state_and_primary_actions(root):
    controller = Mock()
    tab = VideoTab(root, controller)
    assert len(tab.canvas.find_withtag("empty_state")) == 2
    tab.start_stop_button.invoke()
    controller.toggle_analysis.assert_called_once_with()
    tab.set_analysis_status(True)
    assert tab.start_stop_button.cget("text") == "Stop Analysis"
    assert tab.start_stop_button.cget("style") == "Accent.TButton"
    tab.set_play_button_label(True)
    assert tab.play_button.cget("text") == "Pause"


def test_hdmi_recording_controls_are_adjacent_and_track_record_stop_save_states(root):
    controller = Mock()
    tab = VideoTab(root, controller)
    try:
        assert tab.record_video_button.master is tab.connect_hdmi_button.master
        assert tab.record_video_button.grid_info()["row"] == tab.connect_hdmi_button.grid_info()["row"]
        assert tab.record_video_button.instate(["disabled"])
        tab.set_hdmi_connected(True)
        tab.record_video_button.invoke()
        controller.toggle_video_recording.assert_called_once_with()
        tab.set_recording_state("recording", "Recording locally")
        assert tab.record_video_button.cget("text") == "Stop recording"
        assert tab.recording_status_var.get() == "Recording locally"
        tab.set_play_button_label(False)
        assert tab.record_video_button.instate(["!disabled"])
        tab.set_recording_state("stopping")
        assert tab.record_video_button.instate(["disabled"])
        tab.set_recording_state("unsaved")
        assert tab.record_video_button.cget("text") == "Save recording..."
        tab.set_recording_state("saving")
        assert tab.record_video_button.instate(["disabled"])
        tab.set_recording_state("idle")
        tab.set_hdmi_connected(False)
        assert tab.record_video_button.instate(["disabled"])
    finally:
        tab.destroy()


def test_events_table_scrolling_and_publishing_states(root):
    root.deiconify()
    root.geometry("760x600")
    controller = Mock()
    tab = EventsTab(root, controller)
    tab.pack(fill="both", expand=True)
    for index in range(50):
        tab.append_event(SimpleNamespace(
            session_id="00000000-0000-4000-8000-000000000001",
            sequence=index, event_type="score_changed", player="P1",
            payload={"field": "score", "previous_value": index, "new_value": index + 1},
            video_time_ms=index * 100, observed_at_utc="2026-10-26T12:00:00Z",
        ))
    root.update()
    assert len(tab.tree.get_children()) == 50
    assert tab.count_var.get() == "50 events recorded"
    assert tuple(tab.tree["columns"]) == COLUMNS
    bars = [child for child in tab.tree.master.winfo_children() if isinstance(child, ttk.Scrollbar)]
    assert len(bars) == 2
    assert all(bar.winfo_ismapped() for bar in bars)
    assert tab.tree.yview()[0] > 0
    assert tab.tree.xview()[1] < 1
    tab.set_publishing_status("Connected", True)
    assert tab.test_button.instate(["!disabled"])
    tab.test_button.invoke()
    controller.send_mapping_sample.assert_called_once_with()
    tab.set_publishing_status("Off", False)
    assert tab.test_button.instate(["disabled"])
    tab.clear()
    assert not tab.tree.get_children()
    assert tab.count_var.get() == "0 events recorded"


def test_game_end_card_is_shared_and_capture_controls_are_wired(root):
    from app.models import Region

    controller = Mock()
    tab = VideoTab(root, controller)
    region = Region("Results", "Unknown", "game_end", 0, 0, 40, 30)
    tab.refresh_region_list([region])
    row = tab._region_rows["game_end"]
    assert row["kind"] == "game_end"
    parent = row["thumb_label"].master
    buttons = [child for child in parent.winfo_children() if isinstance(child, ttk.Button)]
    buttons[0].invoke()
    controller.capture_arrow_reference.assert_called_once_with(region, "ended")
    buttons[1].invoke()
    controller.clear_arrow_reference.assert_called_once_with(region, "ended")
    tab.update_arrow_recognition_row("game_end", None, "ended", 1.0, "active: ended")
    assert row["state_var"].get() == "state: ended"
    tab.set_arrow_reference_thumbnail("game_end", "ended", None, 0)
    assert row["ended_ref_label"].cget("text") == "(none)"
    tab.game_end_threshold_var.set(0.96)
    tab.game_end_confirm_var.set(3)
    tab._on_game_end_settings_changed()
    controller.set_game_end_settings.assert_called_once_with(0.96, 3)


def test_separate_threshold_controls_route_to_independent_analysis_settings(root):
    from app.analysis import AnalysisSession
    from app.event_bus import EventBus
    from app.ui.app_window import App

    controller = Mock()
    controller.analysis = AnalysisSession(EventBus())
    controller.set_arrow_similarity_threshold.side_effect = lambda value: App.set_arrow_similarity_threshold(controller, value)
    controller.set_judgment_similarity_threshold.side_effect = lambda value: App.set_judgment_similarity_threshold(controller, value)
    tab = VideoTab(root, controller)
    try:
        assert tab.arrow_threshold_var.get() == .55
        assert tab.judgment_threshold_var.get() == .65
        assert controller.analysis.judgment_similarity_threshold == .65
        tab.arrow_threshold_var.set(.95)
        tab._on_arrow_threshold_changed()
        tab.judgment_threshold_var.set(.65)
        tab._on_judgment_threshold_changed()
        assert controller.analysis.arrow_similarity_threshold == .95
        assert controller.analysis.judgment_similarity_threshold == .65
        controller.set_arrow_similarity_threshold.assert_called_once_with(.95)
        controller.set_judgment_similarity_threshold.assert_called_once_with(.65)
        for variable, handler, label in (
            (tab.arrow_threshold_var, tab._on_arrow_threshold_changed, "arrow"),
            (tab.judgment_threshold_var, tab._on_judgment_threshold_changed, "judgment"),
        ):
            for value in ("invalid", float("nan"), .49, 1.01):
                variable.set(value)
                handler()
                assert f"Invalid {label} similarity:" in tab.error_status_var.get()
        assert controller.analysis.arrow_similarity_threshold == .95
        assert controller.analysis.judgment_similarity_threshold == .65
        tab.judgment_threshold_var.set(.7)
        tab._on_judgment_threshold_changed()
        assert not tab.error_status_var.get()
        assert controller.analysis.arrow_similarity_threshold == .95
        assert controller.analysis.judgment_similarity_threshold == .7
    finally:
        tab.destroy()


def test_judgment_card_displays_its_own_diagnostics_without_modifying_other_player(root, monkeypatch):
    from app.models import Region

    controller = Mock()
    tab = VideoTab(root, controller)
    regions = [
        Region(player, player, "judgment", 0, 0, 100, 30) for player in ("P1", "P2")
    ]
    try:
        clock = [1.0]
        monkeypatch.setattr("app.ui.video_tab.time.perf_counter", lambda: clock[0])
        tab.refresh_region_list(regions)
        tab.set_judgment_diagnostics("P1:judgment", "Latch blocked PERFECT; no-match 33/50 ms.")
        tab._region_rows["P1:judgment"]["diagnostics_toggle"].invoke()
        assert tab._region_rows["P1:judgment"]["diagnostics_var"].get() == (
            "diagnostics: Latch blocked PERFECT; no-match 33/50 ms."
        )
        assert tab._region_rows["P2:judgment"]["diagnostics_var"].get() == (
            "diagnostics: Advance/play to inspect judgment matching."
        )
        clock[0] += .3
        tab.set_judgment_diagnostics("P1:judgment", None)
        assert tab._region_rows["P1:judgment"]["diagnostics_var"].get() == (
            "diagnostics: No frame diagnostics available."
        )
    finally:
        tab.destroy()


@pytest.mark.parametrize("kind", ["arrow", "judgment"])
def test_diagnostics_stay_cached_when_collapsed_and_refresh_at_four_hz_when_open(root, monkeypatch, kind):
    from app.models import Region

    tab = VideoTab(root, Mock())
    region = Region("Center", "P1", kind, 0, 0, 80, 80, direction="Center")
    clock = [1.0]
    monkeypatch.setattr("app.ui.video_tab.time.perf_counter", lambda: clock[0])
    try:
        tab.refresh_region_list([region])
        row = tab._region_rows[region.key()]
        setter = tab.set_arrow_diagnostics if kind == "arrow" else tab.set_judgment_diagnostics
        writes = []
        token = row["diagnostics_var"].trace_add("write", lambda *_: writes.append(True))
        assert not row["diagnostics_label"].winfo_manager()
        for i in range(100):
            setter(region.key(), f"Frame {i}")
        assert writes == []
        row["diagnostics_toggle"].invoke()
        assert row["diagnostics_var"].get() == "diagnostics: Frame 99"
        assert row["diagnostics_label"].winfo_manager() == "pack"
        assert len(writes) == 1
        clock[0] = 1.1
        setter(region.key(), "Frame 100")
        assert len(writes) == 1
        clock[0] = 1.25
        setter(region.key(), "Frame 101")
        assert row["diagnostics_var"].get() == "diagnostics: Frame 101"
        assert len(writes) == 2
        row["diagnostics_toggle"].invoke()
        assert not row["diagnostics_label"].winfo_manager()
        clock[0] = 2
        setter(region.key(), "Frame 102")
        assert len(writes) == 2
        row["diagnostics_toggle"].invoke()
        assert row["diagnostics_var"].get() == "diagnostics: Frame 102"
        assert len(writes) == 3
        row["diagnostics_var"].trace_remove("write", token)
    finally:
        tab.destroy()


def test_card_height_changes_do_not_reconfigure_label_wrapping():
    label = Mock(spec=ttk.Label)
    row = Mock()
    row.winfo_children.return_value = [label]
    VideoTab._wrap_card_labels(row)
    callback = row.bind.call_args.args[1]
    label.configure.reset_mock()
    callback(SimpleNamespace(width=300, height=100))
    label.configure.assert_called_once_with(wraplength=288)
    callback(SimpleNamespace(width=300, height=500))
    callback(SimpleNamespace(width=300, height=50))
    assert label.configure.call_count == 1
    callback(SimpleNamespace(width=400, height=50))
    assert label.configure.call_count == 2
    label.configure.assert_called_with(wraplength=388)


def test_unchanged_arrow_text_and_photo_do_not_trigger_tk_reconfiguration(root):
    from app.models import Region
    from PIL import Image, ImageTk
    from unittest.mock import patch

    tab = VideoTab(root, Mock())
    region = Region("Center", "P1", "arrow", 0, 0, 80, 80, direction="Center")
    try:
        tab.refresh_region_list([region])
        row = tab._region_rows[region.key()]
        photo = ImageTk.PhotoImage(Image.new("RGB", (20, 20)))
        tab.update_arrow_recognition_row(region.key(), photo, "active", .8, "activity")
        with patch.object(row["thumb_label"], "configure") as configure, \
                patch.object(row["state_var"], "set") as state, \
                patch.object(row["confidence_var"], "set") as confidence, \
                patch.object(row["status_var"], "set") as status:
            for _ in range(20):
                tab.update_arrow_recognition_row(region.key(), photo, "active", .8, "activity")
            configure.assert_not_called()
            state.assert_not_called()
            confidence.assert_not_called()
            status.assert_not_called()
            tab.update_arrow_recognition_row(region.key(), None, "idle", .1, "empty")
            state.assert_called_once_with("state: idle")
            status.assert_called_once_with("status: empty")
    finally:
        tab.destroy()


def test_arrow_and_judgment_confirmation_controls_are_independent(root):
    from app.analysis import AnalysisSession
    from app.event_bus import EventBus
    from app.ui.app_window import App

    controller = Mock()
    controller.analysis = AnalysisSession(EventBus())
    controller.set_arrow_min_confirm_frames.side_effect = lambda value: App.set_arrow_min_confirm_frames(controller, value)
    controller.set_judgment_min_confirm_frames.side_effect = lambda value: App.set_judgment_min_confirm_frames(controller, value)
    tab = VideoTab(root, controller)
    try:
        assert tab.arrow_confirm_var.get() == 1
        assert tab.judgment_confirm_var.get() == 2
        tab.judgment_confirm_var.set(3)
        tab._on_judgment_confirm_changed()
        assert controller.analysis.judgment_min_confirm_frames == 3
        assert controller.analysis.arrow_min_confirm_frames == 1
        tab.arrow_confirm_var.set(2)
        tab._on_arrow_confirm_changed()
        assert controller.analysis.arrow_min_confirm_frames == 2
        assert controller.analysis.judgment_min_confirm_frames == 3
        for value in ("invalid", 0, 11):
            tab.judgment_confirm_var.set(value)
            tab._on_judgment_confirm_changed()
            assert "Invalid judgment confirmation" in tab.error_status_var.get()
        assert controller.analysis.judgment_min_confirm_frames == 3
        tab.judgment_confirm_var.set(2)
        tab._on_judgment_confirm_changed()
        assert not tab.error_status_var.get()
    finally:
        tab.destroy()


def test_no_judgment_capture_gallery_and_deletion_refresh_real_player_cards(root):
    import numpy as np
    from app import image_utils
    from app.analysis import AnalysisSession
    from app.event_bus import EventBus
    from app.models import Region
    from app.ui.app_window import App
    from app.ui.video_tab import ReferenceGalleryDialog

    word = image_utils.encode_crop_to_base64_png(np.full((20, 20, 3), 255, dtype=np.uint8))
    regions = [
        Region(player, player, "judgment", index * 20, 0, 20, 20,
               reference_images={"perfect": [word]})
        for index, player in enumerate(("P1", "P2"))
    ]
    controller = Mock()
    controller.regions = regions
    controller.analysis = AnalysisSession(EventBus())
    controller.analysis.set_regions(regions)
    controller._analysis_generation = 0
    controller._last_frame_bgr = np.full((20, 40, 3), 128, dtype=np.uint8)
    controller._make_thumbnail_photo = App._make_thumbnail_photo
    controller._refresh_arrow_reference_thumbnails = lambda: App._refresh_arrow_reference_thumbnails(controller)
    controller.capture_arrow_reference = lambda region, state: App.capture_arrow_reference(controller, region, state)
    controller.clear_arrow_reference = lambda region, state: App.clear_arrow_reference(controller, region, state)
    controller.delete_arrow_reference = lambda region, state, index: App.delete_arrow_reference(controller, region, state, index)
    tab = VideoTab(root, controller)
    controller.video_tab = tab
    tab.refresh_region_list(regions)
    gallery = None
    try:
        for region in regions:
            card = tab._region_rows[region.key()]["thumb_label"].master
            buttons = {
                widget["text"]: widget for group in card.winfo_children() if isinstance(group, ttk.Frame)
                for widget in group.winfo_children() if isinstance(widget, ttk.Button)
            }
            buttons["Add No judgment ref"].invoke()
            buttons["Add No judgment ref"].invoke()
            assert len(region.reference_images["empty"]) == 2
            assert tab._region_rows[region.key()]["empty_ref_label"]["image"]
            assert tab._region_rows[region.key()]["empty_ref_label"]["text"] == "2 refs"
            for title, state in (
                ("Perfect", "perfect"), ("Great", "great"), ("Good", "good"),
                ("Miss", "miss"), ("No judgment", "empty"),
            ):
                controller.show_arrow_reference_gallery.reset_mock()
                buttons[f"View / delete {title} refs..."].invoke()
                controller.show_arrow_reference_gallery.assert_called_once_with(region, state)
        gallery = ReferenceGalleryDialog(root, controller, regions[1], "empty")
        assert "No judgment" in gallery.title()
        gallery._delete_reference(0)
        assert tab._region_rows[regions[1].key()]["empty_ref_label"]["text"] == "1 ref"
        assert len(regions[0].reference_images["empty"]) == 2
        gallery._delete_reference(0)
        assert tab._region_rows[regions[1].key()]["empty_ref_label"]["text"] == "(none)"
        assert regions[1].reference_images["perfect"] == [word]
        # A layout refresh restores the remaining negative preview for P1 only.
        tab.refresh_region_list(regions)
        controller._refresh_arrow_reference_thumbnails()
        assert tab._region_rows[regions[0].key()]["empty_ref_label"]["text"] == "2 refs"
        assert tab._region_rows[regions[1].key()]["empty_ref_label"]["text"] == "(none)"
    finally:
        if gallery is not None:
            gallery.destroy()
        tab.destroy()


def test_game_end_region_is_game_wide_and_cannot_be_added_twice(root):
    from app.ui.region_dialog import RegionDialog

    root.deiconify()
    root.update()
    dialog = RegionDialog(root, existing_keys={"game_end"})
    try:
        dialog.kind_var.set("game_end")
        dialog._on_kind_changed()
        assert dialog.player_var.get() == "Unknown"
        assert dialog.player_combo.instate(["disabled"])
        assert dialog.direction_combo.instate(["disabled"])
        dialog._on_confirm()
        assert dialog.result is None
        assert "already exists" in dialog.error_var.get()
    finally:
        dialog.destroy()


@pytest.mark.parametrize("x,y", [(320, 180), (4500, 100), (-1000, 120)])
def test_region_dialog_opens_over_its_parent_before_grabbing_input(root, x, y):
    from app.ui.region_dialog import RegionDialog

    parent = tk.Toplevel(root)
    parent.geometry(f"700x500+{x}+{y}")
    parent.update()
    dialog = RegionDialog(parent)
    try:
        expected_x = parent.winfo_rootx() + (parent.winfo_width() - dialog.winfo_reqwidth()) // 2
        expected_y = parent.winfo_rooty() + (parent.winfo_height() - dialog.winfo_reqheight()) // 2
        assert abs(dialog.winfo_rootx() - expected_x) <= 16
        assert abs(dialog.winfo_rooty() - expected_y) <= 40
        assert dialog.winfo_viewable()
        assert dialog.grab_current() is dialog
        dialog._on_cancel()
        assert parent.grab_current() is None
    finally:
        if dialog.winfo_exists():
            dialog.destroy()
        parent.destroy()


@pytest.mark.parametrize("direction", ["Up-Right", "Up-Left", "Down-Right", "Down-Left", "Center"])
@pytest.mark.parametrize("player", ["P1", "P2"])
def test_arrow_dialog_offers_and_accepts_all_five_directions(root, direction, player):
    from app.ui.region_dialog import RegionDialog

    root.deiconify()
    root.update()
    dialog = RegionDialog(root)
    try:
        dialog.kind_var.set("arrow")
        dialog.player_var.set(player)
        dialog._on_kind_changed()
        assert tuple(dialog.direction_combo["values"]) == (
            "Up-Right", "Up-Left", "Down-Right", "Down-Left", "Center",
        )
        assert dialog.direction_combo.instate(["readonly"])
        dialog.direction_var.set(direction)
        dialog._on_confirm()
        assert dialog.result == (f"{player} {direction}", player, "arrow", direction)
    finally:
        if dialog.winfo_exists():
            dialog.destroy()


def test_center_arrow_is_unique_per_player(root):
    from app.ui.region_dialog import RegionDialog

    root.deiconify()
    root.update()
    dialog = RegionDialog(root, existing_keys={"P1:arrow:Center"})
    try:
        dialog.kind_var.set("arrow")
        dialog.direction_var.set("Center")
        dialog._on_kind_changed()
        dialog._on_confirm()
        assert dialog.result is None
        assert "already exists" in dialog.error_var.get()
        dialog.player_var.set("P2")
        dialog._on_confirm()
        assert dialog.result == ("P2 Center", "P2", "arrow", "Center")
    finally:
        if dialog.winfo_exists():
            dialog.destroy()


@pytest.mark.parametrize("kind", ["score", "combo", "judgment", "game_end"])
@pytest.mark.parametrize("player", ["P1", "P2"])
def test_region_dialog_generates_names_without_a_label_input(root, kind, player):
    from app.models import Region
    from app.ui.region_dialog import RegionDialog

    root.deiconify()
    root.update()
    dialog = RegionDialog(root)
    try:
        assert dialog.title() == "Add region"
        form, = dialog.winfo_children()
        assert all(widget.winfo_class() != "TEntry" for widget in form.winfo_children())
        assert all(
            widget.cget("text") != "Label:"
            for widget in form.winfo_children() if widget.winfo_class() == "TLabel"
        )
        dialog.player_var.set(player)
        dialog.kind_var.set(kind)
        dialog._on_kind_changed()
        dialog._on_confirm()
        expected_player = "Unknown" if kind == "game_end" else player
        expected_label = "Game end" if kind == "game_end" else f"{player} {kind.capitalize()}"
        assert dialog.result == (expected_label, expected_player, kind, None)
        region = Region(expected_label, expected_player, kind, 0, 0, 20, 20)
        assert Region.from_dict(region.to_dict()).label == expected_label
    finally:
        if dialog.winfo_exists():
            dialog.destroy()


def test_judgment_still_requires_a_known_player_without_a_label_field(root):
    from app.ui.region_dialog import RegionDialog

    root.deiconify()
    root.update()
    dialog = RegionDialog(root)
    try:
        dialog.player_var.set("Unknown")
        dialog.kind_var.set("judgment")
        dialog._on_kind_changed()
        dialog._on_confirm()
        assert dialog.result is None
        assert "Choose P1 or P2" in dialog.error_var.get()
    finally:
        dialog.destroy()


def test_source_picker_names_devices_and_disables_replay_controls_for_hdmi(root):
    controller = Mock()
    tab = VideoTab(root, controller)
    tab.set_source_selection("HDMI input")
    tab.set_live_mode(True)
    tab.set_capture_devices(["Built-in camera (device 0)", "Hagibis (device 1)"])
    assert tab.capture_device_var.get() == ""
    assert tab.connect_hdmi_button.instate(["disabled"])
    tab.capture_device_var.set("Hagibis (device 1)")
    tab._update_capture_controls()
    tab.connect_hdmi_button.invoke()
    controller.toggle_hdmi.assert_called_once_with()
    assert tab.hdmi_controls.winfo_manager() == "pack"
    assert tab.file_controls.winfo_manager() == ""
    assert tab.step_back_button.instate(["disabled"])
    assert tab.step_forward_button.instate(["disabled"])
    assert tab.seek_scale.instate(["disabled"])
    tab.set_frame_position(100, 0, 3100)
    assert tab.frame_pos_var.get() == "LIVE | Frame 100 | 3100 ms"
    tab.set_play_button_label(True)
    assert tab.play_button.cget("text") == "Freeze preview"
    tab.set_play_button_label(False)
    assert tab.play_button.cget("text") == "Resume live"
    tab.set_capture_pending(True)
    assert tab.source_combo.instate(["disabled"])
    assert tab.connect_hdmi_button.cget("text") == "Cancel HDMI"
    tab.set_capture_pending(False)
    tab.set_hdmi_connected(True)
    assert tab.connect_hdmi_button.cget("text") == "Disconnect HDMI"
    tab.set_hdmi_connected(False)
    tab.set_source_selection("Video file")
    tab.set_live_mode(False)
    assert tab.step_back_button.instate(["!disabled"])
    assert tab.seek_scale.instate(["!disabled"])
    assert tab.file_controls.winfo_manager() == "pack"
    assert tab.hdmi_controls.winfo_manager() == ""
