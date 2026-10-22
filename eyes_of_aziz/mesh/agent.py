"""The bridge's LXMF agent: a long-running process attached to the local
rnsd (shared instance).

- Receives messages into the inbox and answers commands (commands.py).
- Sends what the CLI queues. Delivery is direct first; if that fails and the
  recipient is offline, the message goes to this bridge's own propagation
  node (lxmd), which holds it until the recipient's phone syncs.
- Announces its address, sends heartbeats to Project Aziz, and writes
  status.json for the status page.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import LXMF
import RNS

from .. import __version__
from . import commands, hardware, heartbeat, interfaces
from .store import MeshHome

logger = logging.getLogger(__name__)

ANNOUNCE_EVERY = 15 * 60
FIRST_HEARTBEAT_AFTER = 30
PATH_WAIT = 300
SNAPSHOT_EVERY = 15
SHELL_TIMEOUT = 120
LXMD_DIR_ENV = "EYES_OF_AZIZ_LXMD_DIR"
DEFAULT_LXMD_DIR = Path.home() / ".lxmd"


def propagation_node_hash(lxmd_dir: Path) -> bytes | None:
    """The local lxmd's propagation destination, from its identity file."""
    identity_path = lxmd_dir / "identity"
    if not identity_path.exists():
        return None
    identity = RNS.Identity.from_file(str(identity_path))
    if identity is None:
        return None
    return RNS.Destination.hash_from_name_and_identity("lxmf.propagation", identity)


