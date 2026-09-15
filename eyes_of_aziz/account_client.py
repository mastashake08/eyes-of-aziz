"""Client for the Project Aziz *user account* API -- distinct from
BackendClient in api_client.py, which authenticates as an already-registered
device via its registration_token. This one logs in as a person
(POST /api/login, the same endpoint the mobile app uses) to register new
cameras on their behalf (POST /api/field-devices/register).
"""

from __future__ import annotations

import requests


class AccountError(RuntimeError):
    """Login failed, or the account API rejected a request."""


class AccountClient:
    def __init__(self, backend_url: str, session: requests.Session | None = None) -> None:
        self._backend_url = backend_url.rstrip("/")
        self._session = session or requests.Session()

    def login(self, email: str, password: str) -> str:
        """Returns a Sanctum bearer token for this account."""
        response = self._session.post(
            f"{self._backend_url}/api/login",
            json={"email": email, "password": password},
            headers={"Accept": "application/json"},
            timeout=15,
        )

        if not response.ok:
            raise AccountError(f"Login failed: {self._error_message(response)}")

        token = response.json().get("token")
        if not token:
            raise AccountError("Login succeeded but the response had no token.")

        return token

    def register_camera(
        self, bearer_token: str, device_id: str, firmware_version: str | None = None
    ) -> dict:
        """Registers a new camera_bridge device under the logged-in account.
        Returns the backend's device config, including registration_token.
        """
        payload = {"device_id": device_id, "device_type": "camera_bridge"}
        if firmware_version:
            payload["firmware_version"] = firmware_version

        response = self._session.post(
            f"{self._backend_url}/api/field-devices/register",
            json=payload,
            headers={"Accept": "application/json", "Authorization": f"Bearer {bearer_token}"},
            timeout=15,
        )

        if not response.ok:
            raise AccountError(f"Camera registration failed: {self._error_message(response)}")

        return response.json()["device"]

    @staticmethod
    def _error_message(response: requests.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text

        # Prefer the specific field errors from a validation failure (e.g.
        # "The provided credentials are incorrect.") over the generic
        # "The given data was invalid." message that comes alongside them.
        errors = body.get("errors")
        if errors:
            return "; ".join(f"{field}: {', '.join(msgs)}" for field, msgs in errors.items())

        message = body.get("message")
        if message:
            return str(message)

        return response.text
