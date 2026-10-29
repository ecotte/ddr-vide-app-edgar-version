"""Private pipe transport to Rayfin's Node SDK; no local HTTP server or token files."""
import json
import queue
import shutil
import subprocess
import threading
from pathlib import Path

from app import config
from app.user_registration import registration_event

PREFIX = "__DDR_SCOREBOARD__"


class ScoreboardPublishError(Exception):
    pass


class ScoreboardConnectionError(ScoreboardPublishError):
    pass


class RayfinSender:
    def __init__(self, registration_sink=None):
        self._registration_sink = registration_sink
        self._acknowledged_registrations = []
        root = Path(config.APP_ROOT) / "scoreboard"
        worker = root / ".publisher" / "scripts" / "publish.js"
        node = shutil.which("node")
        if not node or not worker.exists():
            raise ScoreboardPublishError(
                "Install Node.js and run npm install followed by npm run build:publisher in scoreboard."
            )
        self._messages = queue.Queue()
        self._process = subprocess.Popen(
            [node, str(worker)], cwd=str(root),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._reader = threading.Thread(target=self._read, name="rayfin-status", daemon=True)
        self._reader.start()
        try:
            response = self._receive(180)
            if response.get("status") != "ready":
                raise ScoreboardPublishError(
                    "Rayfin sign-in failed. Complete Microsoft sign-in with the authorized publisher account."
                )
        except (ScoreboardPublishError, OSError):
            self.close()
            raise

    def _read(self):
        try:
            for line in self._process.stdout:
                if line.startswith(PREFIX):
                    try:
                        result = json.loads(line[len(PREFIX):])
                    except json.JSONDecodeError:
                        self._messages.put({"status": "protocol-error"})
                    else:
                        self._messages.put(result)
        finally:
            self._messages.put({"status": "exited"})

    def _receive(self, timeout):
        try:
            response = self._messages.get(timeout=timeout)
        except queue.Empty:
            raise ScoreboardConnectionError("Rayfin request timed out; no acknowledgment received.") from None
        if not isinstance(response, dict):
            raise ScoreboardConnectionError("Rayfin returned an invalid acknowledgment.")
        return response

    def publish(self, snapshot):
        if self._process.poll() is not None:
            raise ScoreboardConnectionError("Rayfin publisher stopped. Disable sharing and enable it again.")
        request = dict(snapshot)
        if self._acknowledged_registrations:
            request["acknowledgedRegistrations"] = self._acknowledged_registrations
        self._process.stdin.write(json.dumps(request, allow_nan=False) + "\n")
        self._process.stdin.flush()
        result = self._receive(90)
        if result.get("status") in ("exited", "protocol-error", "auth-failed"):
            raise ScoreboardConnectionError("Rayfin connection ended. Enable sharing again to reconnect.")
        if result.get("status") != "published" or result.get("revision") != snapshot["revision"]:
            if result.get("status") == "invalid":
                raise ScoreboardPublishError("Rayfin rejected invalid or older scoreboard state.")
            raise ScoreboardPublishError(
                "Rayfin did not acknowledge the update. Check connectivity, sign-in and app permissions."
            )
        self._acknowledged_registrations = []
        rows = result.get("registrations", [])
        if not isinstance(rows, list) or len(rows) > 25:
            raise ScoreboardPublishError("Rayfin returned invalid user registrations.")
        if rows and self._registration_sink is None:
            raise ScoreboardPublishError("Connect to Fabric to relay pending player registrations.")
        for row in rows:
            try:
                event = registration_event(row)
                self._registration_sink(event)
            except (ValueError, RuntimeError) as exc:
                raise ScoreboardPublishError(str(exc)) from None
            self._acknowledged_registrations.append(event.event_id)

    def close(self):
        if self._process.stdin and not self._process.stdin.closed:
            try:
                self._process.stdin.close()
            except OSError:
                pass  # The child may already have closed its pipe; exit status is checked below.
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)
        self._reader.join(timeout=2)
        self._process.stdout.close()
