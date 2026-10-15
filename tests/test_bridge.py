from unittest.mock import MagicMock

import numpy as np

from eyes_of_aziz.api_client import BackendError, DeviceRejectedError
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
    # These tests are about the capture loop, so they upload whole frames;
    # faces mode is tested separately below.
    upload_mode="frames",
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

    def isOpened(self):
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


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_heartbeat_on_start_and_then_every_interval(monkeypatch):
    client = MagicMock()
    clock = FakeClock()
    config = BridgeConfig(**{**CONFIG.__dict__, "heartbeat_interval_seconds": 300})
    bridge = CameraBridge(config, client=client, clock=clock)

    reads = [(True, A_FRAME)] * 4
    def read_and_advance():
        clock.now += 120  # two minutes per frame
        if not reads:
            bridge.stop()
            return False, None
        return reads.pop(0)

    capture = MagicMock()
    capture.read.side_effect = read_and_advance
    monkeypatch.setattr("eyes_of_aziz.bridge.cv2.VideoCapture", lambda _url: capture)

    bridge.run()

    # At 0 (start), then once 300 seconds had passed (at 360), and not between.
    assert client.send_status.call_count == 2


def test_a_failed_heartbeat_does_not_stop_frames(monkeypatch):
    client = MagicMock()
    client.send_status.side_effect = BackendError("offline")
    bridge, _ = _bridge_with_captures(monkeypatch, [[(True, A_FRAME), (True, A_FRAME)]], client)

    bridge.run()

    assert client.send_frame.call_count == 2


def test_a_rejected_heartbeat_stops_the_bridge(monkeypatch):
    client = MagicMock()
    client.send_status.side_effect = DeviceRejectedError("bad token")
    bridge, _ = _bridge_with_captures(monkeypatch, [[(True, A_FRAME)]], client)

    bridge.run()

    assert client.send_frame.call_count == 0


from pathlib import Path

import cv2

from eyes_of_aziz.faces import Face, FaceDetector, MotionGate

FACES_CONFIG = BridgeConfig(**{**CONFIG.__dict__, "upload_mode": "faces", "motion_threshold": 0})


class FakeDetector:
    def __init__(self, faces):
        self.faces = faces
        self.calls = 0

    def detect(self, _frame):
        self.calls += 1
        return list(self.faces)


def _faces_bridge(monkeypatch, reads, detector, client, config=FACES_CONFIG, motion_gate=None):
    bridge = CameraBridge(config, client=client, detector=detector, motion_gate=motion_gate)
    capture = FakeCapture(reads, on_stop=bridge.stop)
    monkeypatch.setattr("eyes_of_aziz.bridge.cv2.VideoCapture", lambda _url: capture)
    return bridge


BIG_FRAME = np.full((240, 320, 3), 128, dtype=np.uint8)


def test_frames_without_faces_are_not_uploaded(monkeypatch):
    client = MagicMock()
    bridge = _faces_bridge(monkeypatch, [(True, BIG_FRAME)] * 3, FakeDetector([]), client)

    bridge.run()

    assert client.send_frame.call_count == 0
    assert bridge.stats == {"frames_captured": 3, "frames_with_faces": 0, "uploads": 0}


def test_each_face_is_uploaded_as_its_own_crop(monkeypatch):
    client = MagicMock()
    faces = [Face(10, 10, 60, 60, 0.9), Face(150, 20, 50, 50, 0.9)]
    bridge = _faces_bridge(monkeypatch, [(True, BIG_FRAME)], FakeDetector(faces), client)

    bridge.run()

    assert client.send_frame.call_count == 2
    crop = cv2.imdecode(np.frombuffer(client.send_frame.call_args_list[0].args[0], np.uint8), cv2.IMREAD_COLOR)
    assert min(crop.shape[:2]) >= 160  # small faces are scaled up for the search
    assert bridge.stats["frames_with_faces"] == 1


def test_faces_per_frame_are_capped(monkeypatch):
    client = MagicMock()
    config = BridgeConfig(**{**FACES_CONFIG.__dict__, "max_faces_per_frame": 2})
    faces = [Face(i * 30, 10, 25, 25, 0.9) for i in range(5)]
    bridge = _faces_bridge(monkeypatch, [(True, BIG_FRAME)], FakeDetector(faces), client, config=config)

    bridge.run()

    assert client.send_frame.call_count == 2


def test_an_unchanged_scene_skips_face_detection(monkeypatch):
    client = MagicMock()
    detector = FakeDetector([])
    gate = MotionGate(threshold=0.01, recheck_every=100)
    bridge = _faces_bridge(monkeypatch, [(True, BIG_FRAME)] * 4, detector, client, motion_gate=gate)

    bridge.run()

    # Only the first frame is looked at; the identical ones after it are skipped.
    assert detector.calls == 1


def test_the_real_detector_finds_every_face_in_a_photo():
    photo = cv2.imread(str(Path(__file__).parent / "fixtures" / "three-faces.jpg"))

    faces = FaceDetector().detect(photo)

    assert len(faces) == 3
    assert FaceDetector().detect(np.zeros((240, 320, 3), np.uint8)) == []
