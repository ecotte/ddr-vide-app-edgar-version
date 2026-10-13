"""Durable delivery tests use fake senders; no credentials or network required."""
import json
import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.event_bus import EventBus
from app.eventstream import EventHubSender, EventstreamPublisher, Outbox, endpoint_identity
from app.ui.app_window import App


CONNECTION = (
    "Endpoint=sb://example.servicebus.windows.net/;"
    "SharedAccessKeyName=send;SharedAccessKey=not-a-real-key;EntityPath=events"
)
TARGET = "example.servicebus.windows.net/events"


def sample(bus=None):
    bus = bus or EventBus(source_mode="synthetic")
    return bus.make_event(
        "score_observed",
        {"observed_value": 1250, "confirmed": False, "field": "score"},
        60, 1000, player="P1", confidence=0.7,
    )


def wait_until(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        if time.monotonic() > deadline:
            pytest.fail("Timed out waiting for publisher state")
        time.sleep(0.01)


class FakeSender:
    def __init__(self):
        self.calls = []
        self.failures = 0
        self.closed = False
        self.called = threading.Event()

    def send(self, body, session_id):
        self.calls.append((body, session_id))
        self.called.set()
        if self.failures:
            self.failures -= 1
            raise OSError("Sensitive transport detail must not reach the status")

    def close(self):
        self.closed = True

    def send_many(self, bodies, session_id):
        for body in bodies:
            self.send(body, session_id)
        return len(bodies)


@pytest.mark.parametrize("connection", [
    "", "SharedAccessKey=secret",
    CONNECTION.replace("sb://", "http://"),
    CONNECTION.replace("example.servicebus.windows.net", "localhost"),
    CONNECTION.replace("EntityPath=events", "EntityPath=../events"),
    CONNECTION + ";SharedAccessKey=second-secret",
])
def test_invalid_credentials_have_sanitized_errors(connection):
    with pytest.raises(ValueError) as error:
        endpoint_identity(connection)
    assert "secret" not in str(error.value)
    assert "not-a-real-key" not in str(error.value)


def test_outbox_persists_exact_json_and_deduplicates_pending_ids(tmp_path):
    path = str(tmp_path / "outbox.sqlite3")
    event = sample()
    outbox = Outbox(path, TARGET)
    outbox.enqueue(event)
    outbox.enqueue(event)
    assert outbox.count() == 1
    outbox.close()
    reopened = Outbox(path, TARGET)
    row_id, session_id, body = reopened.next_event()
    assert session_id == event.session_id
    assert json.loads(body) == event.to_dict()
    reopened.acknowledge(row_id)
    assert reopened.count() == 0
    reopened.close()


def test_pending_events_cannot_be_redirected_to_another_endpoint(tmp_path):
    path = str(tmp_path / "outbox.sqlite3")
    queue = Outbox(path, TARGET)
    queue.enqueue(sample())
    queue.close()
    with pytest.raises(ValueError, match="different endpoint"):
        Outbox(path, "different.servicebus.windows.net/events")
    queue = Outbox(path, TARGET)
    queue.acknowledge(queue.next_event()[0])
    queue.close()
    queue = Outbox(path, "different.servicebus.windows.net/events")
    assert queue.count() == 0
    queue.close()


def test_event_bus_forwards_only_future_recorded_events():
    bus = EventBus()
    bus.record(sample(bus))
    sink = Mock()
    bus.set_delivery_sink(sink)
    event = sample(bus)
    bus.record(event)
    sink.assert_called_once_with(event)
    bus.clear_events()
    bus.new_session()
    sink.assert_called_once_with(event)
    bus.set_delivery_sink(None)
    bus.record(sample(bus))
    sink.assert_called_once()


def test_success_removes_only_acknowledged_events_and_preserves_envelope(tmp_path):
    sender = FakeSender()
    publisher = EventstreamPublisher(str(tmp_path / "queue.sqlite3"), lambda _: sender)
    event = sample()
    try:
        publisher.start(CONNECTION)
        publisher.enqueue(event)
        wait_until(lambda: publisher.status().sent == 1)
        assert publisher.status().pending == 0
        assert json.loads(sender.calls[0][0]) == event.to_dict()
        assert sender.calls[0][1] == event.session_id
    finally:
        publisher.close(5)
    assert sender.closed


def test_failure_retries_identical_event_and_hides_transport_details(tmp_path):
    sender = FakeSender()
    sender.failures = 1
    publisher = EventstreamPublisher(str(tmp_path / "queue.sqlite3"), lambda _: sender)
    try:
        publisher.start(CONNECTION)
        publisher.enqueue(sample())
        wait_until(lambda: bool(publisher.status().error))
        assert publisher.status().pending == 1
        assert publisher.status().sent == 0
        assert "Sensitive" not in publisher.status().error
        wait_until(lambda: publisher.status().sent == 1)
        assert len(sender.calls) == 2
        assert sender.calls[0] == sender.calls[1]
        assert publisher.status().error == ""
    finally:
        publisher.close(5)


def test_restart_resumes_unsent_events_without_persisting_credentials(tmp_path):
    path = str(tmp_path / "queue.sqlite3")
    failing = FakeSender()
    failing.failures = 100
    publisher = EventstreamPublisher(path, lambda _: failing)
    event = sample()
    publisher.start(CONNECTION)
    publisher.enqueue(event)
    wait_until(lambda: bool(publisher.status().error))
    publisher.close(5)
    sender = FakeSender()
    resumed = EventstreamPublisher(path, lambda _: sender)
    try:
        resumed.start(CONNECTION.replace("not-a-real-key", "rotated-placeholder"))
        wait_until(lambda: resumed.status().sent == 1)
        assert json.loads(sender.calls[0][0])["event_id"] == event.event_id
    finally:
        resumed.close(5)
    raw = (tmp_path / "queue.sqlite3").read_bytes()
    assert b"not-a-real-key" not in raw
    assert b"rotated-placeholder" not in raw
    assert b"SharedAccessKey" not in raw


def test_queue_failure_is_visible_and_local_event_remains_exportable(tmp_path, monkeypatch):
    sender = FakeSender()
    publisher = EventstreamPublisher(str(tmp_path / "queue.sqlite3"), lambda _: sender)
    bus = EventBus()
    try:
        publisher.start(CONNECTION)
        monkeypatch.setattr(
            publisher._outbox, "enqueue", Mock(side_effect=sqlite3.OperationalError("disk full"))
        )
        bus.set_delivery_sink(publisher.enqueue)
        event = sample(bus)
        bus.record(event)
        status = publisher.status()
        assert status.not_queued == 1
        assert "NOT QUEUED: 1" in status.text()
        assert "export" in status.text()
        assert bus.events == [event]
        assert sender.calls == []
    finally:
        bus.set_delivery_sink(None)
        publisher.close(5)


def test_failed_ack_retains_event_for_at_least_once_retry(tmp_path, monkeypatch):
    path = str(tmp_path / "queue.sqlite3")
    publisher = EventstreamPublisher(path, lambda _: FakeSender())
    try:
        publisher.start(CONNECTION)
        monkeypatch.setattr(
            publisher._outbox, "acknowledge_many", Mock(side_effect=sqlite3.OperationalError())
        )
        publisher.enqueue(sample())
        wait_until(lambda: not publisher.running)
        assert publisher.status().pending == 1
        assert "Publisher stopped" in publisher.status().error
    finally:
        publisher.close(5)


def test_sdk_uses_json_session_partition_and_acknowledged_send(monkeypatch):
    client = Mock()
    factory = Mock(return_value=client)
    monkeypatch.setattr(
        "app.eventstream.EventHubProducerClient.from_connection_string", factory,
    )
    sender = EventHubSender(CONNECTION)
    sender.send('{"source_mode":"synthetic"}', "session-1")
    client.create_batch.assert_called_once_with(partition_key="session-1")
    data = client.create_batch.return_value.add.call_args.args[0]
    assert data.content_type == "application/json"
    assert json.loads(data.body_as_str())["source_mode"] == "synthetic"
    client.send_batch.assert_called_once_with(client.create_batch.return_value, timeout=15)
    assert factory.call_args.kwargs["retry_total"] == 0
    sender.close()
    client.close.assert_called_once()


def test_existing_hundred_event_backlog_sends_in_one_request_without_waiting(tmp_path):
    path = str(tmp_path / "queue.sqlite3")
    bus = EventBus()
    events = [sample(bus) for _ in range(100)]
    queue = Outbox(path, TARGET)
    for event in events:
        queue.enqueue(event)
    queue.close()
    sender = FakeSender()
    sender.send_many = Mock(wraps=sender.send_many)
    publisher = EventstreamPublisher(path, lambda _: sender)
    try:
        publisher.start(CONNECTION)
        wait_until(lambda: publisher.status().sent == 100)
        assert publisher.status().pending == 0
        sender.send_many.assert_called_once()
        assert [json.loads(body)["event_id"] for body in sender.send_many.call_args.args[0]] == [
            event.event_id for event in events
        ]
    finally:
        publisher.close(5)


def test_outbox_groups_are_bounded_ordered_and_do_not_cross_sessions(tmp_path):
    queue = Outbox(str(tmp_path / "queue.sqlite3"), TARGET)
    a, b = EventBus(), EventBus()
    events = [sample(a) for _ in range(105)] + [sample(b), sample(a)]
    try:
        for event in events:
            queue.enqueue(event)
        first = queue.next_events()
        assert len(first) == 100
        queue.acknowledge_many([row[0] for row in first])
        for size, session in ((5, a.session_id), (1, b.session_id), (1, a.session_id)):
            rows = queue.next_events()
            assert len(rows) == size
            assert all(row[1] == session for row in rows)
            queue.acknowledge_many([row[0] for row in rows])
        assert queue.next_events() == []
    finally:
        queue.close()


def test_sdk_sends_only_fitting_prefix_and_reports_acknowledged_count(monkeypatch):
    client = Mock()
    client.create_batch.return_value.add.side_effect = [None, None, ValueError("batch full")]
    monkeypatch.setattr(
        "app.eventstream.EventHubProducerClient.from_connection_string", lambda *a, **kw: client,
    )
    sender = EventHubSender(CONNECTION)
    assert sender.send_many(['{"n":1}', '{"n":2}', '{"n":3}'], "session") == 2
    client.send_batch.assert_called_once()


def test_sdk_oversized_first_event_is_reported_without_sending(monkeypatch):
    client = Mock()
    client.create_batch.return_value.add.side_effect = ValueError("event too large")
    monkeypatch.setattr(
        "app.eventstream.EventHubProducerClient.from_connection_string", lambda *a, **kw: client,
    )
    with pytest.raises(ValueError):
        EventHubSender(CONNECTION).send_many(["large"], "session")
    client.send_batch.assert_not_called()


def test_only_acknowledged_prefix_is_removed_before_next_send(tmp_path):
    path = str(tmp_path / "queue.sqlite3")
    bus = EventBus()
    events = [sample(bus) for _ in range(3)]
    queue = Outbox(path, TARGET)
    for event in events:
        queue.enqueue(event)
    queue.close()
    entered, release = threading.Event(), threading.Event()
    groups = []

    class PartialSender(FakeSender):
        def send_many(self, bodies, session_id):
            groups.append(bodies)
            if len(groups) == 1:
                return 2
            entered.set()
            assert release.wait(5)
            return len(bodies)

    publisher = EventstreamPublisher(path, lambda _: PartialSender())
    try:
        publisher.start(CONNECTION)
        assert entered.wait(5)
        assert publisher.status().sent == 2
        assert publisher.status().pending == 1
        assert len(groups[0]) == 3
        assert [json.loads(body)["event_id"] for body in groups[1]] == [events[2].event_id]
        release.set()
        wait_until(lambda: publisher.status().sent == 3)
    finally:
        release.set()
        publisher.close(5)


def test_failed_group_retains_every_event_and_retries_same_payloads(tmp_path):
    path = str(tmp_path / "queue.sqlite3")
    bus = EventBus()
    queue = Outbox(path, TARGET)
    for _ in range(3):
        queue.enqueue(sample(bus))
    queue.close()
    groups = []

    class FailingGroup(FakeSender):
        def send_many(self, bodies, session_id):
            groups.append((bodies, session_id))
            if len(groups) == 1:
                raise OSError("private transport detail")
            return len(bodies)

    publisher = EventstreamPublisher(path, lambda _: FailingGroup())
    try:
        publisher.start(CONNECTION)
        wait_until(lambda: bool(publisher.status().error))
        assert publisher.status().pending == 3
        assert publisher.status().sent == 0
        assert "private" not in publisher.status().error
        wait_until(lambda: publisher.status().sent == 3)
        assert groups[0] == groups[1]
    finally:
        publisher.close(5)


def test_mapping_sample_is_synthetic_and_does_not_change_gameplay_session():
    bus = EventBus()
    original_session = bus.session_id
    ui = SimpleNamespace(
        _publishing_enabled=True, event_bus=bus, events_tab=Mock(),
    )
    App.send_mapping_sample(ui)
    event = bus.events[0]
    assert event.source_mode == "synthetic"
    assert event.session_id != original_session
    assert event.payload["is_test"] is True
    assert event.payload["new_value"] == 1250
    assert bus.session_id == original_session
    ui.events_tab.append_event.assert_called_once_with(event)


def test_network_wait_does_not_block_recording_or_delete_queued_events(tmp_path):
    sending, release = threading.Event(), threading.Event()

    class SlowSender(FakeSender):
        def send(self, body, session_id):
            sending.set()
            assert release.wait(5)
            super().send(body, session_id)

    sender = SlowSender()
    publisher = EventstreamPublisher(str(tmp_path / "queue.sqlite3"), lambda _: sender)
    bus = EventBus()
    try:
        publisher.start(CONNECTION)
        bus.set_delivery_sink(publisher.enqueue)
        bus.record(sample(bus))
        assert sending.wait(5)
        recorded = threading.Event()

        def record_another():
            bus.record(sample(bus))
            recorded.set()

        worker = threading.Thread(target=record_another)
        worker.start()
        assert recorded.wait(2)
        worker.join(2)
        assert publisher.status().pending == 2
        assert publisher.status().sent == 0
        bus.clear_events()
        bus.new_session()
        assert publisher.status().pending == 2
        assert bus.events == []
    finally:
        release.set()
        bus.set_delivery_sink(None)
        publisher.close(5)


def test_ui_connect_requires_explicit_action_and_disconnect_detaches_sink(monkeypatch):
    monkeypatch.setenv("DDR_EVENTSTREAM_CONNECTION_STRING", CONNECTION)
    publisher = Mock()
    bus = EventBus()
    ui = SimpleNamespace(
        _publishing_enabled=False, publisher=publisher, event_bus=bus,
    )
    bus.record(sample(bus))
    publisher.enqueue.assert_not_called()
    App.toggle_publishing(ui)
    publisher.start.assert_called_once_with(CONNECTION)
    assert ui._publishing_enabled
    event = sample(bus)
    bus.record(event)
    publisher.enqueue.assert_called_once_with(event)
    App.toggle_publishing(ui)
    assert not ui._publishing_enabled
    publisher.stop.assert_called_once()
    bus.record(sample(bus))
    publisher.enqueue.assert_called_once()


def test_cancelled_credential_prompt_does_not_enable_publishing(monkeypatch):
    monkeypatch.delenv("DDR_EVENTSTREAM_CONNECTION_STRING", raising=False)
    monkeypatch.setattr("app.ui.app_window.credential_store.load_connection", lambda: None)
    prompt = Mock(return_value=None)
    monkeypatch.setattr("app.ui.app_window.simpledialog.askstring", prompt)
    ui = SimpleNamespace(
        _publishing_enabled=False, publisher=Mock(), event_bus=EventBus(),
    )
    App.toggle_publishing(ui)
    assert prompt.call_args.kwargs["show"] == "*"
    ui.publisher.start.assert_not_called()
    assert not ui._publishing_enabled
