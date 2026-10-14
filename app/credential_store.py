"""Store the Fabric credential only in the current user's Windows vault."""
import sys

from keyring.errors import KeyringError, PasswordDeleteError

SERVICE = "DDR Video Event Extractor - Fabric Eventstream"
ACCOUNT = "eventhub-connection"

_STORAGE_ERRORS = (KeyringError, OSError, RuntimeError, ValueError)
if sys.platform == "win32":
    from win32ctypes.pywin32.pywintypes import error as WindowsCredentialError

    _STORAGE_ERRORS += (WindowsCredentialError,)


class CredentialStorageError(Exception):
    pass


def _vault():
    if sys.platform != "win32":
        raise KeyringError("Windows Credential Manager is unavailable.")
    from keyring.backends.Windows import WinVaultKeyring

    backend = WinVaultKeyring()
    backend.persist = "local machine"
    return backend


def load_connection() -> str | None:
    try:
        return _vault().get_password(SERVICE, ACCOUNT)
    except _STORAGE_ERRORS:
        raise CredentialStorageError(
            "Cannot read Windows Credential Manager. Check access under your Windows account."
        ) from None


def save_connection(connection: str) -> None:
    try:
        _vault().set_password(SERVICE, ACCOUNT, connection)
    except _STORAGE_ERRORS:
        raise CredentialStorageError(
            "Cannot save to Windows Credential Manager. The connection works for this run only; "
            "it will need to be entered again."
        ) from None


def forget_connection() -> None:
    try:
        _vault().delete_password(SERVICE, ACCOUNT)
    except PasswordDeleteError:
        # Forgetting an already absent credential is successful.
        return
    except _STORAGE_ERRORS:
        raise CredentialStorageError(
            "Cannot remove the saved credential. Remove it from Windows Credential Manager manually."
        ) from None
