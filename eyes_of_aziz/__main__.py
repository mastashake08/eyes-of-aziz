"""CLI entrypoint: `python -m eyes_of_aziz` or the `eyes-of-aziz-bridge` script."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import types
from pathlib import Path

from dotenv import load_dotenv

from .bridge import CameraBridge
from .config import BridgeConfig, ConfigError

logger = logging.getLogger(__name__)


def resolve_env_file(explicit_path: str | None, search_dir: Path = Path(".")) -> tuple[str | None, str | None]:
    """Picks which config file to load. Returns (path, error) -- exactly one is set.

    `path=None` means "let load_dotenv() use its own default search" (an
    explicit .env in the current directory, or none). This exists so a
    packaged, double-clicked build -- which can't be handed --env-file --
    still does something sensible: if there's exactly one camera config
    under cameras/, just use it, since running the setup wizard's output
    directly is the common case for a single-camera install.
    """
    if explicit_path:
        return explicit_path, None

    if (search_dir / ".env").exists():
        return None, None

    candidates = sorted((search_dir / "cameras").glob("*.env")) if (search_dir / "cameras").is_dir() else []

    if len(candidates) == 1:
        logger.info("No --env-file given; using the only camera config found: %s", candidates[0])
        return str(candidates[0]), None

    if len(candidates) > 1:
        names = ", ".join(str(p) for p in candidates)
        return None, (
            f"Multiple camera configs found ({names}) -- run this once per "
            f"camera with --env-file pointing at each one."
        )

    return None, None


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

    env_path, resolve_error = resolve_env_file(args.env_file)
    if resolve_error:
        logger.error(resolve_error)
        return 1

    if env_path:
        if not load_dotenv(env_path):
            logger.error("Could not read env file: %s", env_path)
            return 1
    else:
        load_dotenv()

    try:
        config = BridgeConfig.from_env(os.environ)
    except ConfigError as exc:
        logger.error(str(exc))
        return 1

    bridge = CameraBridge(config)

    def _handle_signal(signum: int, _frame: types.FrameType | None) -> None:
        logger.info("Received signal %s, shutting down", signum)
        bridge.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    bridge.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
