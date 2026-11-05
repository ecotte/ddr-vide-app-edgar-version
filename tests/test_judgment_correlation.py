"""Player judgments validate arrow activity; ambiguous directions are never guessed."""
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from app import config, image_utils, layout_store
from app.analysis import AnalysisSession
from app.event_bus import EventBus
from app.models import LayoutProfile, Region
from app.recognition import classify_judgment_state
from app.ui.app_window import App
from app.ui.video_tab import VideoTab


COLORS = {"hit": 255, "miss": 0, "idle": 128, "appeared": 64}


def image(state):
    return np.full((20, 20, 3), COLORS[state], dtype=np.uint8)


def setup(players=("P1",), directions=("Up-Right",), judgments=("P1",)):
    regions = []
    for player in players:
        for direction in directions:
            regions.append(Region(
                direction, player, "arrow", len(regions) * 20, 0, 20, 20,
                direction=direction,
                reference_images={
                    state: [image_utils.encode_crop_to_base64_png(image(state))]
                    for state in ("hit", "miss", "appeared")
                },
            ))
        if player in judgments:
            regions.append(Region(
                "Judgment", player, "judgment", len(regions) * 20, 0, 20, 20,
                reference_images={
                    state: [image_utils.encode_crop_to_base64_png(image(state))]
                    for state in ("hit", "miss", "idle")
                },
            ))
    bus = EventBus()
    session = AnalysisSession(bus)
    session.arrow_similarity_threshold = 0.99
    session.set_regions(regions)
    session.start()
    index = 0

    def frame(timestamp, **states):
        nonlocal index
        pixels = np.full((20, 20 * len(regions), 3), 128, dtype=np.uint8)
        for region in regions:
            pixels[:, region.x:region.x + 20] = image(states.get(region.key(), "idle"))
        index += 1
        return session.process_arrow_frame(pixels, index, timestamp)

    return session, bus, regions, frame


def outcomes(bus):
    return [event for event in bus.events if event.event_type in (
        "arrow_hit", "arrow_miss", "judgment_hit", "judgment_miss",
    )]


@pytest.mark.parametrize("outcome", ["hit", "miss"])
@pytest.mark.parametrize("offset", [-151, -150, 0, 150, 151])
def test_correlates_only_within_source_time_window_and_uses_judgment_outcome(outcome, offset):
    session, bus, _, frame = setup()
    arrow = {"P1:arrow:Up-Right": "miss" if outcome == "hit" else "hit"}
    judgment = {"P1:judgment": outcome}
    if offset < 0:
        frame(200 + offset, **arrow)
        frame(200, **judgment)
    elif offset == 0:
        frame(200, **arrow, **judgment)
    else:
        frame(200, **judgment)
        frame(200 + offset, **arrow)
    assert outcomes(bus) == []
    frame(499)
    assert outcomes(bus) == []
    resolved = frame(500)
    event, = outcomes(bus)
    known = abs(offset) <= config.JUDGMENT_CORRELATION_WINDOW_MS
    assert event.event_type == f"{'arrow' if known else 'judgment'}_{outcome}"
    assert event.payload["direction"] == ("Up-Right" if known else None)
    assert event.payload["state"] == outcome
    assert event.payload["judgment_video_time_ms"] == 200
    assert event in resolved.new_events
    assert session._pending_judgments == []


def test_player_judgments_never_validate_other_player_arrows_and_legacy_players_still_work():
    _, bus, _, frame = setup(players=("P1", "P2"), judgments=("P1",))
    frame(100, **{"P1:judgment": "miss", "P2:arrow:Up-Right": "hit"})
    assert [event.event_type for event in outcomes(bus)] == ["arrow_hit"]
    frame(400)
    p2, p1 = outcomes(bus)
    assert (p2.player, p2.event_type) == ("P2", "arrow_hit")
    assert (p1.player, p1.event_type, p1.payload["direction"]) == ("P1", "judgment_miss", None)


def test_arrow_without_new_judgment_does_not_count_with_correlation_enabled():
    _, bus, _, frame = setup()
    frame(0, **{"P1:arrow:Up-Right": "hit"})
    frame(1000)
    assert outcomes(bus) == []


