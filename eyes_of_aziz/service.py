"""Installs the bridge as a background service that starts with the
computer (or at login) and is restarted if it stops, running every camera
in cameras/ (`eyes-of-aziz-bridge --all`):

- macOS: a launchd agent in ~/Library/LaunchAgents
- Linux: a systemd user unit in ~/.config/systemd/user
- Windows: a scheduled task that runs at logon

Per user, so no administrator rights are needed.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

LABEL = "com.projectaziz.eyes-of-aziz"
SYSTEMD_UNIT = "eyes-of-aziz.service"
WINDOWS_TASK = "Eyes Of Aziz"

Runner = Callable[..., subprocess.CompletedProcess]


class ServiceError(RuntimeError):
    """Installing or removing the service failed."""


def bridge_command(cameras_dir: Path) -> list[str]:
    """How to start the bridge for all cameras, from source or a packaged build."""
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable)
        # The setup app installs the service too; it runs the bridge next to it.
        if "setup" in executable.name:
            executable = executable.with_name(executable.name.replace("setup", "bridge"))
        command = [str(executable)]
    else:
        command = [sys.executable, "-m", "eyes_of_aziz"]
    return command + ["--all", "--cameras-dir", str(cameras_dir.resolve())]


def install(cameras_dir: Path, platform: str = sys.platform, home: Path | None = None, run: Runner = subprocess.run) -> str:
    """Installs and starts the service. Returns a sentence describing what was done."""
    home = home or Path.home()
    command = bridge_command(cameras_dir)
    working_dir = str(cameras_dir.resolve().parent)

    if platform == "darwin":
        plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        log = home / "Library" / "Logs" / "eyes-of-aziz.log"
        plist.write_text(_launchd_plist(command, working_dir, str(log)))
        domain = f"gui/{os.getuid()}"
        run(["launchctl", "bootout", domain, str(plist)], capture_output=True)  # replace an older copy
        _check(run(["launchctl", "bootstrap", domain, str(plist)], capture_output=True, text=True))
        return f"Installed: Eyes Of Aziz starts when you log in to this Mac. Log: {log}"

    if platform.startswith("linux"):
        unit = home / ".config" / "systemd" / "user" / SYSTEMD_UNIT
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(_systemd_unit(command, working_dir))
        _check(run(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True))
        _check(run(["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT], capture_output=True, text=True))
        return (
            "Installed: Eyes Of Aziz runs as a systemd user service. To keep it running when "
            f"nobody is logged in, run: loginctl enable-linger {os.environ.get('USER', '$USER')}"
        )

    if platform == "win32":
        _check(run(
            ["schtasks", "/Create", "/TN", WINDOWS_TASK, "/TR", subprocess.list2cmdline(command),
             "/SC", "ONLOGON", "/RL", "LIMITED", "/F"],
            capture_output=True, text=True,
        ))
        _check(run(["schtasks", "/Run", "/TN", WINDOWS_TASK], capture_output=True, text=True))
        return "Installed: Eyes Of Aziz starts when you sign in to Windows."

    raise ServiceError(f"Running as a service isn't supported on {platform}.")


def uninstall(platform: str = sys.platform, home: Path | None = None, run: Runner = subprocess.run) -> str:
    """Stops and removes the service. Returns a sentence describing what was done."""
    home = home or Path.home()

    if platform == "darwin":
        plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)], capture_output=True)
        plist.unlink(missing_ok=True)
        return "Removed: Eyes Of Aziz no longer starts automatically."

    if platform.startswith("linux"):
        run(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT], capture_output=True)
        (home / ".config" / "systemd" / "user" / SYSTEMD_UNIT).unlink(missing_ok=True)
        run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        return "Removed: Eyes Of Aziz no longer starts automatically."

    if platform == "win32":
        run(["schtasks", "/End", "/TN", WINDOWS_TASK], capture_output=True)
        _check(run(["schtasks", "/Delete", "/TN", WINDOWS_TASK, "/F"], capture_output=True, text=True))
        return "Removed: Eyes Of Aziz no longer starts automatically."

    raise ServiceError(f"Running as a service isn't supported on {platform}.")


def _check(result: subprocess.CompletedProcess) -> None:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ServiceError(f"{' '.join(map(str, result.args[:3]))} failed: {detail or f'exit {result.returncode}'}")


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _launchd_plist(command: list[str], working_dir: str, log: str) -> str:
    arguments = "".join(f"\n        <string>{_xml(part)}</string>" for part in command)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>{arguments}
    </array>
    <key>WorkingDirectory</key>
    <string>{_xml(working_dir)}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{_xml(log)}</string>
    <key>StandardErrorPath</key>
    <string>{_xml(log)}</string>
</dict>
</plist>
"""


def _systemd_unit(command: list[str], working_dir: str) -> str:
    return f"""[Unit]
Description=Eyes Of Aziz camera bridge for Project Aziz
After=network-online.target
Wants=network-online.target

[Service]
ExecStart={shlex.join(command)}
WorkingDirectory={working_dir}
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
"""
