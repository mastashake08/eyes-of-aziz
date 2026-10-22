"""Tells Project Aziz the bridge is up, and how to reach it over the mesh:
POST /api/field-devices/{device_id}/status with its registration token.
"""

from __future__ import annotations

import logging

import requests

from .. import __version__

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 300


class HeartbeatError(RuntimeError):
    pass


def build_payload(device: dict, snapshot: dict) -> dict:
    payload = {
        "registration_token": device["registration_token"],
        "firmware_version": __version__,
        "mesh": {
            key: snapshot.get(key)
            for key in ("lxmf_address", "propagation_node", "shell_address", "transport", "cameras")
            if snapshot.get(key) is not None
        },
    }
    interfaces = snapshot.get("interfaces")
    if interfaces:
        payload["mesh"]["interfaces"] = [
            {"name": str(i["name"])[:100], "up": bool(i["up"])} for i in interfaces[:32]
        ]
    if device.get("latitude") is not None and device.get("longitude") is not None:
        payload["latitude"] = device["latitude"]
        payload["longitude"] = device["longitude"]
    return payload


def send(device: dict, snapshot: dict, session: requests.Session | None = None) -> None:
    url = f"{device['backend_url'].rstrip('/')}/api/field-devices/{device['device_id']}/status"
    try:
        response = (session or requests).post(
            url, json=build_payload(device, snapshot), headers={"Accept": "application/json"}, timeout=15
        )
    except requests.RequestException as exc:
        raise HeartbeatError(f"Couldn't reach Project Aziz: {exc}") from exc
    if not response.ok:
        raise HeartbeatError(f"Heartbeat rejected ({response.status_code}): {response.text[:200]}")
