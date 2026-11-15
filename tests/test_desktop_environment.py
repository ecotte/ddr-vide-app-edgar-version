import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import credential_store
from app.desktop_environment import apply_profile, fetch_connection, load_profile, merge_env, require_empty_outbox

PROFILE_PATH = Path(__file__).resolve().parents[1] / "deployments" / "ddr-dev.json"
CONNECTION = (
    "Endpoint=sb://test.servicebus.windows.net/;"
    "SharedAccessKeyName=test;SharedAccessKey=test-only;EntityPath=test-hub"
)


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.delenv("DDR_EVENTSTREAM_CONNECTION_STRING", raising=False)
    rayfin = tmp_path / "scoreboard" / "rayfin"
    rayfin.mkdir(parents=True)
    (rayfin / ".env").write_text("# keep this\nLOCAL_SETTING=preserved\nRAYFIN_PUBLIC_API_URL=old\n")
    (tmp_path / "scoreboard" / ".env.local").write_text("VITE_FABRIC_ITEM_ID=old\nOTHER=keep\n")
    (rayfin / ".deployments.json").write_text(json.dumps({
        "active": "original", "deployments": {"original": {"fabricItemId": "old"}},
    }))
    save = Mock()
    monkeypatch.setattr(credential_store, "save_connection", save)
    return tmp_path, rayfin, save


def test_checked_in_profile_is_public_and_valid():
    profile = load_profile(PROFILE_PATH)
    assert profile["name"] == "ddr-dev"
    assert profile["publishableKey"].startswith("pk-")


@pytest.mark.parametrize("change", [
    {"connectionString": CONNECTION},
    {"rayfinApiUrl": "https://example.com/"},
    {"workspaceId": "00000000-0000-0000-0000-000000000001"},
    {"publishableKey": "not-a-public-key"},
    {"portalUrl": "https://example.com"},
    {"name": "unsafe\nsetting"},
])
def test_profile_rejects_secrets_or_inconsistent_bindings(tmp_path, change):
    profile = json.loads(PROFILE_PATH.read_text())
    profile.update(change)
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile))
    with pytest.raises(ValueError):
        load_profile(path)


def test_switch_preserves_original_deployment_and_other_settings_without_writing_secret(local):
    root, rayfin, save = local
    profile = load_profile(PROFILE_PATH)
    apply_profile(root, profile, CONNECTION)
    registry = json.loads((rayfin / ".deployments.json").read_text())
    assert registry["active"] == "ddr-dev"
    assert registry["deployments"]["original"]["fabricItemId"] == "old"
    assert registry["deployments"]["ddr-dev"]["fabricWorkspaceId"] == profile["workspaceId"]
    assert "LOCAL_SETTING=preserved" in (rayfin / ".env").read_text()
    assert f"RAYFIN_PUBLIC_TENANT_ID={profile['tenantId']}" in (rayfin / ".env").read_text()
    vite = (root / "scoreboard" / ".env.local").read_text()
    assert f"VITE_FABRIC_WORKSPACE_ID={profile['workspaceId']}" in vite
    assert f"VITE_RAYFIN_API_URL={profile['rayfinApiUrl']}" in vite
    assert "OTHER=keep" in vite
    save.assert_called_once_with(CONNECTION)
    for path in (root / "scoreboard").rglob("*"):
        if path.is_file():
            assert CONNECTION not in path.read_text()
    assert not list(root.rglob("*.environment.tmp"))


def test_pending_events_block_switch_before_vault_or_config_changes(local):
    root, rayfin, save = local
    path = root / "data" / "eventstream-outbox.sqlite3"
    path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE pending (id INTEGER)")
        db.execute("INSERT INTO pending VALUES (1)")
    before = (rayfin / ".env").read_text()
    with pytest.raises(ValueError, match="Drain"):
        apply_profile(root, load_profile(PROFILE_PATH), CONNECTION)
    save.assert_not_called()
    assert (rayfin / ".env").read_text() == before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM pending").fetchone()[0] == 1


def test_vault_failure_restores_all_local_files(local):
    root, rayfin, save = local
    paths = [rayfin / ".env", rayfin / ".deployments.json", root / "scoreboard" / ".env.local"]
    before = {path: path.read_text() for path in paths}
    save.side_effect = credential_store.CredentialStorageError("Vault unavailable.")
    with pytest.raises(credential_store.CredentialStorageError):
        apply_profile(root, load_profile(PROFILE_PATH), CONNECTION)
    assert {path: path.read_text() for path in paths} == before


def test_environment_override_blocks_vault_switch(local, monkeypatch):
    root, _, save = local
    monkeypatch.setenv("DDR_EVENTSTREAM_CONNECTION_STRING", "launcher-override")
    with pytest.raises(ValueError, match="Unset"):
        apply_profile(root, load_profile(PROFILE_PATH), CONNECTION)
    save.assert_not_called()


def test_environment_merge_replaces_exported_and_duplicate_values():
    assert merge_env("export KEY=old\n KEY =duplicate\nOTHER=keep\n", {"KEY": "new"}) == "OTHER=keep\nKEY=new\n"


def test_empty_or_missing_outbox_is_allowed(tmp_path):
    path = tmp_path / "outbox.sqlite3"
    require_empty_outbox(path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE pending (id INTEGER)")
    require_empty_outbox(path)


def test_authentication_errors_do_not_expose_token_or_command_output(monkeypatch):
    monkeypatch.setattr("app.desktop_environment.shutil.which", lambda _: "az")
    monkeypatch.setattr("app.desktop_environment.subprocess.run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="sensitive-output", stderr="sensitive-error",
    ))
    with pytest.raises(RuntimeError, match="sign-in") as error:
        fetch_connection(load_profile(PROFILE_PATH))
    assert "sensitive" not in str(error.value)
