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

from . import service
from .config import BridgeConfig, ConfigError
from .status_page import StatusServer
from .supervisor import Supervisor, load_camera_configs

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


def self_test() -> int:
    """Loads the bundled face detector and renders the status page once.
    Used after packaging, so a build missing the model or the page's
    templates fails there, not for users."""
    import numpy as np

    from . import __version__
    from .faces import FaceDetector
    from .status_page import create_status_app

    try:
        FaceDetector().detect(np.zeros((240, 320, 3), dtype=np.uint8))
        page = create_status_app(list).test_client().get("/")
        if page.status_code != 200:
            raise RuntimeError(f"status page returned {page.status_code}")
    except Exception as exc:  # noqa: BLE001 - reporting any failure is the point
        print(f"eyes-of-aziz {__version__}: self-test FAILED: {exc}")
        return 1
    print(f"eyes-of-aziz {__version__}: self-test passed (face detector and status page)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="eyes-of-aziz-bridge",
        description="Pulls frames from RTSP cameras and forwards the faces in them to Project Aziz.",
    )
    parser.add_argument(
        "--env-file",
        metavar="PATH",
        help="Run just this camera's config file (default: .env, or the cameras in cameras/).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run every camera in the cameras folder (the default when there's more than one).",
    )
    parser.add_argument(
        "--cameras-dir",
        metavar="DIR",
        default="cameras",
        help="Where the camera configs are (default: cameras).",
    )
    parser.add_argument(
        "--status-port",
        type=int,
        default=5152,
        help="Port for the local status page (default: 5152).",
    )
    parser.add_argument(
        "--status-host",
        default="127.0.0.1",
        help="Address the status page listens on (default: this machine only; 0.0.0.0 in Docker).",
    )
    parser.add_argument("--no-status-page", action="store_true", help="Don't serve the status page.")
    parser.add_argument(
        "--install-service",
        action="store_true",
        help="Start every camera automatically when this computer starts (or at login), then exit.",
    )
    parser.add_argument("--uninstall-service", action="store_true", help="Stop starting automatically, then exit.")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Check this install works (loads the face detector), then exit.",
    )
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    logging.basicConfig(
        level=os.environ.get("EYES_OF_AZIZ_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cameras_dir = Path(args.cameras_dir)

    if args.install_service or args.uninstall_service:
        try:
            message = service.install(cameras_dir) if args.install_service else service.uninstall()
        except service.ServiceError as exc:
            logger.error(str(exc))
            return 1
        print(message)
        return 0

    configs, error = _configs_to_run(args, cameras_dir)
    if error:
        logger.error(error)
        return 1

    supervisor = Supervisor(configs)
    status_server = None
    if not args.no_status_page:
        try:
            status_server = StatusServer(supervisor.status, args.status_port, args.status_host)
            status_server.start()
        except OSError as exc:
            # Another bridge may already be serving it; the cameras matter more.
            logger.warning("Status page not started on port %s: %s", args.status_port, exc)
            status_server = None

    def _handle_signal(signum: int, _frame: types.FrameType | None) -> None:
        logger.info("Received signal %s, shutting down", signum)
        supervisor.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    supervisor.run()
    if status_server:
        status_server.stop()
    return 0


def _configs_to_run(args: argparse.Namespace, cameras_dir: Path) -> tuple[list[BridgeConfig], str | None]:
    """The cameras this run handles: every one in cameras_dir with --all (or
    when there's more than one and no --env-file), otherwise the single
    config resolve_env_file picks."""
    configs_found = sorted(cameras_dir.glob("*.env")) if cameras_dir.is_dir() else []
    if args.all or (not args.env_file and len(configs_found) > 1):
        configs, problems = load_camera_configs(cameras_dir)
        for problem in problems:
            logger.error("Skipping %s", problem)
        if not configs:
            return [], f"No usable camera configs in {cameras_dir}/. Run eyes-of-aziz-setup to add one."
        logger.info("Running %d camera(s) from %s/", len(configs), cameras_dir)
        return configs, None

    env_path, resolve_error = resolve_env_file(args.env_file, cameras_dir.parent)
    if resolve_error:
        return [], resolve_error

    if env_path:
        if not load_dotenv(env_path):
            return [], f"Could not read env file: {env_path}"
    else:
        load_dotenv()

    try:
        return [BridgeConfig.from_env(os.environ)], None
    except ConfigError as exc:
        return [], str(exc)


if __name__ == "__main__":
    sys.exit(main())
