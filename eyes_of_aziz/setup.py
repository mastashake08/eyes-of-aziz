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

from . import secrets
from .account_client import AccountClient, AccountError
from .api_client import BackendClient, BackendError
from .config import BridgeConfig
from .discovery import (
    CameraDetails,
    CameraProbeError,
    DiscoveredDevice,
    probe_camera,
    scan_network,
)
from .location import LocationError, parse_location

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
    location: tuple[float, float] | None = None,
    store_secrets: Callable[[str, dict[str, str]], bool] = secrets.store,
) -> Path:
    """Writes the camera's config. Its registration token and RTSP URL (which
    usually includes the camera's password) go to the system keychain where
    there is one, and into this file otherwise."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{device_id}.env"
    secret_values = {
        "EYES_OF_AZIZ_REGISTRATION_TOKEN": registration_token,
        "EYES_OF_AZIZ_RTSP_URL": rtsp_url,
    }
    lines = [
        f"EYES_OF_AZIZ_BACKEND_URL={backend_url}",
        f"EYES_OF_AZIZ_DEVICE_ID={device_id}",
    ]
    if store_secrets(device_id, secret_values):
        lines.append(f"EYES_OF_AZIZ_SECRETS={secrets.KEYCHAIN}")
    else:
        lines += [f"{var}={value}" for var, value in secret_values.items()]
    if location is not None:
        lines += [f"EYES_OF_AZIZ_LATITUDE={location[0]}", f"EYES_OF_AZIZ_LONGITUDE={location[1]}"]
    path.write_text("\n".join(lines + [""]))
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
    parse_location: Callable[[str], tuple[float, float] | None] = parse_location
    report_status: Callable[[BridgeConfig], None] = lambda config: report_status(config)


LOCATION_PROMPT = (
    "Where is this camera? A street address, or latitude, longitude "
    "[blank to skip]: "
)


def report_status(config: BridgeConfig) -> None:
    """Sends the first heartbeat right after registration, so the camera
    shows as online, with its location, before the bridge is even started."""
    BackendClient(config).send_status()


def announce_camera(io_print: Callable[[str], None], report: Callable[[BridgeConfig], None], config: BridgeConfig) -> None:
    try:
        report(config)
    except BackendError as exc:
        # Not fatal: the bridge sends the same heartbeat when it starts.
        io_print(f"(Couldn't send its location yet, the bridge will when it starts: {exc})")


def streams_to_register(details: CameraDetails, device_id: str, add_all_channels: bool) -> list[tuple[str, str]]:
    """(device id, RTSP URL) for each camera to register: every channel of a
    recorder as <name>-ch1, -ch2, ..., or just the one stream."""
    if details.is_recorder and add_all_channels:
        return [(f"{device_id}-ch{number}", url) for number, url in enumerate(details.channels, start=1)]
    return [(device_id, details.rtsp_url)]


def register_stream(
    account: AccountClient,
    bearer_token: str,
    backend_url: str,
    output_dir: Path,
    device_id: str,
    rtsp_url: str,
    location: tuple[float, float] | None,
    report: Callable[[BridgeConfig], None],
    io_print: Callable[[str], None],
) -> Path:
    """Registers one camera stream with Project Aziz, writes its config and
    sends its first heartbeat. Raises AccountError if registration fails."""
    device_config = account.register_camera(bearer_token, device_id)
    path = write_camera_env_file(
        output_dir,
        device_id=device_config["device_id"],
        backend_url=backend_url,
        registration_token=device_config["registration_token"],
        rtsp_url=rtsp_url,
        location=location,
    )
    announce_camera(
        io_print,
        report,
        BridgeConfig(
            backend_url=backend_url,
            device_id=device_config["device_id"],
            registration_token=device_config["registration_token"],
            rtsp_url=rtsp_url,
            latitude=location[0] if location else None,
            longitude=location[1] if location else None,
        ),
    )
    return path


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

        add_all_channels = False
        if details.is_recorder:
            io.print(f"This is a recorder with {len(details.channels)} cameras connected.")
            add_all_channels = io.prompt(
                f"Add all {len(details.channels)} as separate cameras ({device_id}-ch1, ...)? [Y/n]: "
            ).strip().lower() != "n"

        location = None
        while True:
            try:
                location = io.parse_location(io.prompt(LOCATION_PROMPT))
                break
            except LocationError as exc:
                io.print(str(exc))
        if location is None:
            io.print("No location: its sightings won't appear on a map until you add one.")

        for channel_id, rtsp_url in streams_to_register(details, device_id, add_all_channels):
            try:
                path = register_stream(
                    account, bearer_token, backend_url, io.output_dir,
                    channel_id, rtsp_url, location, io.report_status, io.print,
                )
            except AccountError as exc:
                io.print(f"Could not register {channel_id} with Project Aziz: {exc}")
                continue
            io.print(f"Registered {channel_id}. Config written to {path}")
            registered += 1

    if registered:
        io.print("Run them with: eyes-of-aziz-bridge --all  (or --install-service to start them automatically)")

    io.print(f"\nDone -- registered {registered} camera(s).")
    return 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="eyes-of-aziz-setup",
        description="Log in, scan the network for cameras, and register the ones you pick.",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Open a local web dashboard in your browser instead of prompting in the terminal.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Web dashboard bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5151, help="Web dashboard port (default: 5151)")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="With --web, don't automatically open a browser tab.",
    )
    args = parser.parse_args()

    logging.basicConfig(level="INFO", format="%(message)s")

    if args.web:
        from .webapp import run_web

        run_web(host=args.host, port=args.port, open_browser=not args.no_browser)
        return 0

    return run_wizard()


if __name__ == "__main__":
    sys.exit(main())
