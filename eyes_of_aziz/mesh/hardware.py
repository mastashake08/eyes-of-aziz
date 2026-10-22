"""What this machine is, and whether it's up to running cameras."""

from __future__ import annotations

import re
from pathlib import Path

MODEL_PATH = Path("/proc/device-tree/model")
MEMINFO_PATH = Path("/proc/meminfo")

# Face detection on a few camera streams needs a Pi 4 or 5. A Pi 3 (1 GB,
# and a much slower CPU) runs the mesh side fine, but not the cameras.
_CAMERA_CAPABLE_PI = re.compile(r"Raspberry Pi (4|5|400|500)\b|Compute Module (4|5)\b")
MIN_CAMERA_MEMORY = 1_800_000_000  # "2 GB" machines report a little under


def read_model(path: Path = MODEL_PATH) -> str | None:
    try:
        return path.read_text(errors="replace").strip("\x00\n ") or None
    except OSError:
        return None


def total_memory(path: Path = MEMINFO_PATH) -> int | None:
    """Bytes of RAM, from /proc/meminfo."""
    try:
        for line in path.read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def cameras_supported(model: str | None, memory: int | None) -> bool:
    """A Pi 4/5 can run the camera bridge; an older Pi can't. Anything else
    (a mini PC, a NAS) can if it has about 2 GB of RAM."""
    if model and model.startswith("Raspberry Pi"):
        return bool(_CAMERA_CAPABLE_PI.search(model))
    return memory is not None and memory >= MIN_CAMERA_MEMORY


def this_machine_supports_cameras() -> bool:
    return cameras_supported(read_model(), total_memory())
