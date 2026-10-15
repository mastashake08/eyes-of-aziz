"""Limits what a camera looks at, and when:

- Privacy masks: rectangles of the picture (a neighbour's window, the
  street) blacked out before anything looks for faces, so nobody in them
  is ever sent. EYES_OF_AZIZ_PRIVACY_MASKS="x,y,w,h; ..." as fractions of
  the frame, e.g. "0.7,0,0.3,0.5" is the top-right corner.
- Schedules: the times a camera runs, in this computer's local time.
  EYES_OF_AZIZ_SCHEDULE="mon-fri 18:00-07:00; sat,sun" -- a range ending
  before it starts runs overnight into the next day; days without times
  mean all day. Unset means always.
- Pausing: a <device_id>.paused file next to the camera configs pauses it
  until the file is removed, from the status page or with
  `eyes-of-aziz-bridge --pause/--resume`. A file, so a pause survives
  restarts and reaches a bridge already running as a service.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

Mask = tuple[float, float, float, float]


class PrivacyConfigError(ValueError):
    """A mask or schedule couldn't be understood."""


def parse_masks(text: str | None) -> tuple[Mask, ...]:
    masks = []
    for part in (text or "").split(";"):
        if not part.strip():
            continue
        try:
            x, y, w, h = (float(value) for value in part.split(","))
        except ValueError as exc:
            raise PrivacyConfigError(f'Privacy mask "{part.strip()}" should be x,y,width,height') from exc
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.0001 and y + h <= 1.0001):
            raise PrivacyConfigError(
                f'Privacy mask "{part.strip()}" must be fractions of the picture (0-1) that stay inside it'
            )
        masks.append((x, y, w, h))
    return tuple(masks)


def apply_masks(frame: np.ndarray, masks: tuple[Mask, ...]) -> np.ndarray:
    """A copy of the frame with every mask blacked out."""
    if not masks:
        return frame
    masked = frame.copy()
    height, width = frame.shape[:2]
    for x, y, w, h in masks:
        left, top = int(x * width), int(y * height)
        right, bottom = round((x + w) * width), round((y + h) * height)
        masked[top:bottom, left:right] = 0
    return masked


@dataclass(frozen=True)
class Window:
    days: frozenset[int]  # 0 = Monday
    start: int  # minutes since midnight
    end: int  # minutes since midnight; <= start runs past midnight


@dataclass(frozen=True)
class Schedule:
    windows: tuple[Window, ...] = ()

    @property
    def always(self) -> bool:
        return not self.windows

    def is_active(self, now: datetime) -> bool:
        if self.always:
            return True
        day, minute = now.weekday(), now.hour * 60 + now.minute
        for window in self.windows:
            if window.start < window.end:
                if day in window.days and window.start <= minute < window.end:
                    return True
            else:
                # Overnight: the evening of a listed day, and the morning after it.
                if day in window.days and minute >= window.start:
                    return True
                if (day - 1) % 7 in window.days and minute < window.end:
                    return True
        return False


def parse_schedule(text: str | None) -> Schedule:
    windows = []
    for part in (text or "").split(";"):
        part = part.strip().lower()
        if not part:
            continue
        match = re.fullmatch(r"([a-z,\-\s]+?)(?:\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2}))?", part)
        if not match:
            raise PrivacyConfigError(f'Schedule "{part}" should look like "mon-fri 18:00-07:00"')
        days = _parse_days(match.group(1), part)
        if match.group(2):
            start, end = _minutes(match.group(2), part), _minutes(match.group(3), part)
            if start == end:
                raise PrivacyConfigError(f'Schedule "{part}" starts and ends at the same time')
        else:
            start, end = 0, 24 * 60
        windows.append(Window(frozenset(days), start, end))
    return Schedule(tuple(windows))


def _parse_days(text: str, part: str) -> set[int]:
    days: set[int] = set()
    for item in text.replace(" ", "").split(","):
        if item in ("daily", "every", "all"):
            days.update(range(7))
            continue
        first, _, last = item.partition("-")
        if first not in DAYS or (last and last not in DAYS):
            raise PrivacyConfigError(f'Schedule "{part}": days are mon, tue, wed, thu, fri, sat, sun or daily')
        start = DAYS.index(first)
        stop = DAYS.index(last) if last else start
        day = start
        while True:
            days.add(day)
            if day == stop:
                break
            day = (day + 1) % 7  # "fri-mon" wraps round the weekend
    return days


def _minutes(text: str, part: str) -> int:
    hours, minutes = (int(value) for value in text.split(":"))
    if not (0 <= hours <= 24 and 0 <= minutes < 60) or (hours == 24 and minutes):
        raise PrivacyConfigError(f'Schedule "{part}": {text} isn\'t a time of day')
    return hours * 60 + minutes


def pause_file(cameras_dir: Path, device_id: str) -> Path:
    return cameras_dir / f"{device_id}.paused"


def set_paused(cameras_dir: Path, device_id: str, paused: bool) -> None:
    path = pause_file(cameras_dir, device_id)
    if paused:
        cameras_dir.mkdir(parents=True, exist_ok=True)
        path.touch()
    else:
        path.unlink(missing_ok=True)
