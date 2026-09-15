

from eyes_of_aziz.account_client import AccountError
from eyes_of_aziz.discovery import CameraDetails, CameraProbeError, DiscoveredDevice
from eyes_of_aziz.setup import (
    WizardIO,
    run_wizard,
    slugify_device_id,
    write_camera_env_file,
)

DEVICE = DiscoveredDevice(xaddr="http://192.168.1.50:80/onvif/device_service", host="192.168.1.50", port=80)
DETAILS = CameraDetails(manufacturer="Acme", model="Cam1", rtsp_url="rtsp://192.168.1.50/stream1")


class _ScriptedPrompts:
    """Feeds back scripted answers in order, so a test can read as a
    transcript of the conversation it expects the wizard to have."""

    def __init__(self, answers):
        self._answers = list(answers)

    def __call__(self, _message):
        return self._answers.pop(0)


class _FakeAccountClient:
    def __init__(self, backend_url, login_result="bearer-token", register_result=None, login_error=None, register_error=None):
        self.backend_url = backend_url
        self._login_result = login_result
        self._register_result = register_result or {
            "device_id": "acme-cam1",
            "registration_token": "reg-token-123",
        }
        self._login_error = login_error
        self._register_error = register_error
        self.login_calls = []
        self.register_calls = []

    def login(self, email, password):
        self.login_calls.append((email, password))
        if self._login_error:
            raise self._login_error
        return self._login_result

    def register_camera(self, bearer_token, device_id, firmware_version=None):
        self.register_calls.append((bearer_token, device_id))
        if self._register_error:
            raise self._register_error
        return self._register_result


def _io(tmp_path, prompts, account_client=None, devices=None, probe_result=None, probe_error=None):
    account_client = account_client or _FakeAccountClient("https://projectaziz.com")

    def probe_camera(_device, _username, _password):
        if probe_error:
            raise probe_error
        return probe_result or DETAILS

    # prompt and prompt_password must share one queue, popped in true call
    # order -- the wizard interleaves calls to both, and a real terminal
    # session has exactly one such sequence regardless of which prompt was
    # hidden vs echoed.
    shared_prompts = _ScriptedPrompts(prompts)

    return WizardIO(
        prompt=shared_prompts,
        prompt_password=shared_prompts,
        print=lambda _msg: None,
        scan_network=lambda **_kwargs: devices if devices is not None else [DEVICE],
        probe_camera=probe_camera,
        account_client_factory=lambda _backend_url: account_client,
        output_dir=tmp_path / "cameras",
    )


def test_slugify_device_id_lowercases_and_replaces_separators():
    assert slugify_device_id("Acme Cam1 (Front Door)") == "acme-cam1-front-door"


def test_slugify_device_id_falls_back_when_nothing_survives():
    assert slugify_device_id("!!!") == "camera"


def test_write_camera_env_file_writes_expected_vars_and_restricts_permissions(tmp_path):
    path = write_camera_env_file(
        tmp_path / "cameras",
        device_id="front-door",
        backend_url="https://projectaziz.com",
        registration_token="reg-token-123",
        rtsp_url="rtsp://192.168.1.50/stream1",
    )

    content = path.read_text()
    assert "EYES_OF_AZIZ_BACKEND_URL=https://projectaziz.com" in content
    assert "EYES_OF_AZIZ_DEVICE_ID=front-door" in content
    assert "EYES_OF_AZIZ_REGISTRATION_TOKEN=reg-token-123" in content
    assert "EYES_OF_AZIZ_RTSP_URL=rtsp://192.168.1.50/stream1" in content
    assert (path.stat().st_mode & 0o777) == 0o600


def test_run_wizard_end_to_end_registers_a_discovered_camera(tmp_path):
    account_client = _FakeAccountClient("https://projectaziz.com")
    prompts = [
        "",  # backend URL -> use default
        "person@example.com",
        "correct-password",
        "y",  # add this device?
        "admin",  # camera username
        "cam-password",  # camera password
        "",  # accept default device name
    ]
    io = _io(tmp_path, prompts, account_client=account_client)

    exit_code = run_wizard(io)

    assert exit_code == 0
    assert account_client.login_calls == [("person@example.com", "correct-password")]
    assert account_client.register_calls == [("bearer-token", "acme-cam1")]

    config_path = tmp_path / "cameras" / "acme-cam1.env"
    assert config_path.exists()
    assert "EYES_OF_AZIZ_RTSP_URL=rtsp://192.168.1.50/stream1" in config_path.read_text()


def test_run_wizard_skips_a_device_the_user_declines(tmp_path):
    account_client = _FakeAccountClient("https://projectaziz.com")
    prompts = ["", "person@example.com", "correct-password", "n"]
    io = _io(tmp_path, prompts, account_client=account_client)

    exit_code = run_wizard(io)

    assert exit_code == 0
    assert account_client.register_calls == []
    assert not (tmp_path / "cameras").exists()


def test_run_wizard_stops_early_on_login_failure(tmp_path):
    account_client = _FakeAccountClient("https://projectaziz.com", login_error=AccountError("bad credentials"))
    prompts = ["", "person@example.com", "wrong-password"]
    io = _io(tmp_path, prompts, account_client=account_client)

    exit_code = run_wizard(io)

    assert exit_code == 1
    assert account_client.register_calls == []


def test_run_wizard_continues_past_a_camera_that_fails_to_probe(tmp_path):
    account_client = _FakeAccountClient("https://projectaziz.com")
    prompts = [
        "",
        "person@example.com",
        "correct-password",
        "y",
        "admin",
        "wrong-password",
    ]
    io = _io(
        tmp_path,
        prompts,
        account_client=account_client,
        probe_error=CameraProbeError("did not respond like an ONVIF camera"),
    )

    exit_code = run_wizard(io)

    assert exit_code == 0
    assert account_client.register_calls == []


def test_run_wizard_reports_a_registration_failure_without_crashing(tmp_path):
    account_client = _FakeAccountClient(
        "https://projectaziz.com",
        register_error=AccountError("The device id has already been taken."),
    )
    prompts = ["", "person@example.com", "correct-password", "y", "admin", "cam-password", ""]
    io = _io(tmp_path, prompts, account_client=account_client)

    exit_code = run_wizard(io)

    assert exit_code == 0
    assert not (tmp_path / "cameras").exists()


def test_run_wizard_reports_no_devices_found(tmp_path):
    account_client = _FakeAccountClient("https://projectaziz.com")
    prompts = ["", "person@example.com", "correct-password"]
    io = _io(tmp_path, prompts, account_client=account_client, devices=[])

    exit_code = run_wizard(io)

    assert exit_code == 0
    assert account_client.register_calls == []
