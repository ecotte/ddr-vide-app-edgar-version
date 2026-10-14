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
