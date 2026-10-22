"""Registers this machine with Project Aziz as an Eyes Of Aziz Bridge, under
the account that signs in (like eyes-of-aziz-setup does for cameras)."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from ..account_client import AccountClient

MACHINE_ID = Path("/etc/machine-id")
DEVICE_FILE = "device.json"


def device_id(machine_id_path: Path = MACHINE_ID) -> str:
    """Stable per machine, so registering again refreshes the same device
    instead of adding a second one."""
    try:
        raw = machine_id_path.read_text().strip()
    except OSError:
        raw = ""
    if not re.fullmatch(r"[0-9a-f]{12,}", raw):
        raw = f"{uuid.getnode():012x}"
    return f"eoa-bridge-{raw[:12]}"


def register(
    account: AccountClient,
    bearer_token: str,
    label: str,
    backend_url: str,
    location: tuple[float, float] | None,
    machine_id_path: Path = MACHINE_ID,
) -> dict:
    """Returns what to save as device.json."""
    from .. import __version__

    device = account.register_device(
        bearer_token, device_id(machine_id_path), "eoa_bridge", firmware_version=__version__, label=label
    )
    saved = {
        "backend_url": backend_url,
        "device_id": device["device_id"],
        "registration_token": device["registration_token"],
        "label": label,
    }
    if location:
        saved["latitude"], saved["longitude"] = location
    return saved
