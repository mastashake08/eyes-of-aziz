from unittest.mock import MagicMock

import numpy as np
import pytest

from eyes_of_aziz.api_client import DeviceRejectedError
from eyes_of_aziz.bridge import CameraBridge
from eyes_of_aziz.config import BridgeConfig

CONFIG = BridgeConfig(
    backend_url="https://projectaziz.com",
    device_id="front-door-camera-1",
    registration_token="secret-token",
    rtsp_url="rtsp://192.168.1.50:554/stream1",
    capture_interval_seconds=0,
    reconnect_backoff_seconds=0,
    max_reconnect_backoff_seconds=0,
)

A_FRAME = np.zeros((4, 4, 3), dtype=np.uint8)


class FakeCapture:
    """Stands in for cv2.VideoCapture: yields a scripted sequence of reads,
    then stops the bridge once exhausted so the test loop terminates.
    """

    def __init__(self, reads, on_stop):
        self._reads = list(reads)
        self._on_stop = on_stop
        self.released = False

    def read(self):
        if not self._reads:
            self._on_stop()
            return False, None
        return self._reads.pop(0)

    def isOpened(self):  # noqa: N802 - matches cv2's API
        return True

    def release(self):
        self.released = True


def _bridge_with_captures(monkeypatch, capture_sequences, client):
    bridge = CameraBridge(CONFIG, client=client)
    captures = [FakeCapture(reads, on_stop=bridge.stop) for reads in capture_sequences]
    remaining = list(captures)
    monkeypatch.setattr("eyes_of_aziz.bridge.cv2.VideoCapture", lambda _url: remaining.pop(0))
    return bridge, captures


def test_run_sends_each_successfully_read_frame(monkeypatch):
    client = MagicMock()
    bridge, _ = _bridge_with_captures(
        monkeypatch,
        [[(True, A_FRAME), (True, A_FRAME)]],
        client,
    )

    bridge.run()

    assert client.send_frame.call_count == 2
    jpeg_bytes = client.send_frame.call_args.args[0]
    assert isinstance(jpeg_bytes, bytes)
    assert jpeg_bytes[:2] == b"\xff\xd8"  # JPEG magic bytes


def test_run_reconnects_after_a_dropped_stream(monkeypatch):
    client = MagicMock()
    bridge, captures = _bridge_with_captures(
        monkeypatch,
        [
            [(True, A_FRAME), (False, None)],  # drops after one good frame
            [(True, A_FRAME)],  # second connection
        ],
        client,
    )

    bridge.run()

    assert captures[0].released
    assert client.send_frame.call_count == 2


def test_run_stops_when_the_device_is_rejected(monkeypatch):
    client = MagicMock()
    client.send_frame.side_effect = DeviceRejectedError("bad token")
    bridge, _ = _bridge_with_captures(
        monkeypatch,
        [[(True, A_FRAME), (True, A_FRAME)]],
        client,
    )

    bridge.run()

    # Stops after the first rejection instead of burning through every frame.
    assert client.send_frame.call_count == 1


def test_run_keeps_going_after_a_transient_upload_failure(monkeypatch):
    client = MagicMock()
    client.send_frame.side_effect = [RuntimeError("network blip"), None]
    bridge, _ = _bridge_with_captures(
        monkeypatch,
        [[(True, A_FRAME), (True, A_FRAME)]],
        client,
    )

    bridge.run()

    assert client.send_frame.call_count == 2
