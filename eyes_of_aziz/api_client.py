"""HTTP client for the Project Aziz Eyes Of Aziz frame-ingestion endpoint.

Talks to POST /api/field-devices/{deviceId}/frames (see
FieldDeviceController::ingestFrame in the main Laravel app). That route has
no Sanctum session -- it authenticates the same way FieldDeviceController's
other unauthenticated device endpoints do, via a registration_token issued
at camera registration (POST /api/field-devices/register).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

from .config import BridgeConfig

logger = logging.getLogger(__name__)


class BackendError(RuntimeError):
    """Raised after retries are exhausted or the backend rejects the request outright."""


class DeviceRejectedError(BackendError):
    """Raised on 401/403: bad registration_token, or the camera isn't active in the network.

    Retrying immediately won't help here -- these indicate a configuration
    problem (wrong token, device deactivated, or shares_with_law_enforcement
    revoked) rather than a transient network issue.
    """


class BackendClient:
    def __init__(self, config: BridgeConfig, session: requests.Session | None = None) -> None:
        self._config = config
        self._session = session or requests.Session()

    def send_frame(self, jpeg_bytes: bytes, captured_at: datetime | None = None) -> None:
        captured_at = captured_at or datetime.now(timezone.utc)

        data = {
            "registration_token": self._config.registration_token,
            "captured_at": captured_at.isoformat(),
        }
        files = {
            "frame": ("frame.jpg", jpeg_bytes, "image/jpeg"),
        }

        last_error: Exception | None = None

        for attempt in range(1, self._config.max_send_retries + 1):
            try:
                response = self._session.post(
                    self._config.frames_url,
                    data=data,
                    files=files,
                    timeout=self._config.request_timeout_seconds,
                )
            except requests.RequestException as exc:
                last_error = exc
                logger.warning(
                    "Frame upload attempt %d/%d failed: %s",
                    attempt,
                    self._config.max_send_retries,
                    exc,
                )
                self._sleep_before_retry(attempt)
                continue

            if response.status_code in (401, 403):
                raise DeviceRejectedError(
                    f"Backend rejected this device ({response.status_code}): "
                    f"{self._error_message(response)}"
                )

            if response.ok:
                logger.debug("Frame uploaded (%s)", response.status_code)
                return

            last_error = BackendError(
                f"Backend returned {response.status_code}: {self._error_message(response)}"
            )
            logger.warning(
                "Frame upload attempt %d/%d failed: %s",
                attempt,
                self._config.max_send_retries,
                last_error,
            )
            self._sleep_before_retry(attempt)

        raise BackendError(
            f"Failed to upload frame after {self._config.max_send_retries} attempt(s)"
        ) from last_error

    def _sleep_before_retry(self, attempt: int) -> None:
        if attempt < self._config.max_send_retries:
            time.sleep(min(2 ** (attempt - 1), 10))

    @staticmethod
    def _error_message(response: requests.Response) -> str:
        try:
            return str(response.json().get("message", response.text))
        except ValueError:
            return response.text
