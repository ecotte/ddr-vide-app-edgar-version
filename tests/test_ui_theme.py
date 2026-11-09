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
