"""Opt-in Eventstream delivery with a durable, at-least-once outbox."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.parse import urlsplit

from azure.eventhub import EventData, EventHubProducerClient, TransportType
from azure.eventhub.exceptions import EventHubError

from app.models import Event


def endpoint_identity(connection_string: str) -> str:
    """Validate without including credentials in error messages or storage."""
    try:
        fields = {}
        for part in connection_string.strip().split(";"):
            if part:
                name, value = part.split("=", 1)
                if name in fields:
                    raise ValueError
                fields[name] = value
        endpoint = urlsplit(fields["Endpoint"])
        hub = fields["EntityPath"]
        if (
            endpoint.scheme != "sb" or not endpoint.hostname
            or not endpoint.hostname.endswith(".servicebus.windows.net")
            or endpoint.username or endpoint.password or endpoint.port
            or endpoint.path not in ("", "/") or endpoint.query or endpoint.fragment
            or not hub or any(c in hub for c in "/?#; \r\n")
            or not fields["SharedAccessKey"] or not fields["SharedAccessKeyName"]
        ):
            raise ValueError
    except (KeyError, ValueError):
        raise ValueError(
            "Enter a complete Fabric Event Hub SAS connection string, including EntityPath."
        ) from None
    return f"{endpoint.hostname}/{hub}"


class Outbox:
    def __init__(self, path: str, endpoint: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=5)
        try:
            with self._db:
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS destination (id INTEGER PRIMARY KEY, endpoint TEXT NOT NULL)"
                )
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS pending ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, "
                    "session_id TEXT NOT NULL, body TEXT NOT NULL)"
                )
                self._db.execute("BEGIN IMMEDIATE")
                previous = self._db.execute("SELECT endpoint FROM destination WHERE id=1").fetchone()
                if previous and previous[0] != endpoint and self.count():
                    raise ValueError(
                        "Unsent events belong to a different endpoint. Reconnect to that endpoint "
                        "and drain the queue before changing destinations."
                    )
                self._db.execute(
                    "INSERT INTO destination VALUES (1, ?) "
                    "ON CONFLICT(id) DO UPDATE SET endpoint=excluded.endpoint", (endpoint,),
                )
        except (sqlite3.Error, ValueError):
            self._db.close()
            raise

    def enqueue(self, event: Event) -> None:
        body = json.dumps(event.to_dict(), allow_nan=False)
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO pending (event_id, session_id, body) VALUES (?, ?, ?) "
                "ON CONFLICT(event_id) DO NOTHING",
                (event.event_id, event.session_id, body),
            )

    def next_event(self) -> tuple[int, str, str] | None:
        with self._lock:
            return self._db.execute(
                "SELECT id, session_id, body FROM pending ORDER BY id LIMIT 1"
            ).fetchone()

    def acknowledge(self, row_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM pending WHERE id=?", (row_id,))

    def next_events(self, limit: int = 100) -> list[tuple[int, str, str]]:
        if limit < 1:
            raise ValueError("Outbox read limit must be positive.")
        with self._lock:
            rows = self._db.execute(
                "SELECT id, session_id, body FROM pending ORDER BY id LIMIT ?", (limit,),
            ).fetchall()
        group = []
        for row in rows:
            if group and row[1] != group[0][1]:
                break
            group.append(row)
        return group

    def acknowledge_many(self, row_ids: list[int]) -> None:
        with self._lock, self._db:
            self._db.executemany("DELETE FROM pending WHERE id=?", [(row_id,) for row_id in row_ids])

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM pending").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._db.close()


class Sender(Protocol):
    def send_many(self, bodies: list[str], session_id: str) -> int: ...
    def close(self) -> None: ...


class EventHubSender:
    def __init__(self, connection_string: str):
        self._client = EventHubProducerClient.from_connection_string(
            connection_string,
            transport_type=TransportType.AmqpOverWebsocket,
            retry_total=0,
            auth_timeout=10,
            socket_timeout=10,
        )

    def send(self, body: str, session_id: str) -> None:
        self.send_many([body], session_id)

    def send_many(self, bodies: list[str], session_id: str) -> int:
        if not bodies:
            raise ValueError("Cannot send an empty event group.")
        batch = self._client.create_batch(partition_key=session_id)
        count = 0
        for body in bodies:
            event = EventData(body)
            event.content_type = "application/json"
            try:
                batch.add(event)
            except ValueError:
                if not count:
                    raise
                break
            count += 1
        self._client.send_batch(batch, timeout=15)
        return count

    def close(self) -> None:
        self._client.close()


@dataclass(frozen=True)
class PublishingStatus:
    running: bool
    pending: int
    sent: int
    not_queued: int
    error: str

    def text(self) -> str:
        mode = "sending enabled" if self.running else "off"
        text = f"Fabric: {mode} | queued: {self.pending} | endpoint acknowledged: {self.sent}"
        if self.not_queued:
            text += f" | NOT QUEUED: {self.not_queued}; export local events to preserve them"
        if self.error:
            text += f" | {self.error}"
        return text


class EventstreamPublisher:
    def __init__(
        self, outbox_path: str, sender_factory: Callable[[str], Sender] = EventHubSender,
    ):
        self._path = outbox_path
        self._sender_factory = sender_factory
        self._outbox: Outbox | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.RLock()
        self._sent = 0
        self._not_queued = 0
        self._error = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, connection_string: str) -> None:
        if self.running:
            raise ValueError("The previous publisher is still stopping. Try again shortly.")
        endpoint = endpoint_identity(connection_string)
        if self._outbox is not None:
            self._outbox.close()
            self._outbox = None
        self._outbox = Outbox(self._path, endpoint)
        self._stop.clear()
        self._wake.clear()
        self._error = ""
        self._thread = threading.Thread(
            target=self._run, args=(connection_string.strip(),),
            name="fabric-publisher", daemon=True,
        )
        self._thread.start()

    def enqueue(self, event: Event) -> None:
        # Even after a network failure, recorded events stay durable until reconnect.
        if self._outbox is None:
            raise RuntimeError("Configure the publisher before attaching it to the event bus.")
        try:
            self._outbox.enqueue(event)
        except (sqlite3.Error, ValueError, TypeError):
            with self._lock:
                self._not_queued += 1
                self._error = "Local queue write failed. Events remain in the local table; export them."
        self._wake.set()

    def status(self) -> PublishingStatus:
        pending = 0
        try:
            if self._outbox is not None:
                pending = self._outbox.count()
        except sqlite3.Error:
            with self._lock:
                self._error = "Cannot read the local queue. Check local disk access."
            pending = -1
        with self._lock:
            return PublishingStatus(
                self.running and not self._stop.is_set(),
                pending, self._sent, self._not_queued, self._error,
            )

    def enqueue_registration(self, event: Event) -> None:
        """Acknowledge the SQL relay only after a successful durable queue write."""
        if self._outbox is None:
            raise RuntimeError("Connect to Fabric to relay pending player registrations.")
        try:
            self._outbox.enqueue(event)
        except (sqlite3.Error, ValueError, TypeError):
            with self._lock:
                self._error = "Registration queue write failed. The registration remains pending in Rayfin."
            raise RuntimeError(self._error) from None
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def close(self, timeout: float = 1) -> None:
        self.stop()
        if self._thread is not None:
            self._thread.join(timeout)
        if not self.running and self._outbox is not None:
            self._outbox.close()
            self._outbox = None

    def _run(self, connection_string: str) -> None:
        sender = None
        delay = 1
        outbox = self._outbox
        if outbox is None:
            with self._lock:
                self._error = "Publisher stopped: no local queue is open."
            return
        try:
            sender = self._sender_factory(connection_string)
            connection_string = ""
            while not self._stop.is_set():
                try:
                    rows = outbox.next_events()
                    if not rows:
                        self._wake.wait(0.5)
                        self._wake.clear()
                        continue
                    sent = sender.send_many([row[2] for row in rows], rows[0][1])
                    if type(sent) is not int or not 1 <= sent <= len(rows):
                        raise ValueError("Sender returned an invalid acknowledged event count.")
                    outbox.acknowledge_many([row[0] for row in rows[:sent]])
                    with self._lock:
                        self._sent += sent
                        if not self._not_queued:
                            self._error = ""
                    delay = 1
                except (EventHubError, OSError, TimeoutError):
                    with self._lock:
                        self._error = (
                            f"Send not acknowledged; retry in {delay}s. "
                            "Check endpoint credentials, publication and network."
                        )
                    self._stop.wait(delay)
                    delay = min(30, delay * 2)
        except ImportError:
            with self._lock:
                self._error = "Publisher stopped: install the dependencies in requirements.txt."
        except (sqlite3.Error, ValueError, EventHubError, OSError):
            with self._lock:
                self._error = (
                    "Publisher stopped; queued events retained. "
                    "Check local storage, event size and endpoint configuration, then reconnect."
                )
        finally:
            if sender is not None:
                try:
                    sender.close()
                except (EventHubError, OSError):
                    with self._lock:
                        self._error = "Sender close failed; unacknowledged events remain queued."
