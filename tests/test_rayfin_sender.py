import io
import json
from unittest.mock import Mock

import pytest

from app.rayfin_sender import PREFIX, RayfinSender, ScoreboardPublishError, ScoreboardConnectionError


def fake_process(monkeypatch, tmp_path, messages):
    worker = tmp_path / "scoreboard" / ".publisher" / "scripts" / "publish.js"
    worker.parent.mkdir(parents=True)
    worker.write_text("")
    monkeypatch.setattr("app.rayfin_sender.config.APP_ROOT", str(tmp_path))
    monkeypatch.setattr("app.rayfin_sender.shutil.which", lambda _: "node.exe")
    process = Mock()
    process.stdin = io.StringIO()
    process.stdout = io.StringIO(
        "SDK diagnostic line - not protocol\n" + "\n".join(
            PREFIX + json.dumps(message) for message in messages
        ) + "\n",
    )
    process.poll.return_value = None
    factory = Mock(return_value=process)
    monkeypatch.setattr("app.rayfin_sender.subprocess.Popen", factory)
    return process, factory


def test_private_pipe_acknowledgment_and_no_server_or_token_arguments(monkeypatch, tmp_path):
    process, factory = fake_process(monkeypatch, tmp_path, [
        {"status": "ready"}, {"status": "published", "revision": 3},
    ])
    sender = RayfinSender()
    snapshot = {"active": True, "revision": 3}
    sender.publish(snapshot)
    assert json.loads(process.stdin.getvalue()) == snapshot
    args = factory.call_args.args[0]
    assert args[0] == "node.exe"
    assert args[1].endswith("publish.js")
    assert len(args) == 2
    sender.close()
    assert process.stdin.closed
    assert process.stdout.closed


@pytest.mark.parametrize("message", [
    {"status": "failed", "details": "secret"},
    {"status": "published", "revision": 2},
    {"status": "invalid"},
])
def test_no_success_without_matching_revision(monkeypatch, tmp_path, message):
    _, _ = fake_process(monkeypatch, tmp_path, [{"status": "ready"}, message])
    sender = RayfinSender()
    try:
        with pytest.raises(ScoreboardPublishError) as error:
            sender.publish({"revision": 3})
        assert "secret" not in str(error.value)
    finally:
        sender.close()


def test_child_exit_during_request_is_reported(monkeypatch, tmp_path):
    fake_process(monkeypatch, tmp_path, [{"status": "ready"}])
    sender = RayfinSender()
    try:
        with pytest.raises(ScoreboardConnectionError, match="connection ended"):
            sender.publish({"revision": 3})
    finally:
        sender.close()


def test_failed_sign_in_closes_worker(monkeypatch, tmp_path):
    process, _ = fake_process(monkeypatch, tmp_path, [{"status": "auth-failed"}])
    with pytest.raises(ScoreboardPublishError, match="sign-in failed"):
        RayfinSender()
    assert process.stdin.closed
    assert process.stdout.closed


REGISTRATION = {
    "id": "045bc1ea-322c-4ae7-80e1-06e725893e8e",
    "gameSessionId": "c7510905-589f-4d05-bd14-cd718e288a49",
    "username": "SillyEventhouse1234",
    "pin": "001234",
    "createdAt": "2026-10-29T08:00:00Z",
}


def test_registration_ack_only_after_durable_enqueue(monkeypatch, tmp_path):
    process, _ = fake_process(monkeypatch, tmp_path, [
        {"status": "ready"},
        {"status": "published", "revision": 3, "registrations": [REGISTRATION]},
        {"status": "published", "revision": 4, "registrations": []},
    ])
    sink = Mock()
    sender = RayfinSender(registration_sink=sink)
    try:
        sender.publish({"revision": 3})
        event = sink.call_args.args[0]
        assert event.event_type == "user.register"
        assert event.event_id == REGISTRATION["id"]
        assert event.session_id == REGISTRATION["gameSessionId"]
        assert event.payload["pin"] == "001234"
        sender.publish({"revision": 4})
        requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        assert "acknowledgedRegistrations" not in requests[0]
        assert requests[1]["acknowledgedRegistrations"] == [REGISTRATION["id"]]
        assert "001234" not in process.stdin.getvalue()
    finally:
        sender.close()


def test_registration_enqueue_failure_leaves_sql_pending(monkeypatch, tmp_path):
    process, _ = fake_process(monkeypatch, tmp_path, [
        {"status": "ready"},
        {"status": "published", "revision": 3, "registrations": [REGISTRATION]},
        {"status": "published", "revision": 4, "registrations": []},
    ])
    sender = RayfinSender(registration_sink=Mock(side_effect=RuntimeError("Connect to Fabric")))
    try:
        with pytest.raises(ScoreboardPublishError, match="Connect to Fabric"):
            sender.publish({"revision": 3})
        sender.publish({"revision": 4})
        requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        assert all("acknowledgedRegistrations" not in request for request in requests)
    finally:
        sender.close()


