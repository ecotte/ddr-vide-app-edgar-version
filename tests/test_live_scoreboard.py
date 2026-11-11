import copy
import time
import threading
import pytest

from app.event_bus import EventBus
from app.live_scoreboard import LiveScoreboard
from app.rayfin_sender import ScoreboardPublishError


class FakeSender:
    def __init__(self):
        self.published = []
        self.closed = False

    def publish(self, snapshot):
        self.published.append(copy.deepcopy(snapshot))

    def close(self):
        self.closed = True


def event(bus, kind="combo", value=10, player="P1", direction="Up-Right"):
    return bus.make_event(kind, {
        "new_value": value, "observed_value": value, "value": value, "direction": direction,
    }, 1, 17, player, 0.99)


def judgement_event(bus, tier="perfect", directions=None, player="P1"):
    directions = ["Center"] if directions is None else directions
    outcome = "miss" if tier == "miss" else "hit"
    return bus.make_event("judgement", {
        "field": "judgment", "outcome": outcome, "state": outcome,
        "judgment": tier, "direction": directions[0] if len(directions) == 1 else None,
        "step_directions": directions, "step_arrow_count": len(directions),
    }, 1, 17, player, .99)


def test_streak_counts_judgments_once_and_misses_break_only_the_current_run():
    bus = EventBus()
    board = LiveScoreboard(FakeSender)
    board.start(bus.session_id)
    try:
        for tier, directions in [
            ("perfect", ["Center", "Up-Left"]), ("great", []), ("good", ["Down-Right"]),
        ]:
            row = judgement_event(bus, tier=tier, directions=directions)
            board.record(row, bus.session_id)
            board.record(row, bus.session_id)
        player = board.snapshot()["players"]["P1"]
        assert player["hits"] == 4
        assert (player["currentStreak"], player["bestStreak"]) == (3, 3)
        board.record(judgement_event(bus, tier="miss", directions=[]), bus.session_id)
        board.record(judgement_event(bus), bus.session_id)
        board.record(judgement_event(bus, player="P2"), bus.session_id)
        players = board.snapshot()["players"]
        assert (players["P1"]["currentStreak"], players["P1"]["bestStreak"]) == (1, 3)
        assert (players["P2"]["currentStreak"], players["P2"]["bestStreak"]) == (1, 1)
    finally:
        board.stop()


def test_streak_ignores_ocr_combos_legacy_lane_events_and_synthetic_events():
    bus = EventBus()
    board = LiveScoreboard(FakeSender)
    board.start(bus.session_id)
    try:
        for row in [event(bus, value=999), event(bus, kind="arrow_hit"), event(bus, kind="arrow_miss")]:
            board.record(row, bus.session_id)
        synthetic = judgement_event(bus)
        synthetic.source_mode = "synthetic"
        board.record(synthetic, bus.session_id)
        test = judgement_event(bus)
        test.payload["is_test"] = True
        board.record(test, bus.session_id)
        assert board.snapshot()["players"]["P1"]["bestStreak"] == 0
        assert board.snapshot()["players"]["P1"]["currentStreak"] == 0
    finally:
        board.stop()


@pytest.mark.parametrize("timestamps", [(100, 200, 300), (100, 100, 100)])
def test_delayed_judgments_recompute_streak_in_video_time_and_sequence_order(timestamps):
    bus = EventBus()
    board = LiveScoreboard(FakeSender)
    board.start(bus.session_id)
    try:
        rows = [judgement_event(bus, tier=tier) for tier in ("perfect", "miss", "great")]
        for sequence, (row, timestamp) in enumerate(zip(rows, timestamps), start=1):
            row.video_time_ms = timestamp
            row.sequence = sequence
        for index in (2, 0):
            board.record(rows[index], bus.session_id)
        assert board.snapshot()["players"]["P1"]["bestStreak"] == 2
        board.record(rows[1], bus.session_id)
        player = board.snapshot()["players"]["P1"]
        assert (player["currentStreak"], player["bestStreak"]) == (1, 1)
    finally:
        board.stop()


def test_session_reset_clears_streaks_and_their_ordering_history():
    bus = EventBus()
    board = LiveScoreboard(FakeSender)
    board.start(bus.session_id)
    try:
        old = judgement_event(bus)
        board.record(old, bus.session_id)
        bus.new_session()
        board.record(None, bus.session_id)
        board.record(old, bus.session_id)
        for player in board.snapshot()["players"].values():
            assert player["bestStreak"] == player["currentStreak"] == 0
        assert board._streak_outcomes == {"P1": [], "P2": []}
    finally:
        board.stop()


@pytest.mark.parametrize("timestamp", [None, -1, True, 1.5, 9007199254740992])
def test_invalid_judgment_time_cannot_fabricate_or_bridge_a_streak(timestamp):
    bus = EventBus()
    board = LiveScoreboard(FakeSender)
    board.start(bus.session_id)
    try:
        row = judgement_event(bus)
        row.video_time_ms = timestamp
        before = board.snapshot()
        board.record(row, bus.session_id)
        assert board.snapshot() == before
        assert "invalid judgement" in board.status()
    finally:
        board.stop()


