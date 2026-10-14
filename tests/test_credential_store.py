from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from keyring.errors import KeyringError, PasswordDeleteError

from app import credential_store
from app.credential_store import CredentialStorageError
from app.event_bus import EventBus
from app.ui.app_window import App


@pytest.fixture
def vault(monkeypatch):
    backend = Mock()
    monkeypatch.setattr(credential_store, "_vault", lambda: backend)
    return backend


def test_storage_round_trip_uses_only_the_fixed_vault_entry(vault):
    credential_store.save_connection("test-placeholder")
    vault.set_password.assert_called_once_with(
        credential_store.SERVICE, credential_store.ACCOUNT, "test-placeholder",
    )
    vault.get_password.return_value = "test-placeholder"
    assert credential_store.load_connection() == "test-placeholder"
    credential_store.forget_connection()
    vault.delete_password.assert_called_once_with(
        credential_store.SERVICE, credential_store.ACCOUNT,
    )


@pytest.mark.parametrize("operation,method", [
    (credential_store.load_connection, "get_password"),
    (lambda: credential_store.save_connection("placeholder"), "set_password"),
    (credential_store.forget_connection, "delete_password"),
])
def test_storage_errors_do_not_expose_credentials(vault, operation, method):
    getattr(vault, method).side_effect = KeyringError("secret must never be shown")
    with pytest.raises(CredentialStorageError) as error:
        operation()
    assert "secret" not in str(error.value)


def test_forgetting_absent_credential_is_safe(vault):
    vault.delete_password.side_effect = PasswordDeleteError()
    credential_store.forget_connection()


def controller():
    return SimpleNamespace(
        _publishing_enabled=False, publisher=Mock(), event_bus=EventBus(),
    )


def test_saved_credential_is_reused_without_a_prompt(monkeypatch):
    monkeypatch.delenv("DDR_EVENTSTREAM_CONNECTION_STRING", raising=False)
    monkeypatch.setattr(credential_store, "load_connection", lambda: "saved-placeholder")
    prompt = Mock()
    monkeypatch.setattr("app.ui.app_window.simpledialog.askstring", prompt)
    ui = controller()
    ui.publisher.start.assert_not_called()
    App.toggle_publishing(ui)
    ui.publisher.start.assert_called_once_with("saved-placeholder")
    prompt.assert_not_called()
    assert ui._publishing_enabled


def test_prompted_credential_is_saved_only_after_start_succeeds(monkeypatch):
    monkeypatch.delenv("DDR_EVENTSTREAM_CONNECTION_STRING", raising=False)
    monkeypatch.setattr(credential_store, "load_connection", lambda: None)
    monkeypatch.setattr(
        "app.ui.app_window.simpledialog.askstring", Mock(return_value="entered-placeholder"),
    )
    save = Mock()
    monkeypatch.setattr(credential_store, "save_connection", save)
    ui = controller()
    App.toggle_publishing(ui)
    assert ui._publishing_enabled
    save.assert_called_once_with("entered-placeholder")
    save.reset_mock()
    ui = controller()
    ui.publisher.start.side_effect = ValueError("Invalid configuration")
    monkeypatch.setattr("app.ui.app_window.messagebox.showerror", Mock())
    App.toggle_publishing(ui)
    assert not ui._publishing_enabled
    save.assert_not_called()


def test_save_failure_warns_but_keeps_current_connection(monkeypatch):
    monkeypatch.delenv("DDR_EVENTSTREAM_CONNECTION_STRING", raising=False)
    monkeypatch.setattr(credential_store, "load_connection", lambda: None)
    monkeypatch.setattr(
        "app.ui.app_window.simpledialog.askstring", Mock(return_value="entered-placeholder"),
    )
    monkeypatch.setattr(
        credential_store, "save_connection",
        Mock(side_effect=CredentialStorageError("Not saved; current run only")),
    )
    warning = Mock()
    monkeypatch.setattr("app.ui.app_window.messagebox.showwarning", warning)
    ui = controller()
    App.toggle_publishing(ui)
    assert ui._publishing_enabled
    warning.assert_called_once()


def test_environment_takes_precedence_without_reading_or_saving_vault(monkeypatch):
    monkeypatch.setenv("DDR_EVENTSTREAM_CONNECTION_STRING", "env-placeholder")
    load, save = Mock(), Mock()
    monkeypatch.setattr(credential_store, "load_connection", load)
    monkeypatch.setattr(credential_store, "save_connection", save)
    ui = controller()
    App.toggle_publishing(ui)
    ui.publisher.start.assert_called_once_with("env-placeholder")
    load.assert_not_called()
    save.assert_not_called()


def test_forget_disconnects_without_deleting_pending_events(monkeypatch):
    monkeypatch.delenv("DDR_EVENTSTREAM_CONNECTION_STRING", raising=False)
    monkeypatch.setattr("app.ui.app_window.messagebox.askyesno", lambda *_: True)
    forget = Mock()
    monkeypatch.setattr(credential_store, "forget_connection", forget)
    ui = controller()
    ui._publishing_enabled = True
    ui.event_bus.set_delivery_sink(ui.publisher.enqueue)
    App.forget_fabric_connection(ui)
    assert not ui._publishing_enabled
    ui.publisher.stop.assert_called_once()
    forget.assert_called_once()
    assert ui.event_bus._delivery_sink is None
