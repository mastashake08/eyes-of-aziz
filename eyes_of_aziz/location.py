"""Turns what someone types for a camera's location into coordinates:
either "latitude, longitude" or a street address, looked up with
OpenStreetMap's Nominatim geocoder.
"""

from __future__ import annotations

import re

import requests

from . import __version__

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

_COORDINATES = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$")


class LocationError(ValueError):
    """The location couldn't be understood or found."""


def parse_location(text: str, session: requests.Session | None = None) -> tuple[float, float] | None:
    """Returns (latitude, longitude), or None when `text` is blank."""
    text = text.strip()
    if not text:
        return None

    match = _COORDINATES.match(text)
    if match:
        latitude, longitude = float(match.group(1)), float(match.group(2))
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise LocationError(f"{text!r} isn't a valid latitude, longitude.")
        return latitude, longitude

    return _geocode(text, session or requests.Session())


def _geocode(address: str, session: requests.Session) -> tuple[float, float]:
    try:
        response = session.get(
            NOMINATIM_URL,
            params={"q": address, "format": "json", "limit": 1},
            # Nominatim's usage policy asks every client to identify itself.
            headers={"User-Agent": f"eyes-of-aziz/{__version__} (+https://projectaziz.com)"},
            timeout=15,
        )
        response.raise_for_status()
        results = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise LocationError(f"Couldn't look up that address: {exc}") from exc

    if not results:
        raise LocationError(
            f"Couldn't find {address!r}. Try a fuller address, or enter latitude, longitude."
        )

    return float(results[0]["lat"]), float(results[0]["lon"])