@pytest.mark.parametrize("player", ["P1", "P2"])
@pytest.mark.parametrize("direction", ["Up-Right", "Up-Left", "Down-Right", "Down-Left", "Center"])
@pytest.mark.parametrize("event_type", ["arrow", "arrow_hit"])
def test_arrow_activity_never_changes_score_counts_accuracy_or_pace(player, direction, event_type):
    bus = EventBus()
    board = LiveScoreboard(FakeSender)
    board.start(bus.session_id)
    try:
        row = bus.make_event(event_type, {
            "field": "arrow", "direction": direction, "state": "active",
            "evidence": "arrow_activity", "activity_video_time_ms": 100,
        }, 1, 100, player, .99)
        before = board.snapshot()
        board.record(row, bus.session_id)
        board.record(row, bus.session_id)
        assert board.snapshot() == before
        board.record(judgement_event(bus, tier="miss", directions=[direction], player=player), bus.session_id)
        stats = board.snapshot()["players"][player]
        assert stats["hits"] == 0 and stats["misses"] == 1
        assert stats["missesByDirection"][direction] == 1
        assert stats["runningScore"] == 0 and stats["hitPace"] == 0
    finally:
        board.stop()


@pytest.mark.parametrize("invalid", [
    {"direction": "invalid"}, {"direction": []}, {"direction": None}, {"state": "hit"},
    {"evidence": "unknown"},
])
def test_invalid_activity_is_reported_without_counting_a_hit(invalid):
    bus = EventBus()
    board = LiveScoreboard(FakeSender)
    board.start(bus.session_id)
    try:
        row = bus.make_event("arrow", {
            "field": "arrow", "direction": "Center", "state": "active", "evidence": "arrow_activity",
            **invalid,
        }, 1, 100, "P1", .99)
        before = board.snapshot()
        board.record(row, bus.session_id)
        assert board.snapshot() == before
        assert "invalid arrow activity payload" in board.status()
    finally:
        board.stop()


@pytest.mark.parametrize("invalid", [
    {"outcome": "perfect"}, {"outcome": None}, {"outcome": "miss"},
    {"state": "miss"}, {"judgment": "unknown"}, {"judgment": []},
    {"judgment": "miss"}, {"step_directions": None}, {"step_directions": "Center"},
    {"step_directions": ["invalid"]}, {"step_directions": [[]]},
    {"step_directions": ["Center", "Center"], "step_arrow_count": 2, "direction": None},
    {"step_arrow_count": True}, {"step_arrow_count": -1}, {"step_arrow_count": 2},
    {"direction": "Up-Right"}, {"direction": None},
])
def test_invalid_judgement_payload_does_not_change_metrics(invalid):
    bus = EventBus()
    board = LiveScoreboard(FakeSender)
    board.start(bus.session_id)
    try:
        row = judgement_event(bus)
        row.payload.update(invalid)
        before = board.snapshot()
        board.record(row, bus.session_id)
        assert board.snapshot() == before
        assert "invalid judgement outcome payload" in board.status()
    finally:
        board.stop()


def test_new_judgements_preserve_player_isolation_synthetic_exclusion_and_session_reset():
    bus = EventBus()
    board = LiveScoreboard(FakeSender)
    bus.set_live_sink(board.record)
    board.start(bus.session_id)
    try:
        old = judgement_event(bus, directions=["Up-Left", "Center"], player="P2")
        bus.record(old)
        synthetic = judgement_event(bus, tier="miss")
        synthetic.source_mode = "synthetic"
        bus.record(synthetic)
        assert board.snapshot()["players"]["P1"]["misses"] == 0
        assert board.snapshot()["players"]["P2"]["runningScore"] == 80
        bus.new_session()
        bus.record(old)
        assert all(player["runningScore"] == 0 for player in board.snapshot()["players"].values())
        bus.record(judgement_event(bus, tier="good", directions=[], player="P1"))
        assert board.snapshot()["players"]["P1"]["runningScore"] == 10
        assert board.snapshot()["players"]["P2"]["runningScore"] == 0
    finally:
        board.stop()


def test_multi_arrow_judgement_checks_total_points_before_mutating_metrics():
    bus = EventBus()
    board = LiveScoreboard(FakeSender)
    board.start(bus.session_id)
    try:
        board._state["players"]["P1"]["runningScore"] = 9007199254740991 - 40
        before = board.snapshot()
        board.record(judgement_event(bus, directions=["Center", "Up-Left"]), bus.session_id)
        assert board.snapshot() == before
        assert "supported range" in board.status()
    finally:
        board.stop()


