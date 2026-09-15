"""Main capture loop: pull frames from an RTSP camera and forward them to
Project Aziz's Eyes Of Aziz endpoint for face matching against active
missing-person cases.

Reference implementation -- a starting point, not a hardened production
agent. It handles the two failure modes an RTSP bridge actually hits in
practice (the stream dropping, the backend being briefly unreachable), but
things like multi-camera support in one process are left for later.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

import cv2

from .api_client import BackendClient, DeviceRejectedError
from .config import BridgeConfig

logger = logging.getLogger(__name__)


class CameraBridge:
    def __init__(self, config: BridgeConfig, client: BackendClient | None = None) -> None:
        self._config = config
        self._client = client or BackendClient(config)
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        logger.info(
            "Starting Eyes Of Aziz bridge for device %s -> %s",
            self._config.device_id,
            self._config.frames_url,
        )

        capture = self._open_capture()
        reconnect_backoff = self._config.reconnect_backoff_seconds

        try:
            while not self._stop_event.is_set():
                ok, frame = capture.read()

                if not ok:
                    logger.warning("Lost the RTSP stream, reconnecting in %.0fs", reconnect_backoff)
                    capture.release()
                    if self._stop_event.wait(reconnect_backoff):
                        break
                    capture = self._open_capture()
                    reconnect_backoff = min(
                        reconnect_backoff * 2, self._config.max_reconnect_backoff_seconds
                    )
                    continue

                reconnect_backoff = self._config.reconnect_backoff_seconds
                captured_at = datetime.now(timezone.utc)

                encoded, buffer = cv2.imencode(
                    ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self._config.jpeg_quality]
                )
                if not encoded:
                    logger.warning("Failed to JPEG-encode a captured frame, skipping it")
                elif not self._send(buffer.tobytes(), captured_at):
                    # A rejected device won't start working by itself -- no
                    # point burning the retry budget on every frame after.
                    break

                if self._stop_event.wait(self._config.capture_interval_seconds):
                    break
        finally:
            capture.release()
            logger.info("Bridge stopped")

    def _open_capture(self) -> cv2.VideoCapture:
        capture = cv2.VideoCapture(self._config.rtsp_url)
        if not capture.isOpened():
            logger.warning("Could not open RTSP stream at startup, will keep retrying")
        return capture

    def _send(self, jpeg_bytes: bytes, captured_at: datetime) -> bool:
        try:
            self._client.send_frame(jpeg_bytes, captured_at)
            return True
        except DeviceRejectedError:
            logger.exception(
                "Device rejected by backend -- check EYES_OF_AZIZ_DEVICE_ID/"
                "EYES_OF_AZIZ_REGISTRATION_TOKEN, or that this camera is still "
                "active and shared with law enforcement. Stopping."
            )
            return False
        except Exception:
            logger.exception("Failed to upload frame, will try again next capture")
            return True
