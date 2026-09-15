"""CLI entrypoint: `python -m eyes_of_aziz` or the `eyes-of-aziz-bridge` script."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import types

from dotenv import load_dotenv

from .bridge import CameraBridge
from .config import BridgeConfig, ConfigError


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="eyes-of-aziz-bridge",
        description="Pulls frames from one RTSP camera and forwards them to Project Aziz.",
    )
    parser.add_argument(
        "--env-file",
        metavar="PATH",
        help=(
            "Path to this camera's config file (default: .env in the current "
            "directory). Running more than one camera means running this "
            "once per camera, each with its own --env-file -- see "
            "`eyes-of-aziz-setup`, which generates one under cameras/ per "
            "camera you register."
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=os.environ.get("EYES_OF_AZIZ_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.env_file:
        if not load_dotenv(args.env_file):
            logging.getLogger(__name__).error("Could not read env file: %s", args.env_file)
            return 1
    else:
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