class MeshAgent:
    def __init__(self, home: MeshHome, display_name: str | None = None) -> None:
        self.home = home
        self.home.ensure()
        self.reticulum = RNS.Reticulum()
        identity_path = home.path("identity")
        if identity_path.exists():
            self.identity = RNS.Identity.from_file(str(identity_path))
        else:
            self.identity = RNS.Identity()
            self.identity.to_file(str(identity_path))

        device = home.read_json("device.json") or {}
        name = display_name or device.get("label") or f"Eyes Of Aziz Bridge {socket.gethostname()}"
        self.router = LXMF.LXMRouter(identity=self.identity, storagepath=str(home.path("lxmf")))
        self.destination = self.router.register_delivery_identity(self.identity, display_name=name)
        self.router.register_delivery_callback(self._on_message)
        self.address = RNS.hexrep(self.destination.hash, delimit=False)
        home.write_text("address", self.address)

        self.propagation_node = propagation_node_hash(Path(os.environ.get(LXMD_DIR_ENV) or DEFAULT_LXMD_DIR))
        if self.propagation_node:
            self.router.set_outbound_propagation_node(self.propagation_node)

        self.cameras = hardware.this_machine_supports_cameras()
        self._waiting: list[tuple[bytes, str, str, float]] = []
        self._lock = threading.Lock()
        self._last_announce = 0.0
        self._next_heartbeat = time.time() + FIRST_HEARTBEAT_AFTER
        self._heartbeat_error: str | None = None
        self._last_heartbeat_at: str | None = None
        self._last_snapshot = 0.0
        logger.info("Eyes Of Aziz Bridge listening on %s", self.address)

    # --- sending ----------------------------------------------------------------

    def send(self, recipient: bytes, text: str, title: str = "") -> None:
        if not self._try_send(recipient, text, title):
            RNS.Transport.request_path(recipient)
            with self._lock:
                self._waiting.append((recipient, text, title, time.time()))

    def _try_send(self, recipient: bytes, text: str, title: str, method=None) -> bool:
        their_identity = RNS.Identity.recall(recipient)
        if their_identity is None:
            return False
        destination = RNS.Destination(
            their_identity, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery"
        )
        message = LXMF.LXMessage(
            destination, self.destination, text, title, desired_method=method or LXMF.LXMessage.DIRECT
        )
        message.register_delivery_callback(self._on_delivered)
        message.register_failed_callback(self._on_failed)
        self.router.handle_outbound(message)
        return True

    def _record(self, message, state: str) -> None:
        self.home.log("sent.jsonl", {
            "to": RNS.hexrep(message.destination_hash, delimit=False),
            "state": state,
            "text": message.content_as_string(),
        })

    def _on_delivered(self, message) -> None:
        propagated = message.desired_method == LXMF.LXMessage.PROPAGATED
        self._record(message, "stored_on_node" if propagated else "delivered")

    def _on_failed(self, message) -> None:
        # Recipient offline: leave it on our propagation node for their next sync.
        if self.propagation_node and message.desired_method != LXMF.LXMessage.PROPAGATED:
            self._record(message, "offline_storing")
            self._try_send(
                message.destination_hash, message.content_as_string(), message.title_as_string(),
                method=LXMF.LXMessage.PROPAGATED,
            )
            return
        self._record(message, "failed")

    # --- receiving ----------------------------------------------------------------

    def _on_message(self, message) -> None:
        sender = RNS.hexrep(message.source_hash, delimit=False)
        text = message.content_as_string()
        signed = bool(message.signature_validated)
        self.home.log("inbox.jsonl", {
            "from": sender, "title": message.title_as_string(), "text": text, "signed": signed,
        })
        decision = commands.decide(text, sender, signed, self.home.allowed())
        if decision.reply:
            self.send(message.source_hash, decision.reply)
        if decision.shell:
            logger.info("Running for %s: %r", sender, decision.shell)
            threading.Thread(target=self._run_shell, args=(message.source_hash, decision.shell), daemon=True).start()

    def _run_shell(self, recipient: bytes, command: str) -> None:
        try:
            done = subprocess.run(
                ["bash", "-lc", command], capture_output=True, text=True,
                timeout=SHELL_TIMEOUT, cwd=str(Path.home()), check=False,
            )
            reply = commands.format_shell_reply(done.returncode, done.stdout + done.stderr, SHELL_TIMEOUT)
        except subprocess.TimeoutExpired:
            reply = commands.format_shell_reply(None, "", SHELL_TIMEOUT)
        self.send(recipient, reply, "sh")

    # --- housekeeping -------------------------------------------------------------

    def snapshot(self) -> dict:
        try:
            stats = self.reticulum.get_interface_stats()
        except Exception:  # noqa: BLE001 - the shared instance may be restarting; report no stats
            stats = None
        return {
            "lxmf_address": self.address,
            "propagation_node": RNS.hexrep(self.propagation_node, delimit=False) if self.propagation_node else None,
            "shell_address": self.home.read_text("shell_address"),
            "transport": interfaces.transport_enabled(stats),
            "cameras": self.cameras,
            "interfaces": interfaces.summarize(stats),
            "version": __version__,
            "last_heartbeat_at": self._last_heartbeat_at,
            "heartbeat_error": self._heartbeat_error,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }

    def tick(self) -> None:
        now = time.time()
        if now - self._last_announce > ANNOUNCE_EVERY:
            self.router.announce(self.destination.hash)
            self._last_announce = now

        for item in self.home.take_outbox():
            if item.get("announce"):
                self.router.announce(self.destination.hash)
                continue
            try:
                recipient = bytes.fromhex(item["to"])
            except (KeyError, ValueError, TypeError):
                continue
            self.home.log("sent.jsonl", {"to": item["to"], "state": "queued", "text": item.get("text", "")})
            self.send(recipient, str(item.get("text", "")), str(item.get("title", "")))

        with self._lock:
            for item in list(self._waiting):
                if self._try_send(item[0], item[1], item[2]):
                    self._waiting.remove(item)
                elif now - item[3] > PATH_WAIT:
                    self._waiting.remove(item)
                    self.home.log("sent.jsonl", {"to": item[0].hex(), "state": "no_path", "text": item[1]})

        heartbeat_due = now >= self._next_heartbeat
        if not heartbeat_due and now - self._last_snapshot < SNAPSHOT_EVERY:
            return
        self._last_snapshot = now
        snapshot = self.snapshot()
        if heartbeat_due:
            self._next_heartbeat = now + heartbeat.INTERVAL_SECONDS
            device = self.home.read_json("device.json")
            if device:
                try:
                    heartbeat.send(device, snapshot)
                    self._heartbeat_error = None
                    self._last_heartbeat_at = snapshot["updated_at"]
                except heartbeat.HeartbeatError as exc:
                    self._heartbeat_error = str(exc)
                    logger.warning("%s", exc)
        self.home.write_json("status.json", snapshot)

    def run_forever(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:
                logger.exception("Agent housekeeping failed; carrying on")
            time.sleep(2)
