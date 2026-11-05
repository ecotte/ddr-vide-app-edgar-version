"""Player judgments validate arrow activity; ambiguous directions are never guessed."""
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from app import config, image_utils, layout_store
from app.analysis import AnalysisSession, JudgmentEvidence, LaneEvidence
from app.event_bus import EventBus
from app.models import LayoutProfile, Region
from app.recognition import classify_judgment_state
from app.ui.app_window import App
from app.ui.events_tab import EventsTab
from app.ui.video_tab import VideoTab


COLORS = {"hit": 255, "perfect": 255, "great": 225, "good": 200, "miss": 0, "idle": 128, "appeared": 64}


def image(state):
    return np.full((20, 20, 3), COLORS[state], dtype=np.uint8)


def setup(players=("P1",), directions=("Up-Right",), judgments=("P1",), graded=False):
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
                    for state in (("perfect", "great", "good", "miss", "idle") if graded else ("hit", "miss", "idle"))
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
    frame(1250)
    frame(1300, **{"P1:judgment": "hit", "P1:arrow:Up-Right": "hit"})
    frame(1600)
    assert len(outcomes(bus)) == 2
    assert outcomes(bus)[1].event_type == "arrow_hit"


@pytest.mark.parametrize("tier", ["perfect", "great", "good", "miss"])
@pytest.mark.parametrize("directions", [("Up-Right", "Center"), tuple(config.ARROW_DIRECTIONS)])
def test_simultaneous_arrows_share_one_judgment_and_count_each_direction(tier, directions):
    _, bus, _, frame = setup(directions=directions, graded=True)
    states = {f"P1:arrow:{direction}": "hit" for direction in directions}
    states["P1:judgment"] = tier
    frame(100, **states)
    resolved = frame(400, **states)
    frame(900, **states)
    events = outcomes(bus)
    assert len(events) == len(directions)
    assert len({event.event_id for event in events}) == len(directions)
    assert len({event.payload["judgment_id"] for event in events}) == 1
    assert {event.payload["direction"] for event in events} == set(directions)
    for event in events:
        assert event.event_type == ("arrow_miss" if tier == "miss" else "arrow_hit")
        assert event.payload["judgment"] == tier
        assert set(event.payload["step_directions"]) == set(directions)
        assert event.payload["step_arrow_count"] == len(directions)
        assert f"{len(directions)}-arrow step" in EventsTab._format_detail(event)
        assert event in resolved.new_events
    note = resolved.region_results[-1].validation_note
    assert all(direction in note for direction in directions)
    from app.live_scoreboard import LiveScoreboard

    board = LiveScoreboard(lambda: Mock())
    board.start(bus.session_id)
    try:
        for event in events + events:
            board.record(event, bus.session_id)
        player = board.snapshot()["players"]["P1"]
        assert player["hits"] == (len(directions) if tier != "miss" else 0)
        assert player["misses"] == (len(directions) if tier == "miss" else 0)
        for direction in directions:
            assert player["hitsByDirection"][direction] == (tier != "miss")
    finally:
        board.stop()


def test_two_judgments_cannot_both_claim_one_arrow():
    _, bus, _, frame = setup()
    frame(100, **{"P1:judgment": "hit", "P1:arrow:Up-Right": "hit"})
    frame(150)
    frame(200, **{"P1:judgment": "miss"})
    frame(500)
    assert [event.event_type for event in outcomes(bus)] == ["arrow_hit"]
    assert outcomes(bus)[0].payload["direction"] == "Up-Right"


def test_direct_success_to_miss_transition_requires_blank_before_recounting():
    _, bus, _, frame = setup()
    frame(0, **{"P1:judgment": "hit"})
    frame(100, **{"P1:judgment": "miss"})
    frame(500)
    assert [event.event_type for event in outcomes(bus)] == ["judgment_hit"]
    frame(750)
    frame(800, **{"P1:judgment": "miss"})
    frame(1100)
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
    regions[-1].reference_images = {}
    session.set_regions(regions)
    result = frame(0, **{"P1:arrow:Up-Right": "hit", "P1:judgment": "hit"})
    assert "No judgment outcome references" in result.region_results[-1].error
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
            for name, title in (("perfect", "Perfect"), ("great", "Great"), ("good", "Good"), ("miss", "Miss")):
                button, = [
                    widget for group in card.winfo_children() if isinstance(group, ttk.Frame)
                    for widget in group.winfo_children()
                    if isinstance(widget, ttk.Button) and widget["text"] == f"Add {title} ref"
                ]
                controller.capture_arrow_reference.reset_mock()
                button.invoke()
                if player == "P1":
                    controller.capture_arrow_reference.assert_called_with(region, name)
                else:
                    assert button.instate(["disabled"])
                    controller.capture_arrow_reference.assert_not_called()
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