@pytest.mark.parametrize("value", [None, -1, 1.5, "12", True, [], 9007199254740992])
def test_invalid_current_combo_is_reported_without_using_old_new_value(value):
    bus = EventBus()
    board = LiveScoreboard(FakeSender)
    board.start(bus.session_id)
    try:
        row = bus.make_event("combo", {"value": value, "new_value": 20}, 1, 33, "P1")
        before = board.snapshot()
        board.record(row, bus.session_id)
        assert board.snapshot() == before
        assert "invalid numeric event" in board.status()
    finally:
        board.stop()


def test_current_and_legacy_combos_share_sequence_ordering_and_preserve_zero():
    bus = EventBus()
    board = LiveScoreboard(FakeSender)
    board.start(bus.session_id)
    try:
        old = bus.make_event("combo", {"value": 40}, 1, 33, "P1")
        legacy = bus.make_event("combo_changed", {"previous_value": 40, "new_value": 45}, 2, 66, "P1")
        board.record(legacy, bus.session_id)
        board.record(old, bus.session_id)
        assert board.snapshot()["players"]["P1"]["combo"] == 45
        current = bus.make_event("combo", {"value": 0}, 3, 99, "P1")
        board.record(current, bus.session_id)
        board.record(current, bus.session_id)
        p2 = bus.make_event("combo", {"value": 8}, 3, 99, "P2")
        board.record(p2, bus.session_id)
        assert board.snapshot()["players"]["P1"]["combo"] == 0
        assert board.snapshot()["players"]["P2"]["combo"] == 8
        assert board.snapshot()["players"]["P1"]["runningScore"] == 0
    finally:
        board.stop()


def test_running_score_uses_judgment_points_per_arrow_and_once_for_unassigned():
    board = LiveScoreboard(FakeSender)
    bus = EventBus()
    bus.set_live_sink(board.record)
    board.start(bus.session_id)
    try:
        assert board.snapshot()["players"]["P1"]["runningScore"] == 0
        rows = []
        for tier, kind, direction in (
            ("good", "arrow_hit", "Center"),
            ("great", "arrow_hit", "Up-Left"),
            ("perfect", "arrow_hit", "Down-Left"),
            ("perfect", "arrow_hit", "Up-Right"),
            ("miss", "arrow_miss", "Center"),
            ("great", "judgment_hit", None),
        ):
            row = event(bus, kind, direction=direction)
            row.payload.update(judgment=tier, judgment_id="same-simultaneous-step" if tier == "perfect" else tier)
            rows.append(row)
        for row in reversed(rows):
            bus.record(row)
        for row in rows:
            bus.record(row)
        assert board.snapshot()["players"]["P1"]["runningScore"] == 130
        p2 = event(bus, "arrow_hit", player="P2")
        p2.payload["judgment"] = "perfect"
        bus.record(p2)
        assert board.snapshot()["players"]["P2"]["runningScore"] == 40
        bus.record(event(bus, "score_changed", value=999999))
        bus.record(event(bus, "score_observed", value=888888))
        bus.record(event(bus, "arrow_hit"))
        assert board.snapshot()["players"]["P1"]["runningScore"] == 130
        bus.new_session()
        assert [p["runningScore"] for p in board.snapshot()["players"].values()] == [0, 0]
        bus.record(rows[0])
        assert board.snapshot()["players"]["P1"]["runningScore"] == 0
    finally:
        board.stop()


@pytest.mark.parametrize("tier,kind", [
    ("unknown", "arrow_hit"), ([], "arrow_hit"), (123, "arrow_hit"),
    ("miss", "arrow_hit"), ("perfect", "arrow_miss"),
])
def test_invalid_judgment_does_not_change_score_or_outcome_counts(tier, kind):
    board = LiveScoreboard(FakeSender)
    bus = EventBus()
    board.start(bus.session_id)
    try:
        row = event(bus, kind)
        row.payload["judgment"] = tier
        before = board.snapshot()
        board.record(row, bus.session_id)
        assert board.snapshot() == before
        assert "invalid judgment tier" in board.status()
    finally:
        board.stop()


def test_running_score_excludes_synthetic_events_and_guards_safe_integer_range():
    board = LiveScoreboard(FakeSender)
    bus = EventBus()
    board.start(bus.session_id)
    try:
        row = event(bus, "arrow_hit")
        row.payload["judgment"] = "perfect"
        row.source_mode = "synthetic"
        board.record(row, bus.session_id)
        assert board.snapshot()["players"]["P1"]["runningScore"] == 0
        row.source_mode = "replay"
        board._state["players"]["P1"]["runningScore"] = 9007199254740990
        board.record(row, bus.session_id)
        assert board.snapshot()["players"]["P1"]["runningScore"] == 9007199254740990
        assert "supported range" in board.status()
    finally:
        board.stop()


