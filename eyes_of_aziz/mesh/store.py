"""The bridge's files, shared by the agent and the CLI:

  identity         the bridge's LXMF identity (its address)
  address          its LXMF address, hex
  shell_address    rnsh's address, written by the installer
  device.json      backend registration (owner read/write only)
  allowed          LXMF addresses allowed to run admin commands, one per line
  inbox.jsonl      messages received
  sent.jsonl       send results
  outbox/          messages queued by `eyes-of-aziz-mesh send`
  status.json      the agent's latest snapshot (for the status page)
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

HOME_ENV = "EYES_OF_AZIZ_MESH_HOME"
DEFAULT_HOME = Path.home() / ".eyes-of-aziz-mesh"

_ADDRESS = re.compile(r"^[0-9a-f]{32}$")


def normalize_address(text: str) -> str:
    """'<F21A…>' or 'f21a…' -> 'f21a…'. Raises ValueError if it isn't one."""
    address = text.strip().strip("<>").replace(":", "").lower()
    if not _ADDRESS.match(address):
        raise ValueError(f"Not an LXMF address (32 hex characters): {text!r}")
    return address


@dataclass(frozen=True)
class MeshHome:
    root: Path

    @classmethod
    def default(cls) -> MeshHome:
        return cls(Path(os.environ.get(HOME_ENV) or DEFAULT_HOME))

    @property
    def outbox(self) -> Path:
        return self.root / "outbox"

    def path(self, name: str) -> Path:
        return self.root / name

    def ensure(self) -> None:
        self.outbox.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    # --- logs ---------------------------------------------------------------

    def log(self, name: str, record: dict) -> None:
        record = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), **record}
        with open(self.path(name), "a") as f:
            f.write(json.dumps(record) + "\n")

    def tail(self, name: str, count: int) -> list[dict]:
        try:
            lines = self.path(name).read_text().splitlines()[-count:]
        except FileNotFoundError:
            return []
        records = []
        for line in lines:
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
        return records

    # --- allowed list ---------------------------------------------------------

    def allowed(self) -> set[str]:
        try:
            lines = self.path("allowed").read_text().splitlines()
        except FileNotFoundError:
            return set()
        found = set()
        for line in lines:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            try:
                found.add(normalize_address(line))
            except ValueError:
                continue
        return found

    def allow(self, address: str) -> bool:
        """Adds an address. False if it was already there."""
        address = normalize_address(address)
        if address in self.allowed():
            return False
        with open(self.path("allowed"), "a") as f:
            f.write(address + "\n")
        return True

    # --- outbox ---------------------------------------------------------------

    def queue(self, item: dict) -> Path:
        """Writes an outbox item atomically (rename), so the agent never reads
        half of one."""
        self.ensure()
        tmp = self.root / f".{uuid.uuid4().hex}.tmp"
        tmp.write_text(json.dumps(item))
        target = self.outbox / f"{time.time():.6f}-{uuid.uuid4().hex[:6]}.json"
        tmp.rename(target)
        return target

    def take_outbox(self) -> list[dict]:
        """Removes and returns queued items, oldest first."""
        items = []
        for path in sorted(self.outbox.glob("*.json")):
            try:
                item = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            path.unlink(missing_ok=True)
            if isinstance(item, dict):
                items.append(item)
        return items

    # --- small files ------------------------------------------------------------

    def read_text(self, name: str) -> str | None:
        try:
            return self.path(name).read_text().strip() or None
        except FileNotFoundError:
            return None

    def write_text(self, name: str, text: str) -> None:
        self.path(name).write_text(text.strip() + "\n")

    def read_json(self, name: str) -> dict | None:
        try:
            value = json.loads(self.path(name).read_text())
        except (FileNotFoundError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def write_json(self, name: str, value: dict, private: bool = False) -> None:
        tmp = self.root / f".{name}.tmp"
        tmp.write_text(json.dumps(value, indent=2))
        if private:
            os.chmod(tmp, 0o600)
        tmp.rename(self.path(name))
