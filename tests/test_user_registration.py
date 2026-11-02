import sqlite3
from unittest.mock import Mock

import pytest

from app.eventstream import EventstreamPublisher, Outbox
from app.user_registration import registration_event

ROW = {
    "id": "045bc1ea-322c-4ae7-80e1-06e725893e8e",
    "gameSessionId": "c7510905-589f-4d05-bd14-cd718e288a49",
    "username": "SillyEventhouse1234",
    "createdAt": "2026-10-29T08:00:00Z",
}


@pytest.mark.parametrize("field,value", [
    ("id", "invalid"), ("gameSessionId", None), ("username", "<script>"),
    ("username", "SILLYEventhouse1234"), ("username", "sillyEVENTHOUSE1234"),
    ("username", "sillyEventhouse123"), ("username", "silly Eventhouse1234"),
    ("createdAt", "2026-10-29"), ("createdAt", "bad"),
])
def test_invalid_registration_is_not_an_event(field, value):
    with pytest.raises(ValueError, match="Invalid user registration"):
        registration_event({**ROW, field: value})


@pytest.mark.parametrize("username", ["SillyEventhouse1234", "sillyEventhouse1234", "sillyeventhouse1234"])
def test_registration_preserves_capitalized_and_legacy_names(username):
    event = registration_event({**ROW, "username": username})
    assert event.payload["username"] == username


@pytest.mark.parametrize("username", ["YaelTheChampion", "EdgarIsGoingDown", "The Dancing Queen"])
def test_special_registration_is_explicit_and_needs_no_pin(username):
    row = {**ROW, "username": username, "registrationType": "special"}
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
    })
    assert event.player == player


def test_invalid_station_is_not_published():
    with pytest.raises(ValueError):
        registration_event({**ROW, "player": "P3"})


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