def test_special_registration_acknowledgment_uses_separate_sql_entity(monkeypatch, tmp_path):
    row = {**REGISTRATION, "username": "YaelTheChampion", "registrationType": "special", "pin": None}
    process, _ = fake_process(monkeypatch, tmp_path, [
        {"status": "ready"},
        {"status": "published", "revision": 3, "registrations": [row]},
        {"status": "published", "revision": 4, "registrations": []},
    ])
    sender = RayfinSender(registration_sink=Mock())
    try:
        sender.publish({"revision": 3})
        sender.publish({"revision": 4})
        request = json.loads(process.stdin.getvalue().splitlines()[1])
        assert request["acknowledgedSpecialRegistrations"] == [row["id"]]
        assert "acknowledgedRegistrations" not in request
    finally:
        sender.close()


def test_current_player_slots_are_acknowledged_only_after_durable_enqueue(monkeypatch, tmp_path):
    row = {**REGISTRATION, "player": "P1", "registrationType": "player", "relaySource": "slot"}
    process, _ = fake_process(monkeypatch, tmp_path, [
        {"status": "ready"},
        {"status": "published", "revision": 3, "registrations": [row]},
        {"status": "published", "revision": 4, "registrations": []},
    ])
    sink = Mock()
    sender = RayfinSender(registration_sink=sink)
    try:
        sender.publish({"revision": 3})
        assert sink.call_args.args[0].player == "P1"
        sender.publish({"revision": 4})
        request = json.loads(process.stdin.getvalue().splitlines()[1])
        assert request["acknowledgedGamePlayerRegistrations"] == [row["id"]]
        assert "acknowledgedRegistrations" not in request
    finally:
        sender.close()


def test_bowser_does_not_block_the_following_player_and_both_keep_all_registration_fields(monkeypatch, tmp_path):
    bowser = {
        **REGISTRATION, "player": "P1", "username": "Bowser", "registrationType": "special",
        "relaySource": "slot", "pin": None, "difficulty": 4, "excludeFromLeaderboard": False,
    }
    player = {
        **REGISTRATION, "id": "a81f58cf-407f-4074-ab48-e4f37732672e", "player": "P2",
        "registrationType": "player", "relaySource": "slot", "difficulty": 0, "excludeFromLeaderboard": True,
    }
    process, _ = fake_process(monkeypatch, tmp_path, [
        {"status": "ready"},
        {"status": "published", "revision": 3, "registrations": [bowser, player]},
        {"status": "published", "revision": 4, "registrations": []},
    ])
    sink = Mock()
    sender = RayfinSender(registration_sink=sink)
    try:
        sender.publish({"revision": 3})
        assert sink.call_count == 2
        for call, row in zip(sink.call_args_list, [bowser, player]):
            event = call.args[0]
            assert event.event_id == row["id"]
            assert event.session_id == row["gameSessionId"]
            assert event.player == row["player"]
            assert event.observed_at_utc == row["createdAt"]
            assert event.payload == {
                "user_session_id": row["id"], "game_session_id": row["gameSessionId"],
                "username": row["username"], "registration_type": row["registrationType"],
                "difficulty": row["difficulty"], "exclude_from_leaderboard": row["excludeFromLeaderboard"],
                **({"pin": row["pin"]} if row["registrationType"] == "player" else {}),
            }
        sender.publish({"revision": 4})
        request = json.loads(process.stdin.getvalue().splitlines()[1])
        assert request["acknowledgedGamePlayerRegistrations"] == [bowser["id"], player["id"]]
    finally:
        sender.close()


def test_invalid_pin_is_not_acknowledged_or_exposed(monkeypatch, tmp_path):
    row = {**REGISTRATION, "pin": "bad-pin"}
    process, _ = fake_process(monkeypatch, tmp_path, [
        {"status": "ready"},
        {"status": "published", "revision": 3, "registrations": [row]},
        {"status": "published", "revision": 4, "registrations": []},
    ])
    sink = Mock()
    sender = RayfinSender(registration_sink=sink)
    try:
        with pytest.raises(ScoreboardPublishError) as error:
            sender.publish({"revision": 3})
        assert "bad-pin" not in str(error.value)
        assert "data is invalid or unsupported" in str(error.value)
        sink.assert_not_called()
        sender.publish({"revision": 4})
        requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        assert all("acknowledgedRegistrations" not in request for request in requests)
    finally:
        sender.close()
