"""Frigate as a camera source: instead of reading an RTSP stream and looking
for people itself, the bridge listens for Frigate's "person" events over
MQTT and takes each event's snapshot from Frigate's HTTP API. People who
already run Frigate (often with Home Assistant) get their existing person
detection reused, and the bridge only ever sees frames with a person in.

Frigate publishes event updates on <prefix>/events as JSON with "type"
(new, update, end) and "after" (camera, label, id, has_snapshot...).
"""

from __future__ import annotations

import json
import logging
import queue
from urllib.parse import urlparse

import cv2
import numpy as np
import paho.mqtt.client as mqtt
import requests

from .config import BridgeConfig

logger = logging.getLogger(__name__)


class FrigateError(RuntimeError):
    """A snapshot couldn't be fetched from Frigate."""


class FrigateEvents:
    """Queues the ids of person events for one Frigate camera."""

    def __init__(self, config: BridgeConfig, client: mqtt.Client | None = None) -> None:
        self._config = config
        self._topic = f"{config.frigate_topic_prefix}/events"
        self._queue: queue.Queue[str] = queue.Queue(maxsize=100)
        self._client = client or mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

    def start(self) -> None:
        url = urlparse(self._config.mqtt_url)
        if url.username:
            self._client.username_pw_set(url.username, url.password)
        if url.scheme == "mqtts":
            self._client.tls_set()
        port = url.port or (8883 if url.scheme == "mqtts" else 1883)
        # connect_async + loop_start: reconnects on its own if the broker drops.
        self._client.connect_async(url.hostname, port)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def next_event(self, timeout: float) -> str | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties=None) -> None:
        if getattr(reason_code, "is_failure", False):
            logger.warning("Could not connect to MQTT for Frigate: %s", reason_code)
            return
        client.subscribe(self._topic)
        logger.info("Listening for Frigate person events on %s", self._topic)

    def _on_message(self, _client, _userdata, message) -> None:
        try:
            event = json.loads(message.payload)
        except (TypeError, ValueError):
            return
        after = event.get("after") or {}
        if after.get("camera") != self._config.frigate_camera or after.get("label") != "person":
            return
        # The start of a sighting, and its end, when Frigate has picked the
        # best snapshot of it. Updates in between would mostly repeat them.
        if event.get("type") not in ("new", "end") or not after.get("has_snapshot", True):
            return
        try:
            self._queue.put_nowait(str(after["id"]))
        except (KeyError, queue.Full):
            pass


def fetch_snapshot(config: BridgeConfig, event_id: str, session: requests.Session | None = None) -> np.ndarray:
    """The full-frame snapshot of a Frigate event, as an image."""
    url = f"{config.frigate_url.rstrip('/')}/api/events/{event_id}/snapshot.jpg"
    try:
        response = (session or requests).get(url, params={"bbox": 0, "timestamp": 0}, timeout=config.request_timeout_seconds)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise FrigateError(f"Couldn't get snapshot for event {event_id}: {exc}") from exc

    image = cv2.imdecode(np.frombuffer(response.content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise FrigateError(f"Snapshot for event {event_id} wasn't an image")
    return image
