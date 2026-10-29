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
        assert "pin" not in event.payload
        sender.publish({"revision": 4})
        requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        assert "acknowledgedRegistrations" not in requests[0]
        assert requests[1]["acknowledgedRegistrations"] == [REGISTRATION["id"]]
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
    row = {**REGISTRATION, "username": "YaelTheChampion", "registrationType": "special"}
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