def test_hit_directions_are_separate_and_misses_still_go_to_fabric(tmp_path):
    board = LiveScoreboard(FakeSender)
    bus = EventBus()
    fabric = []
    bus.set_live_sink(board.record)
    bus.set_delivery_sink(fabric.append)
    board.start(bus.session_id)
    try:
        for direction in ("Down-Left", "Down-Left", "Down-Right", "Up-Left", "Up-Right", "Center"):
            bus.record(event(bus, "arrow_hit", direction=direction))
        bus.record(event(bus, "arrow_hit", player="P2", direction="Center"))
        before = board.snapshot()
        bus.record(event(bus, "arrow_miss"))
        assert board.snapshot()["players"]["P1"]["misses"] == 1
        assert board.snapshot()["players"]["P1"]["hits"] == before["players"]["P1"]["hits"]
        assert fabric[-1].event_type == "arrow_miss"
        p1, p2 = before["players"]["P1"], before["players"]["P2"]
        assert p1["hitsByDirection"] == {
            "Up-Right": 1, "Up-Left": 1, "Down-Right": 1, "Down-Left": 2, "Center": 1,
        }
        assert p1["hits"] == 6
        assert p2["hitsByDirection"] == {
            "Up-Right": 0, "Up-Left": 0, "Down-Right": 0, "Down-Left": 0, "Center": 1,
        }
        assert p1["misses"] == 0
        bus.new_session()
        assert all(v == 0 for v in board.snapshot()["players"]["P1"]["hitsByDirection"].values())
        bus.record(event(bus, "arrow_hit", direction="invalid"))
        assert "invalid arrow direction" in board.status()
        assert board.snapshot()["players"]["P1"]["hits"] == 0
        bus.record(event(bus, "arrow_hit", direction="Up"))
        assert "invalid arrow direction" in board.status()
        assert board.snapshot()["players"]["P1"]["hits"] == 0
    finally:
        board.stop()


def test_lane_misses_are_independent_deduplicated_and_reset_with_the_game():
    board = LiveScoreboard(FakeSender)
    bus = EventBus()
    bus.set_live_sink(board.record)
    board.start(bus.session_id)
    try:
        miss = event(bus, "arrow_miss", direction="Down-Left")
        bus.record(miss)
        bus.record(miss)
        bus.record(event(bus, "arrow_miss", direction="Down-Left"))
        bus.record(event(bus, "arrow_miss", player="P2", direction="Up-Right"))
        bus.record(event(bus, "judgment_miss", direction=None))
        p1, p2 = board.snapshot()["players"].values()
        assert p1["misses"] == 3 and p1["unassignedMisses"] == 1
        assert p1["missesByDirection"] == {
            "Up-Right": 0, "Up-Left": 0, "Down-Right": 0, "Down-Left": 2, "Center": 0,
        }
        assert p2["missesByDirection"] == {
            "Up-Right": 1, "Up-Left": 0, "Down-Right": 0, "Down-Left": 0, "Center": 0,
        }
        assert all(value == 0 for value in p1["hitsByDirection"].values())
        bus.record(event(bus, "arrow_miss", direction="invalid"))
        assert board.snapshot()["players"]["P1"]["missesByDirection"] == p1["missesByDirection"]
        bus.new_session()
        for player in board.snapshot()["players"].values():
            assert all(value == 0 for value in player["missesByDirection"].values())
    finally:
        board.stop()


def test_sharing_is_opt_in_and_does_not_replace_fabric_sink(tmp_path):
    sender = FakeSender()
    board = LiveScoreboard(lambda: sender)
    bus = EventBus()
    fabric = []
    bus.set_delivery_sink(fabric.append)
    bus.set_live_sink(board.record)
    bus.record(event(bus))
    assert board.snapshot()["players"]["P1"]["combo"] is None
    board.start(bus.session_id)
    try:
        bus.record(event(bus, value=20))
        assert board.snapshot()["players"]["P1"]["combo"] == 20
        assert [event.event_type for event in fabric] == ["new_session", "combo", "combo"]
        bus.set_delivery_sink(None)
        bus.record(event(bus, value=21))
        assert board.snapshot()["players"]["P1"]["combo"] == 21
        assert len(fabric) == 3
    finally:
        board.stop()
    saved = sender.published[-1]
    assert not saved["active"]
    assert saved["players"]["P1"]["combo"] is None
    assert sender.closed
    assert not list(tmp_path.iterdir())


def test_unknown_direction_judgments_count_for_player_but_not_lane_or_response_metrics():
    board = LiveScoreboard(FakeSender)
    bus = EventBus()
    fabric = []
    bus.set_live_sink(board.record)
    bus.set_delivery_sink(fabric.append)
    board.start(bus.session_id)
    try:
        hit = event(bus, "judgment_hit", direction=None)
        bus.record(hit)
        bus.record(hit)
        bus.record(event(bus, "judgment_miss", direction=None))
        p1 = board.snapshot()["players"]["P1"]
        assert p1["hits"] == p1["unassignedHits"] == 1
        assert p1["misses"] == p1["unassignedMisses"] == 1
        assert sum(p1["hitsByDirection"].values()) == 0
        assert p1["responseSamples"] == 0
        assert p1["hitPace"] == .2
        assert board.snapshot()["players"]["P2"]["hits"] == 0
        assert [row.event_type for row in fabric].count("judgment_hit") == 2
        bus.record(event(bus, "judgment_hit", direction="Center"))
        assert "invalid arrow direction" in board.status()
        assert board.snapshot()["players"]["P1"]["hits"] == 1
        bus.new_session()
        assert board.snapshot()["players"]["P1"]["unassignedHits"] == 0
        assert board.snapshot()["players"]["P1"]["unassignedMisses"] == 0
    finally:
        board.stop()


