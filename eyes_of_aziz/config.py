"""Configuration for the Eyes Of Aziz bridge agent.

Loaded from environment variables (see .env.example), matching the device_id
and registration_token issued by the Project Aziz backend when a camera is
registered via POST /api/field-devices/register.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

REQUIRED_VARS = (
    "EYES_OF_AZIZ_BACKEND_URL",
    "EYES_OF_AZIZ_DEVICE_ID",
    "EYES_OF_AZIZ_REGISTRATION_TOKEN",
    "EYES_OF_AZIZ_RTSP_URL",
)


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class BridgeConfig:
    backend_url: str
    device_id: str
    registration_token: str
    rtsp_url: str
    capture_interval_seconds: float = 10.0
    jpeg_quality: int = 85
    request_timeout_seconds: float = 15.0
    max_send_retries: int = 3
    reconnect_backoff_seconds: float = 5.0
    max_reconnect_backoff_seconds: float = 60.0

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "BridgeConfig":
        missing = [name for name in REQUIRED_VARS if not env.get(name)]
        if missing:
            raise ConfigError(
                "Missing required environment variable(s): "
                + ", ".join(missing)
                + ". See .env.example."
            )

        backend_url = env["EYES_OF_AZIZ_BACKEND_URL"].rstrip("/")

        def _float(name: str, default: float) -> float:
            raw = env.get(name)
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError as exc:
                raise ConfigError(f"{name} must be a number, got {raw!r}") from exc

        def _int(name: str, default: int) -> int:
            raw = env.get(name)
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError as exc:
                raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc

        capture_interval_seconds = _float("EYES_OF_AZIZ_CAPTURE_INTERVAL_SECONDS", 10.0)
        if capture_interval_seconds <= 0:
            raise ConfigError("EYES_OF_AZIZ_CAPTURE_INTERVAL_SECONDS must be greater than 0")

        jpeg_quality = _int("EYES_OF_AZIZ_JPEG_QUALITY", 85)
        if not 1 <= jpeg_quality <= 100:
            raise ConfigError("EYES_OF_AZIZ_JPEG_QUALITY must be between 1 and 100")

        return cls(
            backend_url=backend_url,
            device_id=env["EYES_OF_AZIZ_DEVICE_ID"],
            registration_token=env["EYES_OF_AZIZ_REGISTRATION_TOKEN"],
            rtsp_url=env["EYES_OF_AZIZ_RTSP_URL"],
            capture_interval_seconds=capture_interval_seconds,
            jpeg_quality=jpeg_quality,
            request_timeout_seconds=_float("EYES_OF_AZIZ_REQUEST_TIMEOUT_SECONDS", 15.0),
            max_send_retries=_int("EYES_OF_AZIZ_MAX_SEND_RETRIES", 3),
            reconnect_backoff_seconds=_float("EYES_OF_AZIZ_RECONNECT_BACKOFF_SECONDS", 5.0),
            max_reconnect_backoff_seconds=_float(
                "EYES_OF_AZIZ_MAX_RECONNECT_BACKOFF_SECONDS", 60.0
            ),
        )

    @property
    def frames_url(self) -> str:
        return f"{self.backend_url}/api/field-devices/{self.device_id}/frames"