def test_persistent_judgment_counts_once_and_cannot_validate_later_arrow_flashes():
    _, bus, _, frame = setup()
    frame(0, **{"P1:judgment": "hit"})
    frame(300, **{"P1:judgment": "hit"})
    frame(500, **{"P1:judgment": "hit", "P1:arrow:Up-Right": "hit"})
    frame(900, **{"P1:judgment": "hit"})
    assert len(outcomes(bus)) == 1
    assert outcomes(bus)[0].event_type == "judgment_hit"
    frame(1000)
    frame(1100, **{"P1:judgment": "hit", "P1:arrow:Up-Right": "hit"})
    frame(1400)
    assert len(outcomes(bus)) == 2
    assert outcomes(bus)[1].event_type == "arrow_hit"


def test_simultaneous_arrows_count_one_player_outcome_without_inventing_a_direction():
    _, bus, _, frame = setup(directions=("Up-Right", "Center"))
    frame(100, **{"P1:judgment": "hit", "P1:arrow:Up-Right": "hit", "P1:arrow:Center": "hit"})
    frame(400)
    event, = outcomes(bus)
    assert event.event_type == "judgment_hit"
    assert event.payload["direction"] is None


def test_two_judgments_cannot_both_claim_one_arrow():
    _, bus, _, frame = setup()
    frame(100, **{"P1:judgment": "hit", "P1:arrow:Up-Right": "hit"})
    frame(150)
    frame(200, **{"P1:judgment": "miss"})
    frame(500)
    assert [event.event_type for event in outcomes(bus)] == ["judgment_hit", "judgment_miss"]
    assert all(event.payload["direction"] is None for event in outcomes(bus))


def test_direct_success_to_miss_transition_counts_distinct_outcomes():
    _, bus, _, frame = setup()
    frame(0, **{"P1:judgment": "hit"})
    frame(100, **{"P1:judgment": "miss"})
    frame(500)
    assert [event.event_type for event in outcomes(bus)] == ["judgment_hit", "judgment_miss"]


def test_recording_toggle_does_not_recount_a_still_visible_judgment():
    session, bus, _, frame = setup()
    frame(0, **{"P1:judgment": "hit"})
    frame(300, **{"P1:judgment": "hit"})
    session.set_recording_enabled(False)
    frame(400, **{"P1:judgment": "hit"})
    session.set_recording_enabled(True)
    frame(500, **{"P1:judgment": "hit"})
    frame(1000, **{"P1:judgment": "hit"})
    assert len(outcomes(bus)) == 1


def test_confirmation_samples_use_onset_time_not_confirmation_time():
    session, bus, _, frame = setup()
    session.set_arrow_min_confirm_frames(2)
    both = {"P1:judgment": "hit", "P1:arrow:Up-Right": "hit"}
    frame(100, **both)
    assert outcomes(bus) == []
    frame(133, **both)
    frame(400)
    event, = outcomes(bus)
    assert event.event_type == "arrow_hit"
    assert event.payload["judgment_video_time_ms"] == 100


@pytest.mark.parametrize("action", ["seek", "stop", "recording", "new_session", "recalibrate"])
def test_lifecycle_changes_drop_pending_correlations(action):
    session, bus, regions, frame = setup()
    frame(100, **{"P1:judgment": "hit", "P1:arrow:Up-Right": "hit"})
    if action == "seek":
        session.reset_tracking()
    elif action == "stop":
        session.stop()
        session.start()
    elif action == "recording":
        session.set_recording_enabled(False)
        frame(150, **{"P1:judgment": "miss"})
        session.set_recording_enabled(True)
    elif action == "new_session":
        session.new_session()
    else:
        session.set_regions(regions)
    frame(1000)
    assert outcomes(bus) == []


def test_backward_frames_and_repeated_frames_do_not_duplicate_or_carry_pending_outcomes():
    session, bus, _, frame = setup()
    frame(100, **{"P1:judgment": "hit"})
    frame(0)
    frame(400)
    assert outcomes(bus) == []
    pixels = np.concatenate((image("hit"), image("hit")), axis=1)
    session.process_arrow_frame(pixels, 10, 500)
    session.process_arrow_frame(pixels, 10, 500)
    session.process_arrow_frame(np.concatenate((image("idle"), image("idle")), axis=1), 11, 1000)
    assert len(outcomes(bus)) == 1