@pytest.mark.parametrize("tier", ["perfect", "great", "good", "miss"])
@pytest.mark.parametrize("with_lane", [True, False])
def test_graded_judgments_preserve_tier_and_existing_outcomes(tier, with_lane):
    _, bus, _, frame = setup(graded=True)
    if with_lane:
        frame(0, **{"P1:arrow:Up-Right": "appeared"})
    states = {"P1:judgment": tier}
    if with_lane:
        states["P1:arrow:Up-Right"] = "hit"
    frame(100, **states)
    resolved = frame(400)
    event, = outcomes(bus)
    outcome = "miss" if tier == "miss" else "hit"
    assert event.event_type == f"{'arrow' if with_lane else 'judgment'}_{outcome}"
    assert event.payload["state"] == outcome
    assert event.payload["judgment"] == tier
    assert ("response_time_ms" in event.payload) == (with_lane and tier != "miss")
    direction = "Up-Right" if with_lane else "Direction unknown"
    expected_detail = f"{direction} - {tier.upper()}"
    if with_lane and tier != "miss":
        expected_detail += " | 100 ms response"
    assert EventsTab._format_detail(event) == expected_detail
    judgment_result = next(r for r in resolved.region_results if r.region.kind == "judgment")
    assert f"Counted {tier.upper()}:" in judgment_result.validation_note
    from app.live_scoreboard import LiveScoreboard

    board = LiveScoreboard(lambda: Mock())
    board.start(bus.session_id)
    try:
        board.record(event, bus.session_id)
        player = board.snapshot()["players"]["P1"]
        assert player["hits"] == (tier != "miss")
        assert player["misses"] == (tier == "miss")
    finally:
        board.stop()
    assert event.to_dict()["payload"]["judgment"] == tier


def test_perfect_to_good_transition_requires_a_blank():
    _, bus, _, frame = setup(graded=True)
    frame(0, **{"P1:judgment": "perfect"})
    frame(100, **{"P1:judgment": "good"})
    frame(400, **{"P1:judgment": "good"})
    frame(800, **{"P1:judgment": "good"})
    assert [event.payload["judgment"] for event in outcomes(bus)] == ["perfect"]
    frame(900)
    frame(1150)
    frame(1200, **{"P1:judgment": "good"})
    frame(1500)
    assert [event.payload["judgment"] for event in outcomes(bus)] == ["perfect", "good"]
    assert [event.event_type for event in outcomes(bus)] == ["judgment_hit", "judgment_hit"]


def test_partial_tier_calibration_counts_taught_outcomes_and_ignores_legacy_success():
    session, bus, regions, frame = setup()
    references = regions[-1].reference_images
    references["perfect"] = list(references["hit"])
    session.set_regions(regions)
    result = frame(0, **{"P1:judgment": "perfect"})
    assert result.region_results[-1].error is None
    assert "Untaught judgments: Great, Good" in result.region_results[-1].validation_note
    frame(300)
    assert outcomes(bus)[0].payload["judgment"] == "perfect"
    references["good"] = [image_utils.encode_crop_to_base64_png(image("good"))]
    session.set_regions(regions)
    frame(400, **{"P1:judgment": "perfect"})
    frame(700)
    assert [event.payload["judgment"] for event in outcomes(bus)] == ["perfect", "perfect"]


def test_perfect_good_ties_are_unconfirmed_and_graded_references_round_trip(tmp_path):
    assert classify_judgment_state(image("perfect"), {
        "perfect": [image("perfect")], "good": [image("perfect")],
        "miss": [image("miss")],
    }, .9)[0] is None
    _, _, regions, _ = setup(graded=True)
    path = str(tmp_path / "graded.json")
    layout_store.save_layout_to_path(LayoutProfile("graded", 40, 20, regions), path)
    loaded = layout_store.load_layout_from_path(path)
    assert loaded.regions[-1].reference_images == regions[-1].reference_images
    scaled = layout_store.rescale_profile(loaded, 80, 40)
    for tier in ("perfect", "great", "good", "miss", "idle"):
        assert image_utils.decode_base64_png_to_crop(
            scaled.regions[-1].reference_images[tier][0],
        ).shape == (40, 40, 3)


