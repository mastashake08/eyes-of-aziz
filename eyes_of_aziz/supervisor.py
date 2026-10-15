"""Runs every camera configured under cameras/ in one process, one thread
per camera, and restarts a camera's loop if it crashes. A camera the backend
rejects is not restarted: that needs someone to fix its configuration.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

from dotenv import dotenv_values

from .bridge import CameraBridge
from .config import BridgeConfig, ConfigError

logger = logging.getLogger(__name__)


def load_camera_configs(
    cameras_dir: Path, defaults: Mapping[str, str] | None = None
) -> tuple[list[BridgeConfig], list[str]]:
    """Every cameras/*.env as a BridgeConfig, plus a message per file that
    couldn't be loaded. Settings in the environment (e.g. the tuning in
    .env.example) apply to every camera unless its own file overrides them.
    """
    defaults = dict(os.environ if defaults is None else defaults)
    configs, problems = [], []
    for path in sorted(cameras_dir.glob("*.env")):
        try:
            values = {key: value for key, value in dotenv_values(path).items() if value is not None}
            configs.append(BridgeConfig.from_env({**defaults, **values}))
        except ConfigError as exc:
            problems.append(f"{path.name}: {exc}")
    return configs, problems


class Supervisor:
    def __init__(
        self,
        configs: list[BridgeConfig],
        bridge_factory: Callable[[BridgeConfig], CameraBridge] = CameraBridge,
        restart_delay_seconds: float = 10.0,
    ) -> None:
        self._configs = configs
        self._bridge_factory = bridge_factory
        self._restart_delay = restart_delay_seconds
        self._stop_event = threading.Event()
        self._bridges: dict[str, CameraBridge] = {}
        self._crashes: dict[str, str] = {}
        self._lock = threading.Lock()

    def run(self) -> None:
        """Runs until stop(), or until every camera has been rejected."""
        threads = [
            threading.Thread(target=self._keep_running, args=(config,), name=config.device_id, daemon=True)
            for config in self._configs
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            for bridge in self._bridges.values():
                bridge.stop()

    def status(self) -> list[dict]:
        with self._lock:
            rows = []
            for config in self._configs:
                bridge = self._bridges.get(config.device_id)
                row = bridge.status() if bridge else {"device_id": config.device_id, "state": "starting"}
                if config.device_id in self._crashes and row.get("state") != "running":
                    row["last_error"] = self._crashes[config.device_id]
                rows.append(row)
            return rows

    def _keep_running(self, config: BridgeConfig) -> None:
        while not self._stop_event.is_set():
            try:
                bridge = self._bridge_factory(config)
            except Exception as exc:
                logger.exception("Could not start camera %s", config.device_id)
                self._crashes[config.device_id] = f"Could not start: {exc}"
                if self._stop_event.wait(self._restart_delay):
                    return
                continue

            with self._lock:
                self._bridges[config.device_id] = bridge
            if self._stop_event.is_set():
                return

            try:
                bridge.run()
            except Exception as exc:
                logger.exception("Camera %s crashed; restarting in %.0fs", config.device_id, self._restart_delay)
                self._crashes[config.device_id] = f"Crashed, restarting: {exc}"
            else:
                if bridge.rejected:
                    logger.error("Camera %s was rejected by Project Aziz; not restarting it", config.device_id)
                    return
                if self._stop_event.is_set():
                    return

            if self._stop_event.wait(self._restart_delay):
                return
