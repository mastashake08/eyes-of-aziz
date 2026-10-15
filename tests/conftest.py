import keyring
import pytest
from keyring.backend import KeyringBackend


class MemoryKeyring(KeyringBackend):
    """Stands in for the system keychain so tests never touch the real one."""

    priority = 1

    def __init__(self) -> None:
        super().__init__()
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.passwords.get((service, username))

    def set_password(self, service, username, password):
        self.passwords[(service, username)] = password

    def delete_password(self, service, username):
        self.passwords.pop((service, username), None)


@pytest.fixture(autouse=True)
def memory_keyring(monkeypatch):
    """Every test gets an empty in-memory keychain, and the file fallback
    (as on Linux) unless it asks for the keychain with keychain_on."""
    monkeypatch.setenv("EYES_OF_AZIZ_UPDATE_CHECK", "0")  # no calls to GitHub from tests
    backend = MemoryKeyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)
    monkeypatch.setattr("eyes_of_aziz.secrets.keychain_supported", lambda platform=None: False)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture
def keychain_on(monkeypatch, memory_keyring):
    monkeypatch.setattr("eyes_of_aziz.secrets.keychain_supported", lambda platform=None: True)
    return memory_keyring