@pytest.mark.parametrize("tier", ["perfect", "great", "good", "miss"])
def test_lingering_tiers_ignore_brief_no_match_and_rearm_after_250ms(tier):
    session, bus, _, frame = setup(graded=True)
    session.set_arrow_min_confirm_frames(2)
    frame(0, **{"P1:judgment": tier})
    frame(20, **{"P1:judgment": tier})
    frame(300, **{"P1:judgment": tier})
    frame(400, **{"P1:judgment": "appeared"})
    frame(500, **{"P1:judgment": tier})
    frame(520, **{"P1:judgment": tier})
    frame(600)
    frame(620, **{"P1:judgment": tier})
    frame(640, **{"P1:judgment": tier})
    frame(1000, **{"P1:judgment": tier})
    assert len(outcomes(bus)) == 1
    frame(1100)
    frame(1120)
    frame(1350)
    frame(1400, **{"P1:judgment": tier})
    frame(1420, **{"P1:judgment": tier})
    frame(1700, **{"P1:judgment": tier})
    assert [event.payload["judgment"] for event in outcomes(bus)] == [tier, tier]


@pytest.mark.parametrize("tier", ["perfect", "great", "good", "miss"])
@pytest.mark.parametrize("with_lane", [True, False])
def test_one_taught_category_counts_for_both_players_with_shared_p1_references(tier, with_lane):
    session, bus, regions, frame = setup(
        players=("P1", "P2"), judgments=("P1", "P2"), graded=True,
    )
    p1 = next(region for region in regions if region.key() == "P1:judgment")
    p1.reference_images = {
        state: p1.reference_images[state] for state in (tier, "idle")
    }
    session.shared_player_references = True
    session.set_regions(regions)
    states = {"P1:judgment": tier, "P2:judgment": tier}
    if with_lane:
        states.update({"P1:arrow:Up-Right": "hit", "P2:arrow:Up-Right": "hit"})
    result = frame(100, **states)
    judgments = [r for r in result.region_results if r.region.kind == "judgment"]
    assert all(r.error is None and "Untaught judgments" in r.validation_note for r in judgments)
    frame(400, **states)
    frame(900, **states)
    assert len(outcomes(bus)) == 2
    expected = f"{'arrow' if with_lane else 'judgment'}_{'miss' if tier == 'miss' else 'hit'}"
    assert {event.player for event in outcomes(bus)} == {"P1", "P2"}
    assert all(event.event_type == expected and event.payload["judgment"] == tier for event in outcomes(bus))
    frame(1000)
    frame(1250)
    frame(1300, **states)
    frame(1600)
    assert len(outcomes(bus)) == 4


def test_single_taught_category_needs_no_blank_reference_and_ignores_short_gaps():
    session, bus, regions, frame = setup(graded=True)
    regions[-1].reference_images = {"perfect": regions[-1].reference_images["perfect"]}
    session.set_regions(regions)
    result = frame(100, **{"P1:judgment": "perfect"})
    assert result.region_results[-1].error is None
    assert "Capture a Blank reference" not in result.region_results[-1].validation_note
    frame(400, **{"P1:judgment": "perfect"})
    frame(500)
    frame(600, **{"P1:judgment": "perfect"})
    frame(1000, **{"P1:judgment": "perfect"})
    assert len(outcomes(bus)) == 1


@pytest.mark.parametrize("gap,expected", [(249, 1), (250, 2), (251, 2)])
def test_no_match_rearm_threshold_uses_source_time_without_blank_reference(gap, expected):
    session, bus, regions, frame = setup(graded=True)
    regions[-1].reference_images.pop("idle")
    session.set_regions(regions)
    frame(0, **{"P1:judgment": "perfect"})
    frame(300, **{"P1:judgment": "perfect"})
    frame(400)
    frame(400 + gap)
    frame(700, **{"P1:judgment": "perfect"})
    frame(1000, **{"P1:judgment": "perfect"})
    assert len(outcomes(bus)) == expected


def test_ambiguous_strong_match_restarts_absence_interval(monkeypatch):
    _, bus, _, frame = setup(graded=True)
    frame(0, **{"P1:judgment": "perfect"})
    frame(300, **{"P1:judgment": "perfect"})
    frame(400)
    with monkeypatch.context() as context:
        context.setattr("app.analysis.classify_judgment_state", lambda *args: (None, .999))
        frame(600)
    frame(650)
    frame(850)
    frame(860, **{"P1:judgment": "perfect"})
    frame(1200, **{"P1:judgment": "perfect"})
    assert len(outcomes(bus)) == 1


