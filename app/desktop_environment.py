"""Select public deployment settings; keep Eventstream credentials in Windows vault."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

from app import config, credential_store
from app.eventstream import endpoint_identity

FIELDS = {
    "name", "tenantId", "workspaceId", "rayfinItemId", "rayfinApiUrl",
    "publishableKey", "portalUrl", "hostingUrl", "eventstreamId", "eventstreamSourceId",
}


def load_profile(path: Path) -> dict[str, str]:
    profile = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(profile, dict) or set(profile) != FIELDS:
        raise ValueError("Deployment profile must contain only the documented public settings.")
    if any(not isinstance(value, str) or not value or "\n" in value or "\r" in value for value in profile.values()):
        raise ValueError("Deployment settings must be nonempty single-line strings.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", profile["name"]):
        raise ValueError("Deployment name must use lowercase letters, digits and hyphens.")
    for key in ("tenantId", "workspaceId", "rayfinItemId", "eventstreamId", "eventstreamSourceId"):
        if str(UUID(profile[key])) != profile[key]:
            raise ValueError("Deployment IDs must be canonical GUIDs.")
    api = urlsplit(profile["rayfinApiUrl"])
    expected_path = (
        f"/v1/tenants/{profile['tenantId']}/workspaces/{profile['workspaceId']}"
        f"/appbackends/{profile['rayfinItemId']}/BaaSService/"
    )
    if (api.scheme != "https" or not api.hostname or not api.hostname.endswith(".pbidedicated.windows.net")
            or api.username or api.password or api.port or api.query or api.fragment or api.path != expected_path):
        raise ValueError("Rayfin API URL must match the deployment tenant, workspace and item.")
    host = urlsplit(profile["hostingUrl"])
    if (host.scheme != "https" or not host.hostname or not host.hostname.endswith(".fabricapps.net")
            or host.username or host.password or host.port or host.query or host.fragment or host.path not in ("", "/")):
        raise ValueError("Hosting URL must be a Fabric application origin.")
    if profile["portalUrl"] != "https://app.fabric.microsoft.com":
        raise ValueError("This profile must use the production Fabric portal.")
    if not re.fullmatch(r"pk-[A-Za-z0-9_-]+", profile["publishableKey"]):
        raise ValueError("Only a public Rayfin publishable key belongs in a deployment profile.")
    return profile


def require_empty_outbox(path: Path) -> None:
    if path.exists():
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            if db.execute("SELECT COUNT(*) FROM pending").fetchone()[0]:
                raise ValueError("Drain the old destination's pending events before switching environments.")


def fetch_connection(profile: dict[str, str]) -> str:
    executable = shutil.which("az")
    if not executable:
        raise RuntimeError("Azure CLI is required to retrieve the destination connection securely.")
    result = subprocess.run(
        [executable, "account", "get-access-token", "--tenant", profile["tenantId"],
         "--resource", "https://api.fabric.microsoft.com", "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("Azure CLI sign-in to the destination tenant is required.")
    url = (
        f"https://api.fabric.microsoft.com/v1/workspaces/{profile['workspaceId']}"
        f"/eventstreams/{profile['eventstreamId']}/sources/{profile['eventstreamSourceId']}/connection"
    )
    request = Request(url, headers={"Authorization": "Bearer " + result.stdout.strip()})
    try:
        with urlopen(request, timeout=30) as response:
            details = json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"Fabric denied the source-connection request (HTTP {error.code}).") from None
    except URLError:
        raise RuntimeError("Cannot reach Fabric to retrieve the destination connection.") from None
    connection = details.get("accessKeys", {}).get("primaryConnectionString")
    if not isinstance(connection, str):
        raise ValueError("Fabric did not return a custom-endpoint connection.")
    endpoint_identity(connection)
    return connection


def merge_env(previous: str, values: dict[str, str]) -> str:
    lines = []
    for line in previous.splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if not match or match[1] not in values:
            lines.append(line)
    return "\n".join([*lines, *(f"{key}={value}" for key, value in values.items()), ""])


def _write(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".environment.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def apply_profile(root: Path, profile: dict[str, str], connection: str) -> None:
    if os.environ.get("DDR_EVENTSTREAM_CONNECTION_STRING"):
        raise ValueError("Unset DDR_EVENTSTREAM_CONNECTION_STRING before using a saved environment.")
    endpoint_identity(connection)
    require_empty_outbox(root / "data" / "eventstream-outbox.sqlite3")
    rayfin = root / "scoreboard" / "rayfin"
    env_path = rayfin / ".env"
    vite_path = root / "scoreboard" / ".env.local"
    registry_path = rayfin / ".deployments.json"
    paths = (env_path, vite_path, registry_path)
    previous = {path: path.read_text(encoding="utf-8-sig") if path.exists() else None for path in paths}
    registry = json.loads(previous[registry_path] or '{"deployments":{}}')
    registry["deployments"][profile["name"]] = {
        "fabricItemId": profile["rayfinItemId"], "itemName": "DDR Scoreboard",
        "fabricApiUrl": profile["rayfinApiUrl"], "fabricWorkspaceId": profile["workspaceId"],
        "fabricTenantId": profile["tenantId"], "fabricPortalUrl": profile["portalUrl"],
        "publishableKey": profile["publishableKey"], "hostingUrl": profile["hostingUrl"],
    }
    registry["active"] = profile["name"]
    public = {
        "RAYFIN_PUBLIC_API_URL": profile["rayfinApiUrl"],
        "RAYFIN_PUBLIC_PUBLISHABLE_KEY": profile["publishableKey"],
        "RAYFIN_PUBLIC_ITEM_ID": profile["rayfinItemId"],
        "RAYFIN_PUBLIC_WORKSPACE_ID": profile["workspaceId"],
        "RAYFIN_PUBLIC_TENANT_ID": profile["tenantId"],
        "RAYFIN_PUBLIC_PORTAL_URL": profile["portalUrl"],
    }
    vite = {key.replace("RAYFIN_PUBLIC_", "VITE_RAYFIN_"): value for key, value in public.items()}
    for name in ("ITEM_ID", "WORKSPACE_ID", "TENANT_ID", "PORTAL_URL"):
        vite["VITE_FABRIC_" + name] = vite.pop("VITE_RAYFIN_" + name)
    updates = {
        env_path: merge_env(previous[env_path] or "", {
            **public, "RAYFIN_FABRIC_API_URL": "https://api.fabric.microsoft.com",
            "RAYFIN_FABRIC_PORTAL_URL": profile["portalUrl"],
        }),
        vite_path: merge_env(previous[vite_path] or "", vite),
        registry_path: json.dumps(registry, indent=2) + "\n",
    }
    try:
        for path, text in updates.items():
            _write(path, text)
        credential_store.save_connection(connection)
    except (OSError, credential_store.CredentialStorageError):
        for path, text in previous.items():
            if text is None:
                path.unlink(missing_ok=True)
            else:
                _write(path, text)
        raise


def main() -> None:
    root = Path(config.APP_ROOT)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=root / "deployments" / "ddr-dev.json")
    parser.add_argument("--apply", action="store_true", help="Save local settings and the Windows vault credential.")
    parser.add_argument("--publishers-stopped", action="store_true", help="Confirm both desktop publishers are off.")
    args = parser.parse_args()
    try:
        profile = load_profile(args.profile)
        require_empty_outbox(root / "data" / "eventstream-outbox.sqlite3")
        if args.apply:
            if not args.publishers_stopped:
                raise ValueError("Stop Fabric publishing and live scoreboard sharing, then pass --publishers-stopped.")
            apply_profile(root, profile, fetch_connection(profile))
            print(f"Desktop settings saved for {profile['name']}. Reconnect both publishers to use them.")
        else:
            print(f"Validated {profile['name']}; no settings changed. Stop both publishers before applying.")
    except (ValueError, OSError, sqlite3.Error, RuntimeError, credential_store.CredentialStorageError) as error:
        parser.exit(1, f"Environment switch failed: {error}\n")


if __name__ == "__main__":
    main()