def test_calibrated_game_end_resets_scoreboard_to_the_next_guid():
    import numpy as np

    from app import image_utils
    from app.analysis import AnalysisSession
    from app.models import Region

    board = LiveScoreboard(FakeSender)
    bus = EventBus()
    bus.set_live_sink(board.record)
    frame = np.full((20, 20, 3), 255, dtype=np.uint8)
    region = Region(
        "Results", "Unknown", "game_end", 0, 0, 20, 20,
        reference_images={"ended": [image_utils.encode_crop_to_base64_png(frame)]},
    )
    session = AnalysisSession(bus)
    session.set_regions([region])
    session.start()
    board.start(bus.session_id)
    try:
        bus.record(event(bus, "score_changed", 100))
        bus.record(event(bus, "arrow_hit"))
        previous = bus.session_id
        assert board.snapshot()["players"]["P1"]["hits"] == 1
        session.process_game_end_frame(frame, 10, 330)
        session.process_game_end_frame(frame, 11, 363)
        snapshot = board.snapshot()
        assert snapshot["sessionId"] == bus.session_id != previous
        assert snapshot["players"]["P1"]["hits"] == 0
        assert snapshot["players"]["P1"]["score"] is None
        assert bus.events[-1].payload["previous_session_id"] == previous
    finally:
        board.stop()


def test_dedup_out_of_order_events_corrections_and_players(tmp_path):
    board = LiveScoreboard(FakeSender)
    bus = EventBus()
    board.start(bus.session_id)
    try:
        old = event(bus, value=900)
        other = event(bus, "arrow_hit", player="P2")
        new = event(bus, value=106)
        for e in (new, old, other, other):
            board.record(e, bus.session_id)
        board.record(event(bus, "score_changed", 1000), bus.session_id)
        board.record(event(bus, "score_observed", 9999), bus.session_id)
        state = board.snapshot()
        assert state["players"]["P1"]["combo"] == 106
        assert state["players"]["P2"]["hits"] == 1
        assert state["players"]["P1"]["score"] == 1000
        assert state["players"]["P1"]["observedScore"] == 9999
        assert "payload" not in state
    finally:
        board.stop()


def test_new_session_resets_immediately_and_rejects_synthetic_or_old_session(tmp_path):
    board = LiveScoreboard(FakeSender)
    bus = EventBus()
    bus.set_live_sink(board.record)
    board.start(bus.session_id)
    try:
        old = event(bus)
        bus.record(old)
        bus.new_session()
        assert board.snapshot()["sessionId"] == bus.session_id
        assert board.snapshot()["players"]["P1"]["combo"] is None
        bus.record(old)
        bus.record(event(EventBus(source_mode="synthetic")))
        assert board.snapshot()["players"]["P1"]["combo"] is None
    finally:
        board.stop()


def test_publisher_refreshes_heartbeat_and_invalid_values_are_visible(tmp_path):
    sender = FakeSender()
    board = LiveScoreboard(lambda: sender)
    bus = EventBus()
    board.start(bus.session_id)
    try:
        deadline = time.monotonic() + 3
        while len(sender.published) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(sender.published) >= 2
        assert "connected" in board.status()
        board.record(event(bus, value=-1), bus.session_id)
        assert "invalid numeric" in board.status()
        assert board.snapshot()["players"]["P1"]["combo"] is None
    finally:
        board.stop()


def test_hit_pace_uses_source_time_and_ages_without_new_events(tmp_path):
    board = LiveScoreboard(FakeSender)
    bus = EventBus()
    board.start(bus.session_id)
    try:
        for timestamp, player in ((0, "P1"), (1000, "P1"), (2500, "P1"), (4999, "P2")):
            hit = event(bus, "arrow_hit", player=player)
            hit.video_time_ms = timestamp
            board.record(hit, bus.session_id)
            board.record(hit, bus.session_id)
        assert board.snapshot()["players"]["P1"]["hitPace"] == 0.6
        assert board.snapshot()["players"]["P2"]["hitPace"] == 0.2
        board.advance_video(5000)
        assert board.snapshot()["players"]["P1"]["hitPace"] == 0.4
        board.advance_video(6000)
        assert board.snapshot()["players"]["P1"]["hitPace"] == 0.2
        board.advance_video(7500)
        assert board.snapshot()["players"]["P1"]["hitPace"] == 0
        board.advance_video(9999)
        assert board.snapshot()["players"]["P2"]["hitPace"] == 0
        assert board.snapshot()["players"]["P1"]["hits"] == 3
        assert all(not hits for hits in board._recent_hits.values())
    finally:
        board.stop()


