"""Main capture loop: pull frames from an RTSP camera and forward them to
Project Aziz's Eyes Of Aziz endpoint for face matching against active
missing-person cases. By default only faces are sent: each frame is checked
for faces here and every face found is uploaded as its own crop (faces.py).

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
from .faces import FaceDetector, MotionGate, crop_face

logger = logging.getLogger(__name__)


class CameraBridge:
    def __init__(
        self,
        config: BridgeConfig,
        client: BackendClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        detector: FaceDetector | None = None,
        motion_gate: MotionGate | None = None,
    ) -> None:
        self._config = config
        self._client = client or BackendClient(config)
        self._stop_event = threading.Event()
        self._clock = clock
        self._last_heartbeat: float | None = None
        self._faces_only = config.upload_mode == "faces"
        self._detector = detector
        if self._faces_only and self._detector is None:
            self._detector = FaceDetector(config.face_score_threshold, config.min_face_size)
        self._motion_gate = motion_gate or MotionGate(config.motion_threshold)
        # Counts since start, for logs and the status page.
        self.stats = {"frames_captured": 0, "frames_with_faces": 0, "uploads": 0}
        # What the status page shows. state is one of: starting, running,
        # no_stream (can't read the camera), rejected (backend refused the
        # device), stopped.
        self.state = "starting"
        self.last_upload_at: datetime | None = None
        self.last_heartbeat_at: datetime | None = None
        self.last_error: str | None = None
        self.rejected = False

    @property
    def device_id(self) -> str:
        return self._config.device_id

    def stop(self) -> None:
        self._stop_event.set()

    def status(self) -> dict:
        """A snapshot for the status page."""
        return {
            "device_id": self._config.device_id,
            "state": self.state,
            "upload_mode": self._config.upload_mode,
            "last_upload_at": self.last_upload_at.isoformat() if self.last_upload_at else None,
            "last_heartbeat_at": self.last_heartbeat_at.isoformat() if self.last_heartbeat_at else None,
            "last_error": self.last_error,
            "has_location": self._config.latitude is not None,
            **self.stats,
        }

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
                    self.state = "no_stream"
                    self.last_error = "Can't read the camera's stream; retrying."
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
                if self.state != "running":
                    self.state = "running"
                    self.last_error = None
                self.stats["frames_captured"] += 1

                if not self._handle_frame(frame, datetime.now(timezone.utc)):
                    # A rejected device won't start working by itself -- no
                    # point burning the retry budget on every frame after.
                    break

                if self._stop_event.wait(self._config.capture_interval_seconds):
                    break
        finally:
            capture.release()
            self.state = "rejected" if self.rejected else "stopped"
            logger.info("Bridge stopped")

    def _handle_frame(self, frame, captured_at: datetime) -> bool:
        """Uploads the frame, or the faces in it. Returns False if the
        backend rejected the device."""
        if not self._faces_only:
            return self._upload(frame, captured_at)

        if not self._motion_gate.changed(frame):
            return True

        faces = self._detector.detect(frame)[: self._config.max_faces_per_frame]
        if not faces:
            return True

        self.stats["frames_with_faces"] += 1
        logger.debug("%d face(s) in frame", len(faces))
        for face in faces:
            if not self._upload(crop_face(frame, face), captured_at):
                return False
        return True

    def _upload(self, image, captured_at: datetime) -> bool:
        encoded, buffer = cv2.imencode(
            ".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, self._config.jpeg_quality]
        )
        if not encoded:
            logger.warning("Failed to JPEG-encode an image, skipping it")
            return True
        return self._send(buffer.tobytes(), captured_at)

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
            self.last_heartbeat_at = datetime.now(timezone.utc)
        except DeviceRejectedError as exc:
            self._reject(exc)
            logger.exception("Device rejected by backend on heartbeat. Stopping.")
            return False
        except BackendError as exc:
            self.last_error = f"Heartbeat failed: {exc}"
            logger.warning("Heartbeat failed, will try again next interval: %s", exc)
        return True

    def _reject(self, exc: Exception) -> None:
        self.rejected = True
        self.last_error = f"Project Aziz rejected this camera: {exc}"


    def _open_capture(self) -> cv2.VideoCapture:
        capture = cv2.VideoCapture(self._config.rtsp_url)
        if not capture.isOpened():
            logger.warning("Could not open RTSP stream at startup, will keep retrying")
        return capture

    def _send(self, jpeg_bytes: bytes, captured_at: datetime) -> bool:
        try:
            self._client.send_frame(jpeg_bytes, captured_at)
            self.stats["uploads"] += 1
            self.last_upload_at = datetime.now(timezone.utc)
            return True
        except DeviceRejectedError as exc:
            self._reject(exc)
            logger.exception(
                "Device rejected by backend -- check EYES_OF_AZIZ_DEVICE_ID/"
                "EYES_OF_AZIZ_REGISTRATION_TOKEN, or that this camera is still "
                "active and shared with law enforcement. Stopping."
            )
            return False
        except Exception as exc:
            self.last_error = f"Upload failed: {exc}"
            logger.exception("Failed to upload frame, will try again next capture")
            return True
