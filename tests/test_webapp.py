import pytest

from eyes_of_aziz.account_client import AccountError
from eyes_of_aziz.discovery import CameraDetails, CameraProbeError, DiscoveredDevice
from eyes_of_aziz.webapp import create_app

DEVICE = DiscoveredDevice(xaddr="http://192.168.1.50:80/onvif/device_service", host="192.168.1.50", port=80)
DETAILS = CameraDetails(manufacturer="Acme", model="Cam1", rtsp_url="rtsp://192.168.1.50/stream1")


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


@pytest.fixture
def account_client():
    return _FakeAccountClient("https://projectaziz.com")


def _make_app(tmp_path, account_client, devices=None, scan_error=None, probe_result=None, probe_error=None):
    def scan_network_fn():
        if scan_error:
            raise scan_error
        return devices if devices is not None else [DEVICE]

    def probe_camera_fn(_device, _username, _password):
        if probe_error:
            raise probe_error
        return probe_result or DETAILS

    return create_app(
        account_client_factory=lambda _backend_url: account_client,
        scan_network_fn=scan_network_fn,
        probe_camera_fn=probe_camera_fn,
        output_dir=tmp_path / "cameras",
    )


def _login(client):
    return client.post(
        "/login",
        data={"backend_url": "https://projectaziz.com", "email": "person@example.com", "password": "correct-password"},
    )


def test_index_redirects_to_login_when_logged_out(tmp_path, account_client):
    app = _make_app(tmp_path, account_client)
    response = app.test_client().get("/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/login"


def test_login_success_redirects_to_cameras(tmp_path, account_client):
    app = _make_app(tmp_path, account_client)
    client = app.test_client()

    response = _login(client)

    assert response.status_code == 302
    assert response.headers["Location"] == "/cameras"
    assert account_client.login_calls == [("person@example.com", "correct-password")]


def test_login_failure_shows_error_on_the_login_page(tmp_path):
    account_client = _FakeAccountClient("https://projectaziz.com", login_error=AccountError("bad credentials"))
    app = _make_app(tmp_path, account_client)
    client = app.test_client()

    _login(client)
    response = client.get("/login")

    assert b"bad credentials" in response.data


def test_cameras_page_requires_login(tmp_path, account_client):
    app = _make_app(tmp_path, account_client)
    response = app.test_client().get("/cameras")
    assert response.status_code == 302
    assert response.headers["Location"] == "/login"


def test_scan_lists_discovered_devices(tmp_path, account_client):
    app = _make_app(tmp_path, account_client)
    client = app.test_client()
    _login(client)

    client.post("/scan")
    response = client.get("/cameras")

    assert b"192.168.1.50" in response.data
    assert b"Add this device" in response.data


def test_scan_reports_an_error_without_crashing(tmp_path, account_client):
    app = _make_app(tmp_path, account_client, scan_error=RuntimeError("network unreachable"))
    client = app.test_client()
    _login(client)

    client.post("/scan")
    response = client.get("/cameras")

    assert b"network unreachable" in response.data


def test_scan_with_no_results_shows_the_no_devices_message(tmp_path, account_client):
    app = _make_app(tmp_path, account_client, devices=[])
    client = app.test_client()
    _login(client)

    client.post("/scan")
    response = client.get("/cameras")

    assert b"No devices responded" in response.data


def test_full_flow_add_confirm_register(tmp_path, account_client):
    app = _make_app(tmp_path, account_client)
    client = app.test_client()
    _login(client)
    client.post("/scan")

    add_response = client.post("/cameras/0/add", data={"username": "admin", "password": "cam-password"})
    assert add_response.status_code == 302
    assert add_response.headers["Location"] == "/cameras/0/confirm"

    confirm_page = client.get("/cameras/0/confirm")
    assert b"Acme Cam1" in confirm_page.data
    assert b"rtsp://192.168.1.50/stream1" in confirm_page.data

    register_response = client.post("/cameras/0/register", data={"device_id": "acme-cam1"})
    assert register_response.status_code == 302
    assert register_response.headers["Location"] == "/cameras"

    assert account_client.register_calls == [("bearer-token", "acme-cam1")]
    config_path = tmp_path / "cameras" / "acme-cam1.env"
    assert config_path.exists()

    dashboard = client.get("/cameras")
    assert b"acme-cam1" in dashboard.data
    assert b"Registered" in dashboard.data


def test_add_camera_shows_probe_error_and_keeps_device_addable(tmp_path, account_client):
    app = _make_app(tmp_path, account_client, probe_error=CameraProbeError("did not respond like an ONVIF camera"))
    client = app.test_client()
    _login(client)
    client.post("/scan")

    response = client.post("/cameras/0/add", data={"username": "admin", "password": "wrong"}, follow_redirects=True)

    assert b"did not respond like an ONVIF camera" in response.data


def test_register_failure_shows_error_and_does_not_write_a_config(tmp_path):
    account_client = _FakeAccountClient(
        "https://projectaziz.com",
        register_error=AccountError("The device id has already been taken."),
    )
    app = _make_app(tmp_path, account_client)
    client = app.test_client()
    _login(client)
    client.post("/scan")
    client.post("/cameras/0/add", data={"username": "admin", "password": "cam-password"})

    response = client.post("/cameras/0/register", data={"device_id": "acme-cam1"})

    assert b"already been taken" in response.data
    assert not (tmp_path / "cameras").exists()


def test_logout_clears_state(tmp_path, account_client):
    app = _make_app(tmp_path, account_client)
    client = app.test_client()
    _login(client)
    client.post("/scan")

    client.post("/logout")

    assert client.get("/cameras").headers["Location"] == "/login"
