"""Local web dashboard: the same login -> scan -> probe -> register flow as
setup.py's CLI wizard, as a browser UI instead of terminal prompts.

Binds to 127.0.0.1 only and is meant for a single person setting up their
own cameras on their own machine -- there's no multi-user auth here, and
state (the login token, scan results, in-progress registrations) lives in
an in-memory object on the Flask app rather than in a browser cookie, so
the account's bearer token is never sent to or stored in the browser.
"""

from __future__ import annotations

import logging
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Timer

from flask import Flask, redirect, render_template, request, url_for

from .account_client import AccountClient, AccountError
from .discovery import (
    CameraDetails,
    CameraProbeError,
    DiscoveredDevice,
    probe_camera,
    scan_network,
)
from .setup import DEFAULT_BACKEND_URL, slugify_device_id, write_camera_env_file

logger = logging.getLogger(__name__)


@dataclass
class RegisteredCamera:
    device_id: str
    rtsp_url: str
    config_path: str


@dataclass
class AppState:
    """All of this dashboard's state for the one person using it."""

    backend_url: str = DEFAULT_BACKEND_URL
    bearer_token: str | None = None
    login_error: str | None = None
    devices: list[DiscoveredDevice] = field(default_factory=list)
    has_scanned: bool = False
    scan_error: str | None = None
    # Keyed by index into `devices`, so a probe result survives the redirect
    # from the "enter camera credentials" form to the "name and confirm" page.
    probed: dict[int, CameraDetails] = field(default_factory=dict)
    probe_errors: dict[int, str] = field(default_factory=dict)
    registered: list[RegisteredCamera] = field(default_factory=list)
    registered_indices: set[int] = field(default_factory=set)

    @property
    def logged_in(self) -> bool:
        return self.bearer_token is not None


def create_app(
    *,
    account_client_factory: Callable[[str], AccountClient] = AccountClient,
    scan_network_fn: Callable[..., list[DiscoveredDevice]] = scan_network,
    probe_camera_fn: Callable[..., CameraDetails] = probe_camera,
    output_dir: Path | None = None,
) -> Flask:
    app = Flask(__name__)
    # Only used to sign Flask's own flash-message/session machinery, which
    # this app doesn't otherwise rely on -- all real state lives in `state`
    # below, server-side, so this key never protects anything sensitive.
    app.secret_key = "eyes-of-aziz-local-dashboard"

    state = AppState()
    resolved_output_dir = output_dir or Path("cameras")

    @app.get("/")
    def index():
        return redirect(url_for("cameras" if state.logged_in else "login"))

    @app.get("/login")
    def login():
        return render_template("login.html", backend_url=state.backend_url, error=state.login_error)

    @app.post("/login")
    def do_login():
        backend_url = request.form.get("backend_url", "").strip() or DEFAULT_BACKEND_URL
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        account = account_client_factory(backend_url)
        try:
            token = account.login(email, password)
        except AccountError as exc:
            state.backend_url = backend_url
            state.login_error = str(exc)
            return redirect(url_for("login"))

        state.backend_url = backend_url
        state.bearer_token = token
        state.login_error = None
        return redirect(url_for("cameras"))

    @app.post("/logout")
    def logout():
        state.bearer_token = None
        state.login_error = None
        state.devices = []
        state.probed = {}
        state.probe_errors = {}
        state.registered_indices = set()
        state.has_scanned = False
        return redirect(url_for("login"))

    @app.get("/cameras")
    def cameras():
        if not state.logged_in:
            return redirect(url_for("login"))
        return render_template(
            "cameras.html",
            devices=list(enumerate(state.devices)),
            has_scanned=state.has_scanned,
            probed=state.probed,
            probe_errors=state.probe_errors,
            registered=state.registered,
            registered_indices=state.registered_indices,
            scan_error=state.scan_error,
        )

    @app.post("/scan")
    def scan():
        if not state.logged_in:
            return redirect(url_for("login"))

        state.devices = []
        state.probed = {}
        state.probe_errors = {}
        state.registered_indices = set()
        state.scan_error = None
        state.has_scanned = True

        try:
            state.devices = scan_network_fn()
        except Exception as exc:
            logger.exception("Network scan failed")
            state.scan_error = str(exc)

        return redirect(url_for("cameras"))

    @app.get("/cameras/<int:index>/add")
    def add_camera_form(index: int):
        if not state.logged_in or index >= len(state.devices):
            return redirect(url_for("cameras"))
        return render_template(
            "add_camera.html",
            index=index,
            device=state.devices[index],
            error=state.probe_errors.get(index),
        )

    @app.post("/cameras/<int:index>/add")
    def add_camera(index: int):
        if not state.logged_in or index >= len(state.devices):
            return redirect(url_for("cameras"))

        device = state.devices[index]
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        try:
            details = probe_camera_fn(device, username, password)
        except CameraProbeError as exc:
            state.probe_errors[index] = str(exc)
            return redirect(url_for("add_camera_form", index=index))

        state.probed[index] = details
        state.probe_errors.pop(index, None)
        return redirect(url_for("confirm_camera_form", index=index))

    @app.get("/cameras/<int:index>/confirm")
    def confirm_camera_form(index: int):
        if not state.logged_in or index not in state.probed:
            return redirect(url_for("cameras"))
        return render_template(
            "confirm_camera.html",
            index=index,
            details=state.probed[index],
            default_id=_default_device_id(state, index),
        )

    @app.post("/cameras/<int:index>/register")
    def register_camera(index: int):
        if not state.logged_in or index not in state.probed:
            return redirect(url_for("cameras"))

        details = state.probed[index]
        device_id = request.form.get("device_id", "").strip() or _default_device_id(state, index)

        account = account_client_factory(state.backend_url)
        try:
            device_config = account.register_camera(state.bearer_token, device_id)
        except AccountError as exc:
            return render_template(
                "confirm_camera.html",
                index=index,
                details=details,
                default_id=_default_device_id(state, index),
                error=str(exc),
            )

        path = write_camera_env_file(
            resolved_output_dir,
            device_id=device_config["device_id"],
            backend_url=state.backend_url,
            registration_token=device_config["registration_token"],
            rtsp_url=details.rtsp_url,
        )

        state.registered.append(
            RegisteredCamera(
                device_id=device_config["device_id"],
                rtsp_url=details.rtsp_url,
                config_path=str(path),
            )
        )
        state.registered_indices.add(index)
        state.probed.pop(index, None)
        return redirect(url_for("cameras"))

    return app


def _default_device_id(state: AppState, index: int) -> str:
    device = state.devices[index]
    details = state.probed[index]
    label = " ".join(filter(None, [details.manufacturer, details.model])) or device.host
    return slugify_device_id(label if details.manufacturer else device.host)


def run_web(host: str = "127.0.0.1", port: int = 5151, open_browser: bool = True) -> None:
    app = create_app()

    if open_browser:
        Timer(1.0, lambda: webbrowser.open(f"http://{host}:{port}/")).start()

    app.run(host=host, port=port, debug=False)