def test_repeated_paused_no_match_frame_does_not_rearm():
    session, bus, _, frame = setup(graded=True)
    frame(0, **{"P1:judgment": "perfect"})
    frame(300, **{"P1:judgment": "perfect"})
    pixels = np.concatenate((image("idle"), image("idle")), axis=1)
    for _ in range(10):
        session.process_arrow_frame(pixels, 10, 400)
    visible = np.concatenate((image("idle"), image("perfect")), axis=1)
    session.process_arrow_frame(visible, 11, 700)
    session.process_arrow_frame(visible, 12, 1000)
    assert len(outcomes(bus)) == 1


def test_legacy_blank_reference_cannot_hide_a_matching_word():
    assert classify_judgment_state(image("perfect"), {
        "perfect": [image("perfect")], "idle": [image("perfect")],
    }, .9)[0] == "perfect"


@pytest.mark.parametrize("spread,expected_count", [(150, 2), (151, 1)])
def test_simultaneous_group_requires_close_onsets_not_just_proximity_to_word(spread, expected_count):
    session, bus, regions, _ = setup(directions=("Center", "Up-Right"), graded=True)
    session._pending_judgments = [JudgmentEvidence(regions[-1], "great", 200, .99)]
    session._pending_lanes = [
        LaneEvidence(regions[0], 125, .98, 25),
        LaneEvidence(regions[1], 125 + spread, .97, 50),
    ]
    events = session._correlate_judgments(50, 500)
    assert len(events) == expected_count
    if expected_count == 2:
        assert [e.payload["response_time_ms"] for e in events] == [100, 225]
        assert [e.confidence for e in events] == [.98, .97]
    else:
        assert events[0].event_type == "judgment_hit"
        assert events[0].payload["step_directions"] == []
        assert events[0].payload["step_arrow_count"] == 0


def test_repeated_direction_does_not_get_counted_as_simultaneous_step():
    session, _, regions, _ = setup(graded=True)
    session._pending_judgments = [JudgmentEvidence(regions[-1], "perfect", 200, .99)]
    session._pending_lanes = [
        LaneEvidence(regions[0], 180, .99, None),
        LaneEvidence(regions[0], 220, .99, None),
    ]
    event, = session._correlate_judgments(50, 500)
    assert event.event_type == "judgment_hit"
    assert event.payload["direction"] is None


def test_competing_judgments_cannot_assign_same_simultaneous_arrows():
    session, _, regions, _ = setup(directions=("Center", "Up-Right"), graded=True)
    session._pending_judgments = [
        JudgmentEvidence(regions[-1], "perfect", 200, .99),
        JudgmentEvidence(regions[-1], "miss", 300, .99),
    ]
    session._pending_lanes = [
        LaneEvidence(regions[0], 225, .99, None),
        LaneEvidence(regions[1], 250, .99, None),
    ]
    events = session._correlate_judgments(50, 600)
    assert [e.event_type for e in events] == ["judgment_hit", "judgment_miss"]
    assert len({e.payload["judgment_id"] for e in events}) == 2
    assert all(e.payload["direction"] is None for e in events)
    assert session._pending_lanes == []


def test_simultaneous_steps_remain_separate_for_each_player():
    _, bus, _, frame = setup(
        players=("P1", "P2"), directions=("Center", "Up-Right"),
        judgments=("P1", "P2"), graded=True,
    )
    states = {f"{player}:arrow:{direction}": "hit"
              for player in ("P1", "P2") for direction in ("Center", "Up-Right")}
    frame(100, **states, **{"P1:judgment": "perfect", "P2:judgment": "miss"})
    frame(400)
    events = outcomes(bus)
    assert len(events) == 4
    assert len({e.payload["judgment_id"] for e in events}) == 2
    assert all(e.payload["judgment"] == ("perfect" if e.player == "P1" else "miss") for e in events)


@pytest.mark.parametrize("field", ["arrow", "judgment"])
@pytest.mark.parametrize("state", ["hit", "miss"])
def test_ungraded_event_details_do_not_invent_a_judgment_word(field, state):
    event = SimpleNamespace(
        event_type=f"{field}_{state}",
        payload={"field": field, "state": state, "direction": "Center" if field == "arrow" else None},
    )
    direction = "Center" if field == "arrow" else "Direction unknown"
    assert EventsTab._format_detail(event) == f"{direction} - {state.upper()}"
