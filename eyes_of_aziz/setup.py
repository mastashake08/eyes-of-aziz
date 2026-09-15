"""Interactive setup wizard: log in to your Project Aziz account, scan the
local network for cameras, and register the ones you pick. Each registered
camera gets its own config file under cameras/, ready to hand to
`eyes-of-aziz-bridge --env-file cameras/<name>.env` -- one bridge process per
camera (see README).
"""

from __future__ import annotations

import getpass
import logging
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .account_client import AccountClient, AccountError
from .discovery import (
    CameraDetails,
    CameraProbeError,
    DiscoveredDevice,
    probe_camera,
    scan_network,
)

logger = logging.getLogger(__name__)

DEFAULT_BACKEND_URL = "https://projectaziz.com"


def slugify_device_id(text: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")
    return slug or "camera"


def write_camera_env_file(
    output_dir: Path,
    *,
    device_id: str,
    backend_url: str,
    registration_token: str,
    rtsp_url: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{device_id}.env"
    path.write_text(
        "\n".join(
            [
                f"EYES_OF_AZIZ_BACKEND_URL={backend_url}",
                f"EYES_OF_AZIZ_DEVICE_ID={device_id}",
                f"EYES_OF_AZIZ_REGISTRATION_TOKEN={registration_token}",
                f"EYES_OF_AZIZ_RTSP_URL={rtsp_url}",
                "",
            ]
        )
    )
    # Contains a secret (the registration token) -- owner read/write only.
    path.chmod(0o600)
    return path


@dataclass
class WizardIO:
    """Bundles the wizard's I/O so tests can substitute scripted versions
    without touching real stdin/stdout, the network, or the filesystem."""

    prompt: Callable[[str], str] = input
    prompt_password: Callable[[str], str] = getpass.getpass
    print: Callable[[str], None] = print
    scan_network: Callable[..., list[DiscoveredDevice]] = scan_network
    probe_camera: Callable[..., CameraDetails] = probe_camera
    account_client_factory: Callable[[str], AccountClient] = AccountClient
    output_dir: Path = field(default_factory=lambda: Path("cameras"))


def run_wizard(io: WizardIO | None = None) -> int:
    io = io or WizardIO()

    backend_url = (
        io.prompt(f"Project Aziz backend URL [{DEFAULT_BACKEND_URL}]: ").strip()
        or DEFAULT_BACKEND_URL
    )
    email = io.prompt("Email: ").strip()
    password = io.prompt_password("Password: ")

    account = io.account_client_factory(backend_url)

    try:
        bearer_token = account.login(email, password)
    except AccountError as exc:
        io.print(f"Login failed: {exc}")
        return 1

    io.print("Logged in. Scanning the local network for cameras (this takes a few seconds)...")
    devices = io.scan_network()

    if not devices:
        io.print(
            "No devices responded. Make sure the camera is powered on and on "
            "the same network/VLAN as this machine."
        )
        return 0

    io.print(f"Found {len(devices)} device(s) that might be cameras.")

    registered = 0
    for device in devices:
        io.print(f"\n--- {device.host}:{device.port} ---")
        if io.prompt("Add this device? [y/N]: ").strip().lower() != "y":
            continue

        username = io.prompt(
            "Camera username (its own login, not your Project Aziz account) "
            "[blank for none]: "
        ).strip()
        cam_password = io.prompt_password("Camera password: ") if username else ""

        try:
            details = io.probe_camera(device, username, cam_password)
        except CameraProbeError as exc:
            io.print(f"Could not add it: {exc}")
            continue

        label = " ".join(filter(None, [details.manufacturer, details.model])) or device.host
        io.print(f"Confirmed camera: {label}")

        default_id = slugify_device_id(label if details.manufacturer else device.host)
        device_id = io.prompt(f"Name this camera [{default_id}]: ").strip() or default_id

        try:
            device_config = account.register_camera(bearer_token, device_id)
        except AccountError as exc:
            io.print(f"Could not register it with Project Aziz: {exc}")
            continue

        path = write_camera_env_file(
            io.output_dir,
            device_id=device_config["device_id"],
            backend_url=backend_url,
            registration_token=device_config["registration_token"],
            rtsp_url=details.rtsp_url,
        )
        io.print(f"Registered. Config written to {path}")
        io.print(f"Run it with: eyes-of-aziz-bridge --env-file {path}")
        registered += 1

    io.print(f"\nDone -- registered {registered} camera(s).")
    return 0


def main() -> int:
    logging.basicConfig(level="INFO", format="%(message)s")
    return run_wizard()


if __name__ == "__main__":
    sys.exit(main())
