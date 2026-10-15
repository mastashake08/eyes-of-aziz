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
import time
from collections.abc import Callable
from datetime import datetime, timezone

import cv2

from .api_client import BackendClient, BackendError, DeviceRejectedError
from .config import BridgeConfig

logger = logging.getLogger(__name__)


class CameraBridge:
    def __init__(
        self,
        config: BridgeConfig,
        client: BackendClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._client = client or BackendClient(config)
        self._stop_event = threading.Event()
        self._clock = clock
        self._last_heartbeat: float | None = None

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
                if not self._heartbeat_if_due():
                    break

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

    def _heartbeat_if_due(self) -> bool:
        """Sends a heartbeat on start and then every heartbeat interval.
        Returns False only when the backend rejects the device; any other
        failure is logged and frames keep flowing.
        """
        now = self._clock()
        if (
            self._last_heartbeat is not None
            and now - self._last_heartbeat < self._config.heartbeat_interval_seconds
        ):
            return True

        self._last_heartbeat = now
        try:
            self._client.send_status()
        except DeviceRejectedError:
            logger.exception("Device rejected by backend on heartbeat. Stopping.")
            return False
        except BackendError as exc:
            logger.warning("Heartbeat failed, will try again next interval: %s", exc)
        return True

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