def test_late_delivery_does_not_create_hit_pace_spike(tmp_path):
    board = LiveScoreboard(FakeSender)
    bus = EventBus()
    board.start(bus.session_id)
    try:
        board.advance_video(10000)
        for timestamp in (1000, 9000, 8000, 5000, 9999):
            hit = event(bus, "arrow_hit")
            hit.video_time_ms = timestamp
            board.record(hit, bus.session_id)
        assert board.snapshot()["players"]["P1"]["hits"] == 5
        assert board.snapshot()["players"]["P1"]["hitPace"] == 0.6
        board.advance_video(14000)
        assert board.snapshot()["players"]["P1"]["hitPace"] == 0.2
    finally:
        board.stop()


def test_seek_and_new_session_clear_pace_but_pause_holds_it(tmp_path):
    board = LiveScoreboard(FakeSender)
    bus = EventBus()
    bus.set_live_sink(board.record)
    board.start(bus.session_id)
    try:
        bus.record(event(bus, "arrow_hit"))
        board.advance_video(17)
        first = board.snapshot()
        board.advance_video(17)
        assert board.snapshot() == first
        board.advance_video(30, is_seek=True)
        assert board.snapshot()["players"]["P1"]["hitPace"] == 0
        assert board.snapshot()["players"]["P1"]["hits"] == 1
        bus.record(event(bus, "arrow_hit"))
        board.advance_video(0)
        assert board.snapshot()["players"]["P1"]["hitPace"] == 0
        bus.record(event(bus, "arrow_hit"))
        bus.new_session()
        assert board.snapshot()["players"]["P1"]["hitPace"] == 0
        assert board.snapshot()["players"]["P1"]["hits"] == 0
    finally:
        board.stop()


def test_misses_and_numeric_updates_do_not_increase_pace(tmp_path):
    board = LiveScoreboard(FakeSender)
    bus = EventBus()
    board.start(bus.session_id)
    try:
        for kind in ("arrow_miss", "score_changed", "score_observed", "combo_changed"):
            board.record(event(bus, kind), bus.session_id)
        assert board.snapshot()["players"]["P1"]["hitPace"] == 0
    finally:
        board.stop()


def test_direct_publisher_reads_latest_state_after_slow_request_not_a_backlog():
    entered, release = threading.Event(), threading.Event()
    sender = FakeSender()

    def delayed(snapshot):
        sender.published.append(copy.deepcopy(snapshot))
        if len(sender.published) == 1:
            entered.set()
            assert release.wait(5)

    sender.publish = delayed
    board = LiveScoreboard(lambda: sender)
    bus = EventBus()
    board.start(bus.session_id)
    try:
        assert entered.wait(5)
        for value in range(100):
            board.record(event(bus, value=value), bus.session_id)
        release.set()
        deadline = time.monotonic() + 4
        while len(sender.published) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert sender.published[1]["players"]["P1"]["combo"] == 99
    finally:
        release.set()
        board.stop(5)


class ObservedPublisherWakeup:
    def __init__(self):
        self.event = threading.Event()
        self.waiting = threading.Event()

    def set(self):
        self.event.set()

    def clear(self):
        self.event.clear()

    def wait(self, timeout=None):
        self.waiting.set()
        return self.event.wait(timeout)


@pytest.mark.parametrize("change", ["combo", "judgement", "miss", "new-session", "pace"])
def test_changed_state_interrupts_idle_heartbeat_wait_immediately(change):
    sender = FakeSender()
    published_change = threading.Event()
    original = sender.publish

    def publish(snapshot):
        original(snapshot)
        if len(sender.published) >= 2:
            published_change.set()

    sender.publish = publish
    board = LiveScoreboard(lambda: sender)
    wakeup = ObservedPublisherWakeup()
    board._publish_wakeup = wakeup
    bus = EventBus()
    board.start(bus.session_id)
    try:
        assert wakeup.waiting.wait(5)
        if change == "combo":
            board.record(event(bus, value=42), bus.session_id)
        elif change in ("judgement", "miss"):
            board.record(judgement_event(bus, tier="miss" if change == "miss" else "perfect"), bus.session_id)
        elif change == "new-session":
            board.record(None, "next-game")
        else:
            with board._lock:
                board._recent_hits["P1"] = [17]
                board._state["players"]["P1"]["hitPace"] = .2
            board.advance_video(5017)
        # A fixed one-second post-send sleep cannot satisfy this deadline.
        assert published_change.wait(.75)
        assert sender.published[1]["revision"] > sender.published[0]["revision"]
        if change == "combo":
            assert sender.published[1]["players"]["P1"]["combo"] == 42
        if change == "new-session":
            assert sender.published[1]["sessionId"] == "next-game"
    finally:
        board.stop(5)


