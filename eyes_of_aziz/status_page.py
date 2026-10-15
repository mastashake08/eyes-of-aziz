"""A small local web page showing the cameras a running bridge is handling:
whether each is online, when it last uploaded a face and reported in, and
the last problem. Binds to 127.0.0.1 only, like the setup dashboard.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, redirect, render_template, request
from werkzeug.serving import make_server

from . import __version__
from .updates import Release

logger = logging.getLogger(__name__)

STATE_LABELS = {
    "starting": "Starting",
    "running": "Online",
    "no_stream": "Can't reach camera",
    "rejected": "Rejected by Project Aziz",
    "stopped": "Stopped",
    "paused": "Paused",
}


def _ago(iso: str | None) -> str:
    if not iso:
        return "never"
    seconds = int((datetime.now(datetime.fromisoformat(iso).tzinfo) - datetime.fromisoformat(iso)).total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


def create_status_app(
    get_status: Callable[[], list[dict]],
    get_update: Callable[[], Release | None] = lambda: None,
    set_paused: Callable[[str, bool], None] | None = None,
) -> Flask:
    app = Flask(__name__)

    @app.context_processor
    def update_banner():
        return {"update": get_update(), "version": __version__}

    @app.get("/")
    def index():
        cameras = get_status()
        for camera in cameras:
            camera["label"] = STATE_LABELS.get(camera.get("state"), camera.get("state", ""))
            camera["uploaded_ago"] = _ago(camera.get("last_upload_at"))
            camera["reported_ago"] = _ago(camera.get("last_heartbeat_at"))
        return render_template("status.html", cameras=cameras, version=__version__, can_pause=set_paused is not None)

    @app.post("/cameras/<device_id>/<action>")
    def pause_or_resume(device_id: str, action: str):
        if set_paused is None or action not in ("pause", "resume"):
            abort(404)
        # Only this page's own buttons: another site open in the same browser
        # mustn't be able to pause cameras by posting here.
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            abort(403)
        if device_id not in {camera["device_id"] for camera in get_status()}:
            abort(404)
        set_paused(device_id, action == "pause")
        return redirect("/", code=303)

    @app.get("/status.json")
    def status_json():
        update = get_update()
        return jsonify(
            version=__version__,
            update_available=update.version if update else None,
            cameras=get_status(),
        )

    return app


class StatusServer:
    """Serves the status page on a background thread until stop()."""

    def __init__(
        self,
        get_status: Callable[[], list[dict]],
        port: int = 5152,
        host: str = "127.0.0.1",
        get_update: Callable[[], Release | None] = lambda: None,
        set_paused: Callable[[str, bool], None] | None = None,
    ) -> None:
        app = create_status_app(get_status, get_update, set_paused)
        self._server = make_server(host, port, app, threaded=True)
        self._thread = threading.Thread(target=self._server.serve_forever, name="status-page", daemon=True)

    @property
    def url(self) -> str:
        return f"http://{self._server.host}:{self._server.server_port}/"

    def start(self) -> None:
        self._thread.start()
        logger.info("Status page: %s", self.url)

    def stop(self) -> None:
        self._server.shutdown()
