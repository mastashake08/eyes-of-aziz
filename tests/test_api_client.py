from datetime import datetime, timezone

import pytest

from eyes_of_aziz.api_client import BackendClient, BackendError, DeviceRejectedError
from eyes_of_aziz.config import BridgeConfig


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    monkeypatch.setattr("eyes_of_aziz.api_client.time.sleep", lambda _seconds: None)

CONFIG = BridgeConfig(
    backend_url="https://projectaziz.com",
    device_id="front-door-camera-1",
    registration_token="secret-token",
    rtsp_url="rtsp://192.168.1.50:554/stream1",
    max_send_retries=3,
)


def test_send_frame_posts_the_expected_multipart_request(requests_mock):
    requests_mock.post(CONFIG.frames_url, json={"status": "queued"})

    client = BackendClient(CONFIG)
    client.send_frame(b"jpeg-bytes", datetime(2026, 1, 1, tzinfo=timezone.utc))

    request = requests_mock.last_request
    assert request.timeout == CONFIG.request_timeout_seconds

    body = request.text
    assert "secret-token" in body
    assert "2026-01-01T00:00:00+00:00" in body
    assert "jpeg-bytes" in body
    assert 'name="frame"; filename="frame.jpg"' in body


def test_send_frame_defaults_captured_at_to_now(requests_mock):
    requests_mock.post(CONFIG.frames_url, json={"status": "queued"})

    client = BackendClient(CONFIG)
    client.send_frame(b"jpeg-bytes")

    assert requests_mock.called


def test_send_frame_raises_device_rejected_on_401_without_retrying(requests_mock):
    requests_mock.post(CONFIG.frames_url, status_code=401, json={"message": "Invalid device or registration token."})

    client = BackendClient(CONFIG)

    with pytest.raises(DeviceRejectedError, match="Invalid device or registration token"):
        client.send_frame(b"jpeg-bytes")

    assert requests_mock.call_count == 1


def test_send_frame_raises_device_rejected_on_403_without_retrying(requests_mock):
    requests_mock.post(CONFIG.frames_url, status_code=403, json={"message": "This camera is not active in the network."})

    client = BackendClient(CONFIG)

    with pytest.raises(DeviceRejectedError, match="not active in the network"):
        client.send_frame(b"jpeg-bytes")

    assert requests_mock.call_count == 1


def test_send_frame_retries_on_server_error_then_succeeds(requests_mock):
    requests_mock.post(
        CONFIG.frames_url,
        [
            {"status_code": 500, "json": {"message": "boom"}},
            {"status_code": 200, "json": {"status": "queued"}},
        ],
    )

    client = BackendClient(CONFIG)
    client.send_frame(b"jpeg-bytes")

    assert requests_mock.call_count == 2


def test_send_frame_raises_backend_error_after_exhausting_retries(requests_mock):
    requests_mock.post(CONFIG.frames_url, status_code=500, json={"message": "boom"})

    client = BackendClient(CONFIG)

    with pytest.raises(BackendError):
        client.send_frame(b"jpeg-bytes")

    assert requests_mock.call_count == CONFIG.max_send_retries
