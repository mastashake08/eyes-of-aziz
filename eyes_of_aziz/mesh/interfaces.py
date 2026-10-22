"""Turns Reticulum's interface stats into the short list the app and status
page show: [{"name": "RNode LoRa", "up": True}, ...]."""

from __future__ import annotations

import re

# The shared instance's own plumbing and per-client spawned interfaces
# aren't links anyone configured.
_SKIP = re.compile(r"^(Shared Instance|LocalInterface|Local Shared)|Client on|Spawned", re.IGNORECASE)
_BRACKETED = re.compile(r"^\w+\[(.*)\]$")


def short_name(full_name: str) -> str:
    """'BackboneInterface[Backbone ProjectAziz/projectaziz.com:4242]' -> 'Backbone ProjectAziz'."""
    match = _BRACKETED.match(full_name.strip())
    inner = match.group(1) if match else full_name.strip()
    return inner.split("/", 1)[0].strip() or full_name


def summarize(stats: dict | None) -> list[dict]:
    if not stats:
        return []
    found = []
    for entry in stats.get("interfaces") or []:
        name = str(entry.get("name") or "")
        if not name or _SKIP.search(name):
            continue
        found.append({"name": short_name(name), "up": bool(entry.get("status"))})
    return found


def transport_enabled(stats: dict | None) -> bool | None:
    if not stats:
        return None
    return stats.get("transport_id") is not None
