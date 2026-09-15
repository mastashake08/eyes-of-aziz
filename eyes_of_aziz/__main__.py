"""CLI entrypoint: `python -m eyes_of_aziz` or the `eyes-of-aziz-bridge` script."""

from __future__ import annotations

import logging
import os
import signal
import sys
import types

from dotenv import load_dotenv

from .bridge import CameraBridge
from .config import BridgeConfig, ConfigError


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("EYES_OF_AZIZ_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    load_dotenv()

    try:
        config = BridgeConfig.from_env(os.environ)
    except ConfigError as exc:
        logging.getLogger(__name__).error(str(exc))
        return 1

    bridge = CameraBridge(config)

    def _handle_signal(signum: int, _frame: types.FrameType | None) -> None:
        logging.getLogger(__name__).info("Received signal %s, shutting down", signum)
        bridge.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    bridge.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
