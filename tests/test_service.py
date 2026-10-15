import subprocess
import sys

import pytest

from eyes_of_aziz import service


class FakeRun:
    def __init__(self, failing=None):
        self.calls = []
        self._failing = failing

    def __call__(self, args, **_kwargs):
        self.calls.append(args)
        failed = self._failing and self._failing in args
        return subprocess.CompletedProcess(args, 1 if failed else 0, stdout="", stderr="boom" if failed else "")


def test_bridge_command_from_source_runs_all_cameras(tmp_path):
    command = service.bridge_command(tmp_path / "cameras")

    assert command[:3] == [sys.executable, "-m", "eyes_of_aziz"]
    assert command[3:] == ["--all", "--cameras-dir", str((tmp_path / "cameras").resolve())]


def test_bridge_command_from_the_packaged_setup_app_uses_the_bridge_next_to_it(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "eyes-of-aziz-setup.exe"))

    command = service.bridge_command(tmp_path / "cameras")

    assert command[0] == str(tmp_path / "eyes-of-aziz-bridge.exe")


def test_install_on_macos_writes_a_launch_agent_and_loads_it(tmp_path):
    run = FakeRun()

    message = service.install(tmp_path / "cameras", platform="darwin", home=tmp_path, run=run)

    plist = (tmp_path / "Library" / "LaunchAgents" / f"{service.LABEL}.plist").read_text()
    assert "<string>--all</string>" in plist
    assert "<key>KeepAlive</key>" in plist
    assert any(call[:2] == ["launchctl", "bootstrap"] for call in run.calls)
    assert "log in" in message


def test_install_on_linux_writes_and_enables_a_user_unit(tmp_path):
    run = FakeRun()

    service.install(tmp_path / "cameras", platform="linux", home=tmp_path, run=run)

    unit = (tmp_path / ".config" / "systemd" / "user" / service.SYSTEMD_UNIT).read_text()
    assert "--all --cameras-dir" in unit
    assert "Restart=always" in unit
    assert ["systemctl", "--user", "enable", "--now", service.SYSTEMD_UNIT] in run.calls


def test_install_on_windows_creates_a_logon_task(tmp_path):
    run = FakeRun()

    service.install(tmp_path / "cameras", platform="win32", home=tmp_path, run=run)

    create = run.calls[0]
    assert create[:2] == ["schtasks", "/Create"]
    assert "ONLOGON" in create
    assert "--all" in create[create.index("/TR") + 1]


def test_a_failing_command_is_reported(tmp_path):
    with pytest.raises(service.ServiceError, match="boom"):
        service.install(tmp_path / "cameras", platform="linux", home=tmp_path, run=FakeRun(failing="enable"))


def test_uninstall_removes_the_launch_agent(tmp_path):
    plist = tmp_path / "Library" / "LaunchAgents" / f"{service.LABEL}.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("x")

    service.uninstall(platform="darwin", home=tmp_path, run=FakeRun())

    assert not plist.exists()


def test_unsupported_platform(tmp_path):
    with pytest.raises(service.ServiceError):
        service.install(tmp_path / "cameras", platform="plan9", home=tmp_path, run=FakeRun())
