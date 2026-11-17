import json
import sqlite3
from unittest.mock import Mock

import pytest

from app.eventstream import EventstreamPublisher, Outbox
from app.user_registration import registration_event

ROW = {
    "id": "045bc1ea-322c-4ae7-80e1-06e725893e8e",
    "gameSessionId": "c7510905-589f-4d05-bd14-cd718e288a49",
    "username": "SillyEventhouse1234",
    "pin": "001234",
    "createdAt": "2026-10-29T08:00:00Z",
}


@pytest.mark.parametrize("field,value", [
    ("id", "invalid"), ("gameSessionId", None), ("username", "<script>"),
    ("username", "SILLYEventhouse1234"), ("username", "sillyEVENTHOUSE1234"),
    ("username", "sillyEventhouse123"), ("username", "silly Eventhouse1234"),
    ("createdAt", "2026-10-29"), ("createdAt", "bad"),
    ("createdAt", "2026-11-16T16:40:58"),
    ("createdAt", "0001-01-01T00:00:00+14:00"),
    ("createdAt", "9999-12-31T23:59:59-12:00"),
])
def test_invalid_registration_is_not_an_event(field, value):
    with pytest.raises(ValueError, match="Invalid user registration"):
        registration_event({**ROW, field: value})


@pytest.mark.parametrize("username", ["SillyEventhouse1234", "sillyEventhouse1234", "sillyeventhouse1234"])
def test_registration_preserves_capitalized_and_legacy_names(username):
    event = registration_event({**ROW, "username": username})
    assert event.payload["username"] == username


@pytest.mark.parametrize("timestamp", [
    "2026-11-16T16:40:58.123456-08:00",
    "2026-11-17T02:40:58.123456+02:00",
    "2026-11-17T06:10:58.123456+05:30",
    "2026-11-17T00:41:58.123456+00:01",
    "2026-11-17T00:40:58.123456+00:00",
    "2026-11-17T00:40:58.123456Z",
])
@pytest.mark.parametrize("registration_type", ["player", "special"])
def test_registration_timestamps_are_normalized_to_utc_before_delivery(tmp_path, timestamp, registration_type):
    row = {**ROW, "createdAt": timestamp, "registrationType": registration_type}
    if registration_type == "special":
        row.update(username="YaelTheChampion", pin=None)
    original = dict(row)
    event = registration_event(row)
    assert event.observed_at_utc == "2026-11-17T00:40:58.123456Z"
    assert row == original
    outbox = Outbox(str(tmp_path / "outbox.sqlite3"), "test")
    try:
        outbox.enqueue(event)
        assert json.loads(outbox.next_event()[2])["observed_at_utc"] == "2026-11-17T00:40:58.123456Z"
    finally:
        outbox.close()


@pytest.mark.parametrize("username", ["YaelTheChampion", "EdgarIsGoingDown", "The Dancing Queen"])
def test_special_registration_is_explicit_and_needs_no_pin(username):
    row = {**ROW, "username": username, "registrationType": "special", "pin": None}
    event = registration_event(row)
    assert event.payload["username"] == username
    assert event.payload["registration_type"] == "special"
    assert "pin" not in event.payload
    with pytest.raises(ValueError):
        registration_event({**row, "registrationType": "player"})


@pytest.mark.parametrize("kind,username", [
    ("special", "UnknownPlayer"), ("administrator", "YaelTheChampion"),
])
def test_unknown_special_names_and_registration_types_are_rejected(kind, username):
    with pytest.raises(ValueError):
        registration_event({**ROW, "username": username, "registrationType": kind})


@pytest.mark.parametrize("player", [None, "P1", "P2"])
@pytest.mark.parametrize("kind,username", [
    ("player", "SillyEventhouse1234"), ("special", "YaelTheChampion"),
])
def test_registration_event_keeps_its_station(player, kind, username):
    event = registration_event({
        **ROW, "username": username, "registrationType": kind, "player": player,
        "pin": ROW["pin"] if kind == "player" else None,
    })
    assert event.player == player


def test_invalid_station_is_not_published():
    with pytest.raises(ValueError):
        registration_event({**ROW, "player": "P3"})


@pytest.mark.parametrize("excluded", [False, True])
def test_leaderboard_exclusion_is_preserved_in_durable_registration_event(tmp_path, excluded):
    event = registration_event({**ROW, "player": "P1", "excludeFromLeaderboard": excluded})
    assert event.player == "P1"
    assert event.payload["exclude_from_leaderboard"] is excluded
    outbox = Outbox(str(tmp_path / "outbox.sqlite3"), "test")
    try:
        outbox.enqueue(event)
        assert json.loads(outbox.next_event()[2])["payload"]["exclude_from_leaderboard"] is excluded
    finally:
        outbox.close()


