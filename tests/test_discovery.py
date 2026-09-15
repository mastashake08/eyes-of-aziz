from types import SimpleNamespace

import pytest

from eyes_of_aziz import discovery
from eyes_of_aziz.discovery import CameraProbeError, DiscoveredDevice


class _FakeService:
    def __init__(self, xaddrs):
        self._xaddrs = xaddrs

    def getXAddrs(self):
        return self._xaddrs


class _FakeWSDiscovery:
    def __init__(self, services=None):
        self.services = services or []
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def searchServices(self, timeout):
        self.timeout_used = timeout
        return self.services

    def stop(self):
        self.stopped = True


def test_scan_network_returns_parsed_devices(monkeypatch):
    instance = _FakeWSDiscovery(
        services=[_FakeService(["http://192.168.1.50:80/onvif/device_service"])]
    )
    monkeypatch.setattr(discovery, "WSDiscovery", lambda: instance)

    devices = discovery.scan_network(timeout=2)

    assert devices == [
        DiscoveredDevice(
            xaddr="http://192.168.1.50:80/onvif/device_service", host="192.168.1.50", port=80
        )
    ]
    assert instance.started
    assert instance.stopped
    assert instance.timeout_used == 2


def test_scan_network_defaults_to_port_80_for_http_urls_without_a_port(monkeypatch):
    instance = _FakeWSDiscovery(services=[_FakeService(["http://192.168.1.50/onvif/device_service"])])
    monkeypatch.setattr(discovery, "WSDiscovery", lambda: instance)

    devices = discovery.scan_network()

    assert devices[0].port == 80


def test_scan_network_deduplicates_repeated_xaddrs(monkeypatch):
    xaddr = "http://192.168.1.50:80/onvif/device_service"
    instance = _FakeWSDiscovery(services=[_FakeService([xaddr]), _FakeService([xaddr])])
    monkeypatch.setattr(discovery, "WSDiscovery", lambda: instance)

    devices = discovery.scan_network()

    assert len(devices) == 1


def test_scan_network_stops_discovery_even_if_search_raises(monkeypatch):
    class _RaisingWSDiscovery(_FakeWSDiscovery):
        def searchServices(self, timeout):
            raise RuntimeError("network error")

    instance = _RaisingWSDiscovery()
    monkeypatch.setattr(discovery, "WSDiscovery", lambda: instance)

    with pytest.raises(RuntimeError):
        discovery.scan_network()

    assert instance.stopped


class _FakeDeviceMgmt:
    async def GetDeviceInformation(self):
        return SimpleNamespace(Manufacturer="Acme", Model="Cam1")


class _FakeMedia:
    def __init__(self, profiles=None, stream_uri="rtsp://192.168.1.50/stream1"):
        self._profiles = profiles if profiles is not None else [SimpleNamespace(token="profile1")]
        self._stream_uri = stream_uri

    async def GetProfiles(self):
        return self._profiles

    async def GetStreamUri(self, params):
        assert params["ProfileToken"] == self._profiles[0].token
        assert params["StreamSetup"] == {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}}
        return SimpleNamespace(Uri=self._stream_uri)


class _FakeONVIFCamera:
    devicemgmt_cls = _FakeDeviceMgmt
    media_cls = _FakeMedia
    update_xaddrs_error: Exception | None = None

    def __init__(self, host, port, user, passwd):
        self.host = host
        self.port = port
        self.user = user
        self.passwd = passwd
        self.closed = False

    async def update_xaddrs(self):
        if self.update_xaddrs_error:
            raise self.update_xaddrs_error

    def create_devicemgmt_service(self):
        return self.devicemgmt_cls()

    def create_media_service(self):
        return self.media_cls()

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_fake_camera():
    _FakeONVIFCamera.devicemgmt_cls = _FakeDeviceMgmt
    _FakeONVIFCamera.media_cls = _FakeMedia
    _FakeONVIFCamera.update_xaddrs_error = None
    yield


DEVICE = DiscoveredDevice(xaddr="http://192.168.1.50:80/onvif/device_service", host="192.168.1.50", port=80)


def test_probe_camera_returns_manufacturer_model_and_rtsp_url(monkeypatch):
    monkeypatch.setattr(discovery, "ONVIFCamera", _FakeONVIFCamera)

    details = discovery.probe_camera(DEVICE, "admin", "swordfish")

    assert details.manufacturer == "Acme"
    assert details.model == "Cam1"
    assert details.rtsp_url == "rtsp://192.168.1.50/stream1"


def test_probe_camera_tolerates_get_device_information_failing(monkeypatch):
    class _FailingDeviceMgmt:
        async def GetDeviceInformation(self):
            raise RuntimeError("not supported")

    _FakeONVIFCamera.devicemgmt_cls = _FailingDeviceMgmt
    monkeypatch.setattr(discovery, "ONVIFCamera", _FakeONVIFCamera)

    details = discovery.probe_camera(DEVICE, "admin", "swordfish")

    assert details.manufacturer is None
    assert details.model is None
    assert details.rtsp_url == "rtsp://192.168.1.50/stream1"


def test_probe_camera_raises_when_there_are_no_media_profiles(monkeypatch):
    # staticmethod, not a bare lambda: a plain function assigned to a class
    # attribute gets bound as a method on access (self passed implicitly),
    # which isn't what create_media_service()'s no-arg call expects here.
    _FakeONVIFCamera.media_cls = staticmethod(lambda: _FakeMedia(profiles=[]))
    monkeypatch.setattr(discovery, "ONVIFCamera", _FakeONVIFCamera)

    with pytest.raises(CameraProbeError, match="no media profiles"):
        discovery.probe_camera(DEVICE, "admin", "swordfish")


def test_probe_camera_wraps_a_failed_handshake_as_camera_probe_error(monkeypatch):
    _FakeONVIFCamera.update_xaddrs_error = RuntimeError("401 Unauthorized")
    monkeypatch.setattr(discovery, "ONVIFCamera", _FakeONVIFCamera)

    with pytest.raises(CameraProbeError, match="did not respond like an ONVIF camera"):
        discovery.probe_camera(DEVICE, "admin", "wrong-password")


def test_probe_camera_always_closes_the_connection(monkeypatch):
    _FakeONVIFCamera.update_xaddrs_error = RuntimeError("boom")
    monkeypatch.setattr(discovery, "ONVIFCamera", _FakeONVIFCamera)

    cameras: list[_FakeONVIFCamera] = []
    original_init = _FakeONVIFCamera.__init__

    def _tracking_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        cameras.append(self)

    monkeypatch.setattr(_FakeONVIFCamera, "__init__", _tracking_init)

    with pytest.raises(CameraProbeError):
        discovery.probe_camera(DEVICE, "admin", "wrong-password")

    assert cameras[0].closed