def test_update_during_send_is_published_without_sleep_or_overlapping_requests():
    sender = FakeSender()
    entered, release, next_send = threading.Event(), threading.Event(), threading.Event()
    active_requests = [0]
    original = sender.publish

    def delayed(snapshot):
        active_requests[0] += 1
        try:
            assert active_requests[0] == 1
            original(snapshot)
            if len(sender.published) == 1:
                entered.set()
                assert release.wait(5)
            else:
                next_send.set()
        finally:
            active_requests[0] -= 1

    sender.publish = delayed
    board = LiveScoreboard(lambda: sender)
    bus = EventBus()
    board.start(bus.session_id)
    try:
        assert entered.wait(5)
        for value in range(100):
            board.record(event(bus, value=value), bus.session_id)
        release.set()
        assert next_send.wait(.75)
        assert sender.published[1]["players"]["P1"]["combo"] == 99
        assert len(sender.published) == 2
    finally:
        release.set()
        board.stop(5)


def test_failed_publishes_keep_retry_backoff_even_when_new_data_arrives():
    sender = FakeSender()
    failed, retry = threading.Event(), threading.Event()
    attempts = []

    def fail(snapshot):
        attempts.append(snapshot)
        if len(attempts) > 1:
            retry.set()
        failed.set()
        raise ScoreboardPublishError("offline")

    sender.publish = fail
    board = LiveScoreboard(lambda: sender)
    bus = EventBus()
    board.start(bus.session_id)
    try:
        assert failed.wait(5)
        board.record(event(bus, value=42), bus.session_id)
        assert not retry.wait(.15)
        assert retry.wait(2)
        assert attempts[1]["players"]["P1"]["combo"] == 42
    finally:
        board.stop(5)


def test_stop_wakes_idle_publisher_and_clears_remote_scores_promptly():
    sender = FakeSender()
    board = LiveScoreboard(lambda: sender)
    wakeup = ObservedPublisherWakeup()
    board._publish_wakeup = wakeup
    board.start("game")
    assert wakeup.waiting.wait(5)
    board.stop(.75)
    try:
        assert board._thread is None
        assert sender.closed
        assert not sender.published[-1]["active"]
    finally:
        board.stop(5)


def test_activity_and_unchanged_pace_do_not_trigger_extra_publishes():
    sender = FakeSender()
    extra_publish = threading.Event()
    original = sender.publish

    def publish(snapshot):
        original(snapshot)
        if len(sender.published) > 1:
            extra_publish.set()

    sender.publish = publish
    board = LiveScoreboard(lambda: sender)
    wakeup = ObservedPublisherWakeup()
    board._publish_wakeup = wakeup
    bus = EventBus()
    board.start(bus.session_id)
    try:
        assert wakeup.waiting.wait(5)
        for timestamp in range(10):
            board.record(bus.make_event("arrow", {
                "direction": "Center", "state": "active", "evidence": "arrow_activity",
            }, timestamp, timestamp, "P1"), bus.session_id)
            board.advance_video(timestamp)
        assert not extra_publish.wait(.15)
        assert len(sender.published) == 1
    finally:
        board.stop(5)


def test_publish_failure_is_visible_then_retry_uses_new_state():
    sender = FakeSender()
    attempts = []

    def fail_once(snapshot):
        attempts.append(copy.deepcopy(snapshot))
        if len(attempts) == 1:
            raise ScoreboardPublishError("sensitive backend details")
        sender.published.append(snapshot)

    sender.publish = fail_once
    board = LiveScoreboard(lambda: sender)
    bus = EventBus()
    board.start(bus.session_id)
    try:
        deadline = time.monotonic() + 3
        while not attempts and time.monotonic() < deadline:
            time.sleep(0.01)
        assert "not acknowledged" in board.status()
        assert "sensitive" not in board.status()
        board.record(event(bus, value=65), bus.session_id)
        while len(attempts) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert attempts[1]["players"]["P1"]["combo"] == 65
    finally:
        board.stop(5)


def test_connection_failure_disables_sharing_with_explicit_error():
    def fail():
        raise ScoreboardPublishError("Sign-in is required.")
    board = LiveScoreboard(fail)
    board.start("session")
    deadline = time.monotonic() + 3
    while board.active and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not board.active
    assert "Sign-in is required" in board.status()
    board.stop(5)


def test_dedup_survives_more_than_four_thousand_events():
    board = LiveScoreboard(FakeSender)
    bus = EventBus()
    board.start(bus.session_id)
    try:
        first = event(bus, "arrow_hit")
        board.record(first, bus.session_id)
        for _ in range(4200):
            board.record(event(bus, "arrow_hit"), bus.session_id)
        board.record(first, bus.session_id)
        assert board.snapshot()["players"]["P1"]["hits"] == 4201
        old_session = bus.session_id
        board.record(None, "new-session")
        board.record(first, old_session)
        assert board.snapshot()["sessionId"] == "new-session"
        assert board.snapshot()["players"]["P1"]["hits"] == 0
    finally:
        board.stop(5)