@pytest.mark.parametrize("excluded", [None, False])
def test_legacy_registration_is_not_excluded(excluded):
    assert registration_event(ROW).payload["exclude_from_leaderboard"] is False
    assert registration_event({**ROW, "excludeFromLeaderboard": excluded}).payload["exclude_from_leaderboard"] is False


@pytest.mark.parametrize("excluded", ["false", "true", 0, 1, [], {}])
def test_invalid_leaderboard_exclusion_is_not_published(excluded):
    with pytest.raises(ValueError, match="Invalid user registration"):
        registration_event({**ROW, "excludeFromLeaderboard": excluded})


@pytest.mark.parametrize("difficulty", [0, 3, 4])
@pytest.mark.parametrize("kind", ["player", "special"])
def test_numeric_difficulty_is_preserved_in_the_signin_event_and_durable_outbox(tmp_path, difficulty, kind):
    row = {
        **ROW, "player": "P2", "difficulty": difficulty,
        "excludeFromLeaderboard": difficulty == 0, "registrationType": kind,
    }
    if kind == "special":
        row.update(username="The Dancing Queen", pin=None)
    event = registration_event(row)
    assert event.payload["difficulty"] == difficulty
    assert type(event.payload["difficulty"]) is int
    assert event.payload["exclude_from_leaderboard"] is (difficulty == 0)
    outbox = Outbox(str(tmp_path / "outbox.sqlite3"), "test")
    try:
        outbox.enqueue(event)
        wire = json.loads(outbox.next_event()[2])
        assert wire["payload"]["difficulty"] == difficulty
        assert wire["player"] == "P2"
    finally:
        outbox.close()


@pytest.mark.parametrize("difficulty", [-1, 1, 2, 5, "3", "4", True, False, 3.0, [], {}])
def test_invalid_difficulty_is_rejected_without_publishing(difficulty):
    with pytest.raises(ValueError, match="Invalid user registration"):
        registration_event({**ROW, "difficulty": difficulty})


@pytest.mark.parametrize("difficulty,excluded", [(0, False), (3, True), (4, True)])
def test_conflicting_difficulty_and_ranking_flag_is_rejected(difficulty, excluded):
    with pytest.raises(ValueError, match="Invalid user registration"):
        registration_event({**ROW, "difficulty": difficulty, "excludeFromLeaderboard": excluded})


@pytest.mark.parametrize("excluded", [False, True])
def test_legacy_registration_does_not_invent_a_normal_difficulty(excluded):
    event = registration_event({**ROW, "excludeFromLeaderboard": excluded})
    assert event.payload["difficulty"] == (0 if excluded else None)


def test_registration_is_durable_and_duplicates_pending_are_safe(tmp_path):
    publisher = EventstreamPublisher(str(tmp_path / "outbox.sqlite3"))
    event = registration_event(ROW)
    with pytest.raises(RuntimeError, match="Connect to Fabric"):
        publisher.enqueue_registration(event)
    publisher._outbox = Outbox(str(tmp_path / "outbox.sqlite3"), "test")
    try:
        publisher.enqueue_registration(event)
        publisher.enqueue_registration(event)
        assert publisher._outbox.count() == 1
        assert publisher._outbox.next_event()[2].find("user.register") > 0
    finally:
        publisher.close()


def test_failed_registration_queue_write_is_not_acknowledged(tmp_path):
    publisher = EventstreamPublisher(str(tmp_path / "outbox.sqlite3"))
    publisher._outbox = Mock()
    publisher._outbox.enqueue.side_effect = sqlite3.OperationalError("disk unavailable")
    with pytest.raises(RuntimeError, match="remains pending"):
        publisher.enqueue_registration(registration_event(ROW))


def test_pin_preserves_leading_zeroes_in_durable_event(tmp_path):
    event = registration_event(ROW)
    assert event.payload["pin"] == "001234"
    outbox = Outbox(str(tmp_path / "outbox.sqlite3"), "test")
    try:
        outbox.enqueue(event)
        assert json.loads(outbox.next_event()[2])["payload"]["pin"] == "001234"
    finally:
        outbox.close()


@pytest.mark.parametrize("pin", [None, "", 123456, "12345", "1234567", "12a456", "１２３４５６", "123456\n"])
def test_invalid_pin_is_not_published_or_exposed_in_error(pin):
    with pytest.raises(ValueError) as error:
        registration_event({**ROW, "pin": pin})
    assert str(error.value) == "Invalid user registration received from Rayfin."


def test_missing_pin_is_rejected_rather_than_sending_incomplete_registration():
    row = {key: value for key, value in ROW.items() if key != "pin"}
    with pytest.raises(ValueError, match="Invalid user registration"):
        registration_event(row)
