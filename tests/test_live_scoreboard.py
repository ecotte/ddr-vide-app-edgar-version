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


def event(bus, kind="combo_changed", value=10, player="P1", direction="Up-Right"):
    return bus.make_event(kind, {
        "new_value": value, "observed_value": value, "direction": direction,
    }, 1, 17, player, 0.99)


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
        assert board.snapshot() == before
        assert fabric[-1].event_type == "arrow_miss"
        p1, p2 = before["players"]["P1"], before["players"]["P2"]
        assert p1["hitsByDirection"] == {
            "Up-Right": 1, "Up-Left": 1, "Down-Right": 1, "Down-Left": 2, "Center": 1,
        }
        assert p1["hits"] == 6
        assert p2["hitsByDirection"] == {
            "Up-Right": 0, "Up-Left": 0, "Down-Right": 0, "Down-Left": 0, "Center": 1,
        }
        assert "misses" not in p1
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
        assert [event.event_type for event in fabric] == ["new_session", "combo_changed", "combo_changed"]
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
