"""Checks GitHub Releases for a newer Eyes Of Aziz, so people running an old
build hear about fixes. The status page and setup dashboard show a banner
and the bridge logs a line; nothing is downloaded or installed.

Set EYES_OF_AZIZ_UPDATE_CHECK=0 to turn it off.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass

import requests

from . import __version__

logger = logging.getLogger(__name__)

RELEASES_URL = "https://api.github.com/repos/mastashake08/eyes-of-aziz/releases/latest"
CHECK_EVERY_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class Release:
    version: str
    url: str


def parse_version(text: str) -> tuple[int, ...] | None:
    """"v1.2.3" or "1.2.3" as (1, 2, 3); None for anything else."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", text.strip())
    return tuple(int(part) for part in match.groups()) if match else None


def updates_enabled(env: Mapping[str, str] = os.environ) -> bool:
    return env.get("EYES_OF_AZIZ_UPDATE_CHECK", "1").strip().lower() not in ("0", "false", "no", "off")


def check_for_update(current: str = __version__, session: requests.Session | None = None) -> Release | None:
    """The latest release, if it's newer than this one. None when it isn't,
    or when GitHub can't be reached -- an update check never stops a camera."""
    try:
        response = (session or requests).get(
            RELEASES_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": f"eyes-of-aziz/{current}"},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.debug("Update check failed: %s", exc)
        return None

    latest, running = parse_version(str(data.get("tag_name", ""))), parse_version(current)
    if latest is None or running is None or latest <= running:
        return None
    return Release(version=".".join(map(str, latest)), url=str(data.get("html_url", "")))


class UpdateChecker:
    """Checks on a background thread now and once a day after."""

    def __init__(self, check=check_for_update, interval_seconds: float = CHECK_EVERY_SECONDS) -> None:
        self._check = check
        self._interval = interval_seconds
        self._stop = threading.Event()
        self.available: Release | None = None

    def start(self) -> UpdateChecker:
        if updates_enabled():
            threading.Thread(target=self._run, name="update-check", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def check_now(self) -> Release | None:
        release = self._check()
        if release and release != self.available:
            logger.warning(
                "Eyes Of Aziz %s is available (this is %s). Download it: %s",
                release.version, __version__, release.url,
            )
        self.available = release
        return release

    def _run(self) -> None:
        while True:
            self.check_now()
            if self._stop.wait(self._interval):
                return
