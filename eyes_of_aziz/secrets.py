"""Keeps each camera's secrets -- its Project Aziz registration token and its
RTSP URL, which usually carries the camera's own password -- in the
operating system's credential store instead of the plain config file:

- macOS: the login Keychain
- Windows: Credential Manager

On Linux (including Docker) they stay in the config file, which is written
readable by its owner only: a background service there usually can't reach
the desktop's keyring, and a camera that can't read its token can't run.

A config file whose secrets are in the store says so with
EYES_OF_AZIZ_SECRETS=keychain; BridgeConfig.from_env reads them back.
"""

from __future__ import annotations

import logging
import sys

import keyring
from keyring.errors import KeyringError

logger = logging.getLogger(__name__)

SERVICE = "eyes-of-aziz"
KEYCHAIN = "keychain"
SECRET_VARS = ("EYES_OF_AZIZ_REGISTRATION_TOKEN", "EYES_OF_AZIZ_RTSP_URL")


def keychain_supported(platform: str = sys.platform) -> bool:
    return platform in ("darwin", "win32")


def _name(device_id: str, var: str) -> str:
    return f"{device_id}:{var}"


def store(device_id: str, values: dict[str, str]) -> bool:
    """Saves the secret values for a camera. Returns False (and saves
    nothing) when the credential store isn't available."""
    if not keychain_supported():
        return False
    try:
        for var, value in values.items():
            keyring.set_password(SERVICE, _name(device_id, var), value)
    except KeyringError as exc:
        logger.warning("Couldn't use the system keychain, keeping secrets in the config file: %s", exc)
        return False
    return True


def load(device_id: str) -> dict[str, str]:
    """The secret values saved for a camera. Missing ones are left out."""
    values = {}
    for var in SECRET_VARS:
        try:
            value = keyring.get_password(SERVICE, _name(device_id, var))
        except KeyringError as exc:
            logger.warning("Couldn't read %s for %s from the keychain: %s", var, device_id, exc)
            value = None
        if value:
            values[var] = value
    return values


def forget(device_id: str) -> None:
    for var in SECRET_VARS:
        try:
            keyring.delete_password(SERVICE, _name(device_id, var))
        except KeyringError:
            pass
