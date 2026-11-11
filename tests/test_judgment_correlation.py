"""Player judgments validate arrow activity; ambiguous directions are never guessed."""
from types import SimpleNamespace
from unittest.mock import Mock
import json

import numpy as np
import pytest

from app import config, image_utils, layout_store
from app.analysis import AnalysisSession, JudgmentEvidence, LaneEvidence
from app.event_bus import EventBus
from app.models import LayoutProfile, Region
from app.recognition import JudgmentMatch, classify_judgment_state, match_judgment
from app.ui.app_window import App
from app.ui.events_tab import EventsTab
from app.ui.video_tab import VideoTab

pytestmark = pytest.mark.usefixtures("synthetic_judgment_matching")


COLORS = {"hit": 255, "perfect": 255, "great": 225, "good": 200, "miss": 0, "idle": 128, "appeared": 64}


def image(state):
    return np.full((20, 20, 3), COLORS[state], dtype=np.uint8)


def setup(players=("P1",), directions=("Up-Right",), judgments=("P1",), graded=False, judgment_confirm=1):
    regions = []
    for player in players:
        for direction in directions:
            regions.append(Region(
                direction, player, "arrow", len(regions) * 20, 0, 20, 20,
                direction=direction,
                reference_images={
                    "active": [
                        image_utils.encode_crop_to_base64_png(image(state)) for state in ("hit", "miss")
                    ],
                    "appeared": [image_utils.encode_crop_to_base64_png(image("appeared"))],
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
    session.set_judgment_min_confirm_frames(judgment_confirm)
    session.arrow_similarity_threshold = 0.99
    session.judgment_similarity_threshold = 0.99
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
    return [event for event in bus.events if event.event_type == "judgement"]


@pytest.mark.parametrize("tier", ["perfect", "great", "good", "miss", "hit"])
@pytest.mark.parametrize("player", ["P1", "P2"])
@pytest.mark.parametrize("directions", [(), ("Center",), ("Center", "Up-Right"), tuple(config.ARROW_DIRECTIONS)])
def test_new_contract_emits_immediate_activity_then_one_judgement(tier, player, directions, tmp_path):
    from app.eventstream import Outbox
    from app.live_scoreboard import LiveScoreboard

    _, bus, _, frame = setup(
        players=(player,), directions=directions, judgments=(player,), graded=tier != "hit",
    )
    target = tmp_path / "events.jsonl"
    outbox = Outbox(str(tmp_path / "events.sqlite3"), "example.servicebus.windows.net/events")
    delivered = []

    def enqueue(event):
        delivered.append(event)
        outbox.enqueue(event)

    bus.set_delivery_sink(enqueue)
    states = {f"{player}:arrow:{direction}": "hit" for direction in directions}
    states[f"{player}:judgment"] = tier
    try:
        first = frame(100, **states)
        assert outcomes(bus) == []
        activity_events = [event for event in first.new_events if event.event_type == "arrow"]
        assert len(activity_events) == len(directions)
        resolved = frame(750, **states)
        frame(1000, **states)
        judgment, = outcomes(bus)
        hit_events = [event for event in delivered if event.event_type == "arrow"]
        assert len(hit_events) == len(directions)
        assert hit_events == activity_events
        assert delivered == bus.events
        assert resolved.new_events == [judgment]
        assert judgment.payload == {
            "field": "judgment", "region_label": "Judgment",
            "outcome": "miss" if tier == "miss" else "hit",
            "state": "miss" if tier == "miss" else "hit",
            "judgment": tier, "judgment_id": judgment.payload["judgment_id"],
            "direction": directions[0] if len(directions) == 1 else None,
            "step_directions": list(directions), "step_arrow_count": len(directions),
            "evidence": "judgment_and_arrow" if directions else "judgment_only",
            "judgment_video_time_ms": 100,
            "correlation_window_ms": 500,
            "correlation_lookback_ms": 500, "correlation_lookahead_ms": 0,
        }
        assert judgment.player == player
        assert (judgment.frame_index, judgment.video_time_ms) == (2, 750)
        for event in hit_events:
            assert event.payload["state"] == "active"
            assert event.payload["evidence"] == "arrow_activity"
            assert event.payload["activity_video_time_ms"] == 100
            assert (event.frame_index, event.video_time_ms) == (1, 100)
            assert "outcome" not in event.payload and "judgment" not in event.payload
            assert "judgement_event_id" not in event.payload
        assert not any(event.event_type in ("arrow_hit", "arrow_miss", "judgment_hit", "judgment_miss") for event in delivered)
        bus.export_jsonl(str(target))
        exported = [json.loads(line) for line in target.read_text().splitlines()]
        assert exported == [event.to_dict() for event in delivered]
        queued = []
        while outbox.count():
            row_id, session_id, body = outbox.next_event()
            assert session_id == bus.session_id
            queued.append(json.loads(body))
            outbox.acknowledge(row_id)
        assert queued == exported
        board = LiveScoreboard(lambda: Mock())
        board.start(bus.session_id)
        try:
            # Activity may arrive before or after judgments and be retried, but never changes outcomes.
            for event in list(reversed(delivered)) + delivered:
                board.record(event, bus.session_id)
            result = board.snapshot()["players"][player]
            count = len(directions) or 1
            assert result["hits"] == (0 if tier == "miss" else count)
            assert result["misses"] == (count if tier == "miss" else 0)
            assert result["runningScore"] == config.JUDGMENT_POINTS.get(tier, 0) * count
            assert result["unassignedHits"] == int(not directions and tier != "miss")
            assert result["unassignedMisses"] == int(not directions and tier == "miss")
            assert result["hitPace"] == (0 if tier == "miss" else count / 5)
            for direction in config.ARROW_DIRECTIONS:
                assert result["hitsByDirection"][direction] == int(direction in directions and tier != "miss")
                assert result["missesByDirection"][direction] == int(direction in directions and tier == "miss")
            assert "invalid" not in board.status()
        finally:
            board.stop()
    finally:
        outbox.close()


@pytest.mark.parametrize("outcome", ["hit", "perfect", "great", "good", "miss"])
@pytest.mark.parametrize("offset", [-501, -500, -499, -300, -151, -150, -1, 0, 1, 150, 151])
def test_correlates_only_within_source_time_window_and_uses_judgment_outcome(outcome, offset):
    session, bus, _, frame = setup(graded=outcome != "hit")
    arrow = {"P1:arrow:Up-Right": "miss" if outcome == "hit" else "hit"}
    judgment = {"P1:judgment": outcome}
    if offset < 0:
        frame(1000 + offset, **arrow)
        frame(1000, **judgment)
    elif offset == 0:
        frame(1000, **arrow, **judgment)
    else:
        frame(1000, **judgment)
        frame(1000 + offset, **arrow)
    assert outcomes(bus) == []
    frame(1499)
    assert outcomes(bus) == []
    resolved = frame(1500)
    event, = outcomes(bus)
    known = -500 <= offset <= 0
    expected_outcome = "miss" if outcome == "miss" else "hit"
    assert event.event_type == "judgement"
    assert event.payload["outcome"] == expected_outcome
    arrow_events = [event for event in bus.events if event.event_type == "arrow"]
    assert len(arrow_events) == 1
    assert arrow_events[0].video_time_ms == 1000 + offset
    assert resolved.new_events == [event]
    assert not any(event.event_type in ("arrow_hit", "arrow_miss", "judgment_hit", "judgment_miss") for event in bus.events)
    assert event.payload["direction"] == ("Up-Right" if known else None)
    assert event.payload["state"] == expected_outcome
    assert event.payload["judgment_video_time_ms"] == 1000
    assert event.payload["correlation_lookback_ms"] == 500
    assert event.payload["correlation_lookahead_ms"] == 0
    assert event in resolved.new_events
    assert session._pending_judgments == []


@pytest.mark.parametrize("tier", ["perfect", "great", "good", "miss"])
@pytest.mark.parametrize("player", ["P1", "P2"])
def test_early_arrow_survives_delayed_word_and_full_settling_interval(tier, player):
    session, bus, _, frame = setup(
        players=("P1", "P2"), judgments=("P1", "P2"), graded=True,
    )
    frame(100, **{f"{player}:arrow:Up-Right": "hit"})
    frame(550)
    frame(600, **{f"{player}:judgment": tier})
    frame(1000)
    frame(1099)
    assert outcomes(bus) == []
    assert len(session._pending_lanes) == 1
    frame(1100)
    event, = outcomes(bus)
    assert event.player == player
    assert event.event_type == "judgement"
    assert event.payload["outcome"] == ("miss" if tier == "miss" else "hit")
    assert event.payload["direction"] == "Up-Right"
    assert event.payload["judgment"] == tier
    assert not session._pending_lanes


def test_waits_for_a_competing_delayed_judgment_at_the_last_allowed_instant():
    _, bus, _, frame = setup(graded=True)
    frame(1000, **{"P1:judgment": "perfect", "P1:arrow:Up-Right": "hit"})
    frame(1100)
    frame(1150)
    frame(1400)
    frame(1499)
    assert outcomes(bus) == []
    frame(1500, **{"P1:judgment": "good"})
    first, = outcomes(bus)
    assert first.event_type == "judgement" and first.payload["direction"] is None
    frame(2000)
    assert len(outcomes(bus)) == 2
    assert all(event.payload["direction"] is None for event in outcomes(bus))


def test_future_arrow_is_not_consumed_by_earlier_word_and_can_explain_a_later_word():
    _, bus, _, frame = setup(graded=True)
    frame(1000, **{"P1:judgment": "perfect"})
    frame(1050, **{"P1:arrow:Up-Right": "hit"})
    frame(1100)
    frame(1200, **{"P1:judgment": "great"})
    frame(1500)
    first, = outcomes(bus)
    assert first.payload["judgment"] == "perfect"
    assert first.payload["step_directions"] == []
    frame(1700)
    second = outcomes(bus)[1]
    assert second.payload["judgment"] == "great"
    assert second.payload["step_directions"] == ["Up-Right"]
    assert second.payload["correlation_lookahead_ms"] == 0


def test_expired_arrow_evidence_is_pruned_after_the_full_retention_interval():
    session, _, regions, _ = setup()
    session._pending_lanes = [LaneEvidence(regions[0], 100, .99)]
    assert session._correlate_judgments(50, 1100) == []
    assert len(session._pending_lanes) == 1
    assert session._correlate_judgments(51, 1101) == []
    assert not session._pending_lanes


def test_player_judgments_never_validate_other_player_arrows_or_infer_arrow_only_outcomes():
    _, bus, _, frame = setup(players=("P1", "P2"), judgments=("P1",))
    frame(100, **{"P1:judgment": "miss", "P2:arrow:Up-Right": "hit"})
    assert outcomes(bus) == []
    frame(750)
    p1, = outcomes(bus)
    assert (p1.player, p1.event_type, p1.payload["direction"]) == ("P1", "judgement", None)
    assert p1.payload["outcome"] == "miss"


def test_arrow_without_new_judgment_does_not_count_with_correlation_enabled():
    _, bus, _, frame = setup()
    frame(0, **{"P1:arrow:Up-Right": "hit"})
    frame(1000)
    assert outcomes(bus) == []
    activity, = [event for event in bus.events if event.event_type == "arrow"]
    assert activity.video_time_ms == 0
    assert activity.payload["state"] == "active" and "outcome" not in activity.payload


@pytest.mark.parametrize("tier", ["perfect", "great", "good", "miss"])
@pytest.mark.parametrize("player", ["P1", "P2"])
def test_live_scoreboard_waits_for_outcome_while_activity_is_delivered_immediately(tier, player):
    from app.live_scoreboard import LiveScoreboard

    _, bus, _, frame = setup(
        players=(player,), judgments=(player,), directions=("Center",), graded=True,
    )
    delivered = []
    bus.set_delivery_sink(delivered.append)
    board = LiveScoreboard(lambda: Mock())
    board.start(bus.session_id)
    bus.set_live_sink(board.record)
    try:
        first = frame(100, **{f"{player}:arrow:Center": "hit"})
        activity, = [event for event in first.new_events if event.event_type == "arrow"]
        assert activity in delivered
        assert activity.payload == {
            "field": "arrow", "region_label": "Center", "direction": "Center",
            "state": "active", "evidence": "arrow_activity", "activity_video_time_ms": 100,
        }
        before = board.snapshot()
        assert before["players"][player]["hits"] == before["players"][player]["misses"] == 0
        frame(200, **{f"{player}:judgment": tier})
        frame(699)
        assert outcomes(bus) == []
        assert board.snapshot()["players"] == before["players"]
        settled = frame(700)
        judgment, = settled.new_events
        assert judgment.event_type == "judgement"
        assert judgment.payload["direction"] == "Center"
        assert len([event for event in delivered if event.event_type == "arrow"]) == 1
        stats = board.snapshot()["players"][player]
        assert stats["hits"] == int(tier != "miss")
        assert stats["misses"] == int(tier == "miss")
        assert stats["runningScore"] == config.JUDGMENT_POINTS[tier]
    finally:
        bus.set_live_sink(None)
        board.stop()


@pytest.mark.parametrize("confirm_frames", [1, 2, 3])
def test_activity_confirmation_is_immediate_once_per_pulse_and_respects_recording(confirm_frames):
    session, bus, _, frame = setup(judgments=())
    session.set_arrow_min_confirm_frames(confirm_frames)
    states = {"P1:arrow:Up-Right": "hit"}
    for index in range(confirm_frames):
        frame(index * 20, **states)
        assert len([event for event in bus.events if event.event_type == "arrow"]) == int(index == confirm_frames - 1)
    activity, = [event for event in bus.events if event.event_type == "arrow"]
    assert activity.video_time_ms == (confirm_frames - 1) * 20
    assert activity.payload["activity_video_time_ms"] == 0
    for timestamp in (100, 200, 300):
        frame(timestamp, **states)
    assert len(bus.events) == 2  # session plus a single activity event
    for index in range(confirm_frames):
        frame(400 + index * 20)
    session.set_recording_enabled(False)
    for index in range(confirm_frames):
        frame(500 + index * 20, **states)
    session.set_recording_enabled(True)
    frame(600, **states)
    assert len(bus.events) == 2
    for index in range(confirm_frames):
        frame(700 + index * 20)
    for index in range(confirm_frames):
        frame(800 + index * 20, **states)
    assert len([event for event in bus.events if event.event_type == "arrow"]) == 2
    assert outcomes(bus) == []


def test_persistent_judgment_counts_once_and_cannot_validate_later_arrow_flashes():
    _, bus, _, frame = setup()
    frame(0, **{"P1:judgment": "hit"})
    frame(300, **{"P1:judgment": "hit"})
    frame(500, **{"P1:judgment": "hit", "P1:arrow:Up-Right": "hit"})
    frame(900, **{"P1:judgment": "hit"})
    assert len(outcomes(bus)) == 1
    assert outcomes(bus)[0].payload["step_directions"] == []
    frame(1000)
    frame(1250)
    frame(1300, **{"P1:judgment": "hit", "P1:arrow:Up-Right": "hit"})
    frame(1950)
    assert len(outcomes(bus)) == 2
    assert outcomes(bus)[1].payload["step_directions"] == ["Up-Right"]


@pytest.mark.parametrize("tier", ["perfect", "great", "good", "miss"])
@pytest.mark.parametrize("directions", [("Up-Right", "Center"), tuple(config.ARROW_DIRECTIONS)])
def test_simultaneous_arrows_share_one_judgment_and_count_each_direction(tier, directions):
    _, bus, _, frame = setup(directions=directions, graded=True)
    states = {f"P1:arrow:{direction}": "hit" for direction in directions}
    states["P1:judgment"] = tier
    frame(100, **states)
    resolved = frame(750, **states)
    frame(900, **states)
    events = outcomes(bus)
    assert len(events) == 1
    arrow_events = [event for event in bus.events if event.event_type == "arrow"]
    assert len(arrow_events) == len(directions)
    assert {event.payload["direction"] for event in arrow_events} == set(directions)
    assert all(event.video_time_ms == 100 and event.payload["state"] == "active" for event in arrow_events)
    assert len({event.event_id for event in resolved.new_events}) == len(resolved.new_events)
    assert len({event.payload["judgment_id"] for event in events}) == 1
    for event in events:
        assert event.event_type == "judgement"
        assert event.payload["outcome"] == ("miss" if tier == "miss" else "hit")
        assert event.payload["direction"] is None
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
        for event in list(reversed(bus.events)) + bus.events:
            board.record(event, bus.session_id)
        player = board.snapshot()["players"]["P1"]
        assert player["hits"] == (len(directions) if tier != "miss" else 0)
        assert player["misses"] == (len(directions) if tier == "miss" else 0)
        assert player["runningScore"] == config.JUDGMENT_POINTS[tier] * len(directions)
        for direction in directions:
            assert player["hitsByDirection"][direction] == (tier != "miss")
            assert player["missesByDirection"][direction] == (tier == "miss")
    finally:
        board.stop()


def test_two_judgments_cannot_both_claim_one_arrow():
    _, bus, _, frame = setup()
    frame(100, **{"P1:judgment": "hit", "P1:arrow:Up-Right": "hit"})
    frame(150)
    frame(200, **{"P1:judgment": "miss"})
    frame(750)
    assert [event.event_type for event in outcomes(bus)] == ["judgement"]
    assert outcomes(bus)[0].payload["direction"] == "Up-Right"


def test_direct_success_to_miss_transition_requires_blank_before_recounting():
    _, bus, _, frame = setup()
    frame(0, **{"P1:judgment": "hit"})
    frame(100, **{"P1:judgment": "miss"})
    frame(650)
    assert [event.payload["outcome"] for event in outcomes(bus)] == ["hit"]
    frame(900)
    frame(950, **{"P1:judgment": "miss"})
    frame(1600)
    assert [event.payload["outcome"] for event in outcomes(bus)] == ["hit", "miss"]


def test_recording_toggle_does_not_recount_a_still_visible_judgment():
    session, bus, _, frame = setup()
    frame(0, **{"P1:judgment": "hit"})
    frame(650, **{"P1:judgment": "hit"})
    session.set_recording_enabled(False)
    frame(700, **{"P1:judgment": "hit"})
    session.set_recording_enabled(True)
    frame(800, **{"P1:judgment": "hit"})
    frame(1200, **{"P1:judgment": "hit"})
    assert len(outcomes(bus)) == 1


def test_confirmation_samples_use_onset_time_not_confirmation_time():
    session, bus, _, frame = setup()
    session.set_arrow_min_confirm_frames(2)
    session.set_judgment_min_confirm_frames(2)
    both = {"P1:judgment": "hit", "P1:arrow:Up-Right": "hit"}
    frame(100, **both)
    assert outcomes(bus) == []
    frame(133, **both)
    frame(750)
    event, = outcomes(bus)
    assert event.event_type == "judgement"
    assert event.payload["direction"] == "Up-Right"
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
    session.process_arrow_frame(np.concatenate((image("idle"), image("idle")), axis=1), 11, 1150)
    assert len(outcomes(bus)) == 1


def test_legacy_appearance_images_do_not_generate_response_timing():
    _, bus, _, frame = setup()
    frame(0, **{"P1:arrow:Up-Right": "appeared"})
    frame(100, **{"P1:arrow:Up-Right": "hit", "P1:judgment": "hit"})
    frame(750)
    event, = outcomes(bus)
    assert "response_time_ms" not in event.payload
    assert "hit_video_time_ms" not in event.payload
    assert event.video_time_ms == 750


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
                assert not button.instate(["disabled"])
                controller.capture_arrow_reference.assert_called_once_with(region, name)
                clear_button, = [
                    widget for group in card.winfo_children() if isinstance(group, ttk.Frame)
                    for widget in group.winfo_children()
                    if isinstance(widget, ttk.Button) and widget["text"] == f"Clear {title} refs"
                ]
                controller.clear_arrow_reference.reset_mock()
                clear_button.invoke()
                assert not clear_button.instate(["disabled"])
                controller.clear_arrow_reference.assert_called_once_with(region, name)
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
    resolved = frame(750)
    event, = outcomes(bus)
    outcome = "miss" if tier == "miss" else "hit"
    assert event.event_type == "judgement"
    assert event.payload["outcome"] == outcome
    assert event.payload["state"] == outcome
    assert event.payload["judgment"] == tier
    assert "response_time_ms" not in event.payload
    direction = "Up-Right" if with_lane else "Direction unknown"
    expected_detail = f"{direction} - {tier.upper()}"
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
        assert player["runningScore"] == config.JUDGMENT_POINTS[tier]
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
    frame(1850)
    assert [event.payload["judgment"] for event in outcomes(bus)] == ["perfect", "good"]
    assert [event.event_type for event in outcomes(bus)] == ["judgement", "judgement"]


def test_partial_tier_calibration_counts_taught_outcomes_and_ignores_legacy_success():
    session, bus, regions, frame = setup()
    references = regions[-1].reference_images
    references["perfect"] = list(references["hit"])
    session.set_regions(regions)
    result = frame(0, **{"P1:judgment": "perfect"})
    assert result.region_results[-1].error is None
    assert "Untaught judgments: Great, Good" in result.region_results[-1].validation_note
    frame(650)
    assert outcomes(bus)[0].payload["judgment"] == "perfect"
    references["good"] = [image_utils.encode_crop_to_base64_png(image("good"))]
    session.set_regions(regions)
    frame(750, **{"P1:judgment": "perfect"})
    frame(1400)
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
def test_lingering_tiers_ignore_brief_no_match_and_rearm_after_50ms(tier):
    session, bus, _, frame = setup(graded=True)
    session.set_arrow_min_confirm_frames(2)
    session.set_judgment_min_confirm_frames(2)
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
    frame(1150)
    frame(1200, **{"P1:judgment": tier})
    frame(1220, **{"P1:judgment": tier})
    frame(1850, **{"P1:judgment": tier})
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
    expected = "judgement"
    assert {event.player for event in outcomes(bus)} == {"P1", "P2"}
    assert all(event.event_type == expected and event.payload["judgment"] == tier for event in outcomes(bus))
    frame(1000)
    frame(1250)
    frame(1300, **states)
    frame(1950)
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


@pytest.mark.parametrize("gap,expected", [(49, 1), (50, 2), (51, 2)])
@pytest.mark.parametrize("tier", ["perfect", "great", "good", "miss"])
@pytest.mark.parametrize("player", ["P1", "P2"])
def test_no_match_rearm_threshold_uses_source_time_without_blank_reference(gap, expected, tier, player):
    assert config.JUDGMENT_REARM_MS == 50
    session, bus, regions, frame = setup(
        players=(player,), judgments=(player,), graded=True,
    )
    regions[-1].reference_images.pop("idle")
    session.set_regions(regions)
    frame(0, **{f"{player}:judgment": tier})
    frame(300, **{f"{player}:judgment": tier})
    frame(400)
    frame(400 + gap)
    frame(500, **{f"{player}:judgment": tier})
    frame(1150, **{f"{player}:judgment": tier})
    assert len(outcomes(bus)) == expected
    assert all(event.player == player and event.payload["judgment"] == tier for event in outcomes(bus))


@pytest.mark.parametrize("tier", ["perfect", "great", "good", "miss"])
def test_50ms_rearm_recovers_repeated_judgments_at_two_per_second(tier):
    _, bus, _, frame = setup(graded=True)
    for index in range(150):
        state = tier if index % 15 < 9 else "idle"
        frame(round(index * 1000 / 30), **{"P1:judgment": state})
    frame(6000)
    events = outcomes(bus)
    assert len(events) == 10
    assert all(event.payload["judgment"] == tier for event in events)
    assert not any(event.event_type == "arrow" for event in bus.events)


def test_ambiguous_strong_match_restarts_absence_interval(monkeypatch):
    _, bus, _, frame = setup(graded=True)
    frame(0, **{"P1:judgment": "perfect"})
    frame(300, **{"P1:judgment": "perfect"})
    frame(400)
    with monkeypatch.context() as context:
        context.setattr(
            "app.recognition.match_judgment",
            lambda *args: JudgmentMatch(None, .999, "perfect", "great", .99),
        )
        frame(425)
    frame(450)
    frame(499)
    frame(500, **{"P1:judgment": "perfect"})
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


def test_arrow_and_judgment_thresholds_are_independent():
    session, _, regions, _ = setup(graded=True)
    regions[-1].reference_images = {"perfect": regions[-1].reference_images["perfect"]}
    session.set_regions(regions)
    session.set_arrow_similarity_threshold(.9)
    session.set_judgment_similarity_threshold(.9)
    pixels = np.full((20, 40, 3), 180, dtype=np.uint8)
    first = session.process_arrow_frame(pixels, 0, 0)
    assert first.region_results[0].arrow_state == "idle"
    assert first.region_results[1].arrow_state == "idle"
    session.set_judgment_similarity_threshold(.65)
    assert session.arrow_similarity_threshold == .9
    second = session.process_arrow_frame(pixels, 1, 33)
    assert second.region_results[0].arrow_state == "idle"
    assert second.region_results[1].arrow_state == "perfect"
    session.set_judgment_similarity_threshold(.99)
    session.set_arrow_similarity_threshold(.65)
    assert session.judgment_similarity_threshold == .99
    third = session.process_arrow_frame(pixels, 2, 66)
    assert third.region_results[0].arrow_state == "active"
    assert third.region_results[1].arrow_state == "idle"
    assert session.game_end_similarity_threshold == config.DEFAULT_GAME_END_SIMILARITY_THRESHOLD


@pytest.mark.parametrize("setter", ["set_arrow_similarity_threshold", "set_judgment_similarity_threshold"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -.01, 1.01])
def test_invalid_similarity_settings_cannot_change_recognition_thresholds(setter, value):
    session = AnalysisSession(EventBus())
    before = (session.arrow_similarity_threshold, session.judgment_similarity_threshold)
    with pytest.raises(ValueError, match="finite number between 0 and 1"):
        getattr(session, setter)(value)
    assert (session.arrow_similarity_threshold, session.judgment_similarity_threshold) == before


def test_match_details_retain_scores_without_changing_classification():
    references = {
        "perfect": [image("perfect")], "great": [image("great")], "miss": [image("miss")],
    }
    result = match_judgment(image("perfect"), references, .9)
    assert result.state == result.best_state == "perfect"
    assert result.similarity == pytest.approx(1)
    assert result.runner_up_state == "great"
    assert result.runner_up_similarity == pytest.approx(image_utils.similarity(image("perfect"), image("great")))
    assert classify_judgment_state(image("perfect"), references, .9) == (result.state, result.similarity)
    assert match_judgment(image("perfect"), {}, .9) == JudgmentMatch(None, 0)


def test_judgment_diagnostics_explain_threshold_acceptance_latch_and_rearm():
    _, _, _, frame = setup(graded=True)
    below = frame(0).region_results[-1]
    assert "Below judgment threshold" in below.judgment_diagnostics
    assert "Best:" in below.judgment_diagnostics and "Next:" in below.judgment_diagnostics
    assert "threshold 0.990" in below.judgment_diagnostics
    assert "Frame 1 | 0 ms" in below.judgment_diagnostics
    accepted = frame(100, **{"P1:judgment": "perfect"}).region_results[-1]
    assert "Accepted PERFECT" in accepted.judgment_diagnostics
    assert "Best: PERFECT 1.000" in accepted.judgment_diagnostics
    held = frame(120, **{"P1:judgment": "great"}).region_results[-1]
    assert "Latch blocked GREAT; previous PERFECT" in held.judgment_diagnostics
    frame(200)
    short_gap = frame(249).region_results[-1]
    assert "no-match 49/50 ms" in short_gap.judgment_diagnostics
    ready = frame(250).region_results[-1]
    assert "Latch: ready" in ready.judgment_diagnostics
    assert "no-match 50/50 ms" in ready.judgment_diagnostics
    accepted_again = frame(300, **{"P1:judgment": "great"}).region_results[-1]
    assert "Accepted GREAT" in accepted_again.judgment_diagnostics


def test_diagnostics_reuse_the_single_judgment_matching_pass(monkeypatch):
    session, _, regions, frame = setup(graded=True)
    session.set_regions([regions[-1]])
    similarity = Mock(wraps=image_utils.similarity)
    monkeypatch.setattr(image_utils, "similarity", similarity)
    result = frame(0, **{"P1:judgment": "perfect"}).region_results[-1]
    assert similarity.call_count == 4
    assert "Next:" in result.judgment_diagnostics


def test_judgment_diagnostics_distinguish_ambiguity_from_low_similarity():
    session, _, regions, frame = setup(graded=True)
    regions[-1].reference_images["great"] = list(regions[-1].reference_images["perfect"])
    session.set_regions(regions)
    result = frame(0, **{"P1:judgment": "perfect"}).region_results[-1]
    assert result.arrow_state is None
    assert "Ambiguous match" in result.judgment_diagnostics
    assert "gap 0.000 (minimum 0.020)" in result.judgment_diagnostics
    assert "Below judgment threshold" not in result.judgment_diagnostics


def test_judgment_diagnostics_explain_confirmation_recording_and_repeated_frames():
    session, bus, _, frame = setup(graded=True)
    session.set_arrow_min_confirm_frames(2)
    session.set_judgment_min_confirm_frames(2)
    confirming = frame(0, **{"P1:judgment": "perfect"}).region_results[-1]
    assert "Confirming PERFECT: 1/2 frames" in confirming.judgment_diagnostics
    session.set_recording_enabled(False)
    off = frame(33, **{"P1:judgment": "perfect"}).region_results[-1]
    assert "Recording off" in off.judgment_diagnostics
    assert "recording off" in off.judgment_diagnostics
    pixels = np.concatenate((image("idle"), image("perfect")), axis=1)
    repeated = session.process_arrow_frame(pixels, 2, 33).region_results[-1]
    assert "Repeated frame" in repeated.judgment_diagnostics
    assert outcomes(bus) == []


@pytest.mark.parametrize("invalid", ["references", "crop"])
def test_judgment_diagnostics_surface_calibration_and_crop_errors(invalid):
    session, _, regions, frame = setup(graded=True)
    if invalid == "references":
        regions[-1].reference_images.clear()
    else:
        regions[-1].x = 1000
    session.set_regions(regions)
    result = session.process_arrow_frame(
        np.full((20, 40, 3), 128, dtype=np.uint8), 0, 0,
    ).region_results[-1]
    assert result.error
    assert result.error in result.judgment_diagnostics
    assert "Best: unavailable." in result.judgment_diagnostics


@pytest.mark.parametrize("spread,expected_count", [(150, 2), (151, 0)])
def test_simultaneous_group_requires_close_onsets_not_just_proximity_to_word(spread, expected_count):
    session, bus, regions, _ = setup(directions=("Center", "Up-Right"), graded=True)
    session._pending_judgments = [JudgmentEvidence(regions[-1], "great", 200, .99)]
    session._pending_lanes = [
        LaneEvidence(regions[0], 200 - spread, .98),
        LaneEvidence(regions[1], 200, .97),
    ]
    events = session._correlate_judgments(50, 850)
    assert len(events) == 1
    assert events[0].payload["step_arrow_count"] == expected_count
    if expected_count:
        assert all("response_time_ms" not in e.payload for e in events)
        assert [e.confidence for e in events] == [.99]
    else:
        assert events[0].event_type == "judgement"
        assert events[0].payload["step_directions"] == []
        assert events[0].payload["step_arrow_count"] == 0


def test_repeated_direction_does_not_get_counted_as_simultaneous_step():
    session, _, regions, _ = setup(graded=True)
    session._pending_judgments = [JudgmentEvidence(regions[-1], "perfect", 200, .99)]
    session._pending_lanes = [
        LaneEvidence(regions[0], 180, .99),
        LaneEvidence(regions[0], 200, .99),
    ]
    event, = session._correlate_judgments(50, 850)
    assert event.event_type == "judgement"
    assert event.payload["direction"] is None


def test_competing_judgments_cannot_assign_same_simultaneous_arrows():
    session, _, regions, _ = setup(directions=("Center", "Up-Right"), graded=True)
    session._pending_judgments = [
        JudgmentEvidence(regions[-1], "perfect", 200, .99),
        JudgmentEvidence(regions[-1], "miss", 300, .99),
    ]
    session._pending_lanes = [
        LaneEvidence(regions[0], 175, .99),
        LaneEvidence(regions[1], 200, .99),
    ]
    events = session._correlate_judgments(50, 950)
    assert [e.event_type for e in events] == ["judgement", "judgement"]
    assert [e.payload["outcome"] for e in events] == ["hit", "miss"]
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
    frame(750)
    events = outcomes(bus)
    assert len(events) == 2
    assert len([event for event in bus.events if event.event_type == "arrow"]) == 4
    assert len({e.payload["judgment_id"] for e in events}) == 2
    assert all(e.payload["judgment"] == ("perfect" if e.player == "P1" else "miss") for e in events)


@pytest.mark.parametrize("candidate_specs,reason,assigned", [
    ([], "No arrow activation", []),
    ([("P2", "Center", 100)], "No arrow activation", []),
    ([("P1", "Center", 100), ("P1", "Center", 133)], "repeated activations", []),
    ([("P1", "Center", 0), ("P1", "Up-Right", 200)], "span more than 150 ms", []),
    ([("P1", "Center", 100)], "Assigned: Center.", ["Center"]),
])
def test_correlation_diagnostics_explain_decisions_without_changing_event_payload(candidate_specs, reason, assigned):
    session, _, regions, _ = setup(
        players=("P1", "P2"), judgments=("P1", "P2"),
        directions=("Center", "Up-Right"), graded=True,
    )
    by_key = {region.key(): region for region in regions}
    session._pending_judgments = [JudgmentEvidence(by_key["P1:judgment"], "perfect", 200, .99)]
    session._pending_lanes = [
        LaneEvidence(by_key[f"{player}:arrow:{direction}"], onset, .95)
        for player, direction, onset in candidate_specs
    ]
    event, = session._correlate_judgments(50, 850)
    diagnostic = session._last_correlation_diagnostics["P1:judgment"]
    assert reason in diagnostic
    assert "PERFECT at 200 ms" in diagnostic
    assert event.payload["step_directions"] == assigned
    assert "diagnostics" not in event.payload
    if assigned:
        assert "Center -100 ms" in diagnostic
    assert "P2:judgment" not in session._last_correlation_diagnostics


def test_correlation_diagnostics_explain_competing_words():
    session, _, regions, _ = setup(graded=True)
    session._pending_judgments = [
        JudgmentEvidence(regions[-1], "perfect", 200, .99),
        JudgmentEvidence(regions[-1], "great", 300, .99),
    ]
    session._pending_lanes = [LaneEvidence(regions[0], 200, .95)]
    session._correlate_judgments(50, 850)
    assert "shared with another judgment" in session._last_correlation_diagnostics[regions[-1].key()]


def test_correlation_diagnostics_show_wait_then_persist_last_decision_until_reset():
    session, _, _, frame = setup(graded=True)
    pending = frame(100, **{"P1:judgment": "perfect"}).region_results[-1]
    assert "Pending judgment: 500 ms" in pending.judgment_diagnostics
    assert "Last direction decision:" not in pending.judgment_diagnostics
    settled = frame(750).region_results[-1]
    assert "Last direction decision: PERFECT at 100 ms." in settled.judgment_diagnostics
    assert "No arrow activation" in settled.judgment_diagnostics
    assert "Candidates (relative to word): none" in settled.judgment_diagnostics
    following = frame(800).region_results[-1]
    assert "Last direction decision:" in following.judgment_diagnostics
    session.reset_tracking()
    assert not session._last_correlation_diagnostics
    assert "Last direction decision:" not in frame(850).region_results[-1].judgment_diagnostics


@pytest.mark.parametrize("field", ["arrow", "judgment"])
@pytest.mark.parametrize("state", ["hit", "miss"])
def test_ungraded_event_details_do_not_invent_a_judgment_word(field, state):
    event = SimpleNamespace(
        event_type=f"{field}_{state}",
        payload={"field": field, "state": state, "direction": "Center" if field == "arrow" else None},
    )
    direction = "Center" if field == "arrow" else "Direction unknown"
    assert EventsTab._format_detail(event) == f"{direction} - {state.upper()}"