def test_timing_is_only_carried_for_a_unique_successful_lane():
    _, bus, _, frame = setup()
    frame(0, **{"P1:arrow:Up-Right": "appeared"})
    frame(100, **{"P1:arrow:Up-Right": "hit", "P1:judgment": "hit"})
    frame(400)
    event, = outcomes(bus)
    assert event.payload["response_time_ms"] == 100
    assert event.payload["hit_video_time_ms"] == 100
    assert event.video_time_ms == 400


def test_uncalibrated_judgment_region_blocks_arrow_only_outcomes_and_explains_why():
    session, bus, regions, frame = setup()
    regions[-1].reference_images = {"hit": regions[-1].reference_images["hit"]}
    session.set_regions(regions)
    result = frame(0, **{"P1:arrow:Up-Right": "hit", "P1:judgment": "hit"})
    assert "success and MISS" in result.region_results[-1].error
    frame(500)
    assert outcomes(bus) == []


def test_judgment_similarity_rejects_ties_and_respects_blank_examples():
    assert classify_judgment_state(image("hit"), {"hit": [image("hit")], "miss": [image("hit")]}, .9)[0] is None
    assert classify_judgment_state(image("idle"), {
        "hit": [image("hit")], "miss": [image("miss")], "idle": [image("idle")],
    }, .9)[0] == "idle"


def test_unrecognized_judgment_frame_does_not_rearm_a_lingering_word():
    session, bus, regions, frame = setup()
    regions[-1].reference_images.pop("idle")
    session.set_regions(regions)
    frame(0, **{"P1:judgment": "hit"})
    frame(300, **{"P1:judgment": "hit"})
    frame(400)
    frame(500, **{"P1:judgment": "hit"})
    frame(900)
    assert len(outcomes(bus)) == 1


def test_judgment_layout_references_round_trip_and_rescale(tmp_path):
    _, _, regions, _ = setup()
    path = str(tmp_path / "judgment.json")
    layout_store.save_layout_to_path(LayoutProfile("judgments", 80, 40, regions), path)
    loaded = layout_store.load_layout_from_path(path)
    assert loaded.regions[-1].to_dict() == regions[-1].to_dict()
    scaled = layout_store.rescale_profile(loaded, 160, 80)
    assert image_utils.decode_base64_png_to_crop(scaled.regions[-1].reference_images["idle"][0]).shape == (40, 40, 3)


def test_judgment_cards_align_and_route_all_calibration_actions():
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.withdraw()
    controller = Mock()
    tab = VideoTab(root, controller)
    _, _, regions, _ = setup(players=("P1", "P2"), judgments=("P1", "P2"))
    try:
        tab.refresh_region_list(regions)
        for player in ("P1", "P2"):
            region = next(region for region in regions if region.key() == f"{player}:judgment")
            data = tab._region_rows[region.key()]
            card = data["thumb_label"].master
            assert card.grid_info()["column"] == (0 if player == "P1" else 1)
            for name, title in (("hit", "Success"), ("miss", "MISS"), ("idle", "Blank")):
                button, = [
                    widget for group in card.winfo_children() if isinstance(group, ttk.Frame)
                    for widget in group.winfo_children()
                    if isinstance(widget, ttk.Button) and widget["text"] == f"Add {title} ref"
                ]
                button.invoke()
                controller.capture_arrow_reference.assert_called_with(region, name)
                tab.set_arrow_reference_thumbnail(region.key(), name, None, 0)
            tab.update_arrow_recognition_row(region.key(), None, "hit", .99, "Counted hit: direction unknown.")
            assert "direction unknown" in data["status_var"].get()
            rr = SimpleNamespace(error=None, validation_note="Waiting for judgment.", is_unreadable=False)
            assert App._format_arrow_status(rr, None) == "Waiting for judgment."
        assert tab._region_rows["P1:judgment"]["thumb_label"].master.grid_info()["row"] == (
            tab._region_rows["P2:judgment"]["thumb_label"].master.grid_info()["row"]
        )
    finally:
        root.destroy()