def test_old_observation_cannot_replace_newer_accepted_score():
    board = LiveScoreboard(FakeSender)
    bus = EventBus()
    board.start(bus.session_id)
    try:
        observed = event(bus, "score_observed", 999)
        accepted = event(bus, "score_changed", 100)
        board.record(accepted, bus.session_id)
        board.record(observed, bus.session_id)
        assert board.snapshot()["players"]["P1"]["observedScore"] is None
    finally:
        board.stop(5)


def test_hit_from_before_seek_cannot_advance_current_video_clock():
    board = LiveScoreboard(FakeSender)
    bus = EventBus()
    board.start(bus.session_id)
    try:
        board.advance_video(1000, is_seek=True)
        late = event(bus, "arrow_hit")
        late.video_time_ms = 10000
        board.record(late, bus.session_id)
        assert board._video_time_ms == 1000
        assert board.snapshot()["players"]["P1"]["hitPace"] == 0
        current = event(bus, "arrow_hit")
        current.video_time_ms = 1000
        board.record(current, bus.session_id)
        assert board.snapshot()["players"]["P1"]["hitPace"] == 0.2
    finally:
        board.stop(5)


def test_stop_does_not_allow_new_worker_until_inflight_send_finishes():
    entered, release = threading.Event(), threading.Event()
    sender = FakeSender()
    original = sender.publish

    def delayed(snapshot):
        if snapshot["active"]:
            entered.set()
            assert release.wait(5)
        original(snapshot)

    sender.publish = delayed
    board = LiveScoreboard(lambda: sender)
    board.start("session")
    try:
        assert entered.wait(5)
        board.stop(timeout=0)
        with pytest.raises(ValueError, match="still stopping"):
            board.start("new-session")
        release.set()
        board.stop(5)
        assert not sender.published[-1]["active"]
        assert sender.closed
    finally:
        release.set()
        board.stop(5)


def timed_hit(bus, appearance, hit, player="P1"):
    result = event(bus, "arrow_hit", player=player)
    result.video_time_ms = hit
    result.payload.update(
        appearance_video_time_ms=appearance, hit_video_time_ms=hit, response_time_ms=hit - appearance,
    )
    return result


def test_response_averages_and_outcomes_are_per_player_deduplicated_and_reset():
    board = LiveScoreboard(FakeSender)
    bus = EventBus()
    board.start(bus.session_id)
    try:
        first = timed_hit(bus, 100, 300)
        second = timed_hit(bus, 500, 801)
        for item in (first, first, second, timed_hit(bus, 100, 200, "P2")):
            board.record(item, bus.session_id)
        miss = event(bus, "arrow_miss")
        for item in (miss, miss, event(bus, "arrow_hit")):
            board.record(item, bus.session_id)
        players = board.snapshot()["players"]
        assert players["P1"]["hits"] == 3
        assert players["P1"]["misses"] == 1
        assert players["P1"]["responseSamples"] == 2
        assert players["P1"]["averageResponseTimeMs"] == 250.5
        assert players["P2"]["misses"] == 0
        assert players["P2"]["averageResponseTimeMs"] == 100
        board.record(None, "next-game")
        assert board.snapshot()["players"]["P1"]["responseSamples"] == 0
        assert board.snapshot()["players"]["P1"]["averageResponseTimeMs"] is None
        assert board.snapshot()["players"]["P1"]["misses"] == 0
    finally:
        board.stop()


@pytest.mark.parametrize("payload", [
    {"response_time_ms": -1},
    {"response_time_ms": True},
    {"response_time_ms": 200.5},
    {"response_time_ms": 201},
    {"hit_video_time_ms": 301},
    {"appearance_video_time_ms": None},
])
def test_invalid_response_samples_are_reported_without_losing_real_hits(payload):
    board = LiveScoreboard(FakeSender)
    bus = EventBus()
    board.start(bus.session_id)
    try:
        hit = timed_hit(bus, 100, 300)
        hit.payload.update(payload)
        board.record(hit, bus.session_id)
        player = board.snapshot()["players"]["P1"]
        assert player["hits"] == 1
        assert player["averageResponseTimeMs"] is None
        assert player["responseSamples"] == 0
        assert "invalid response timing" in board.status()
    finally:
        board.stop()


def test_all_misses_are_real_outcomes_but_invalid_and_synthetic_misses_are_not():
    board = LiveScoreboard(FakeSender)
    bus = EventBus()
    board.start(bus.session_id)
    try:
        board.record(event(bus, "arrow_miss"), bus.session_id)
        board.record(event(bus, "arrow_miss", direction="invalid"), bus.session_id)
        synthetic = event(bus, "arrow_miss")
        synthetic.payload["is_test"] = True
        board.record(synthetic, bus.session_id)
        player = board.snapshot()["players"]["P1"]
        assert player["misses"] == 1
        assert player["hits"] == 0
        assert player["hitPace"] == 0
    finally:
        board.stop()
