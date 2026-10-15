import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest

from eyes_of_aziz.bridge import CameraBridge
from eyes_of_aziz.config import BridgeConfig
from eyes_of_aziz.faces import Face
from eyes_of_aziz.frigate import FrigateError, FrigateEvents, fetch_snapshot

CONFIG = BridgeConfig(
    backend_url="https://projectaziz.com",
    device_id="driveway",
    registration_token="token",
    rtsp_url="",
    source="frigate",
    frigate_url="http://frigate.local:5000",
    frigate_camera="driveway",
    mqtt_url="mqtt://user:secret@broker.local:1883",
    capture_interval_seconds=0,
)


def _message(event_type, camera="driveway", label="person", event_id="1700000000.1-abc", has_snapshot=True):
    payload = {"type": event_type, "after": {"id": event_id, "camera": camera, "label": label, "has_snapshot": has_snapshot}}
    return SimpleNamespace(payload=json.dumps(payload).encode())


def _events():
    return FrigateEvents(CONFIG, client=MagicMock())


def test_person_events_for_this_camera_are_queued():
    events = _events()

    events._on_message(None, None, _message("new"))
    events._on_message(None, None, _message("update"))  # in between: skipped
    events._on_message(None, None, _message("end"))

    assert events.next_event(0) == "1700000000.1-abc"
    assert events.next_event(0) == "1700000000.1-abc"
    assert events.next_event(0) is None


@pytest.mark.parametrize("message", [
    _message("new", camera="backyard"),
    _message("new", label="car"),
    _message("end", has_snapshot=False),
    SimpleNamespace(payload=b"not json"),
])
def test_other_events_are_ignored(message):
    events = _events()

    events._on_message(None, None, message)

    assert events.next_event(0) is None


def test_start_connects_with_the_credentials_in_the_url():
    client = MagicMock()

    FrigateEvents(CONFIG, client=client).start()

    client.username_pw_set.assert_called_once_with("user", "secret")
    client.connect_async.assert_called_once_with("broker.local", 1883)
    client.loop_start.assert_called_once()


def test_fetch_snapshot_decodes_the_event_image(requests_mock):
    _, jpeg = cv2.imencode(".jpg", np.full((60, 80, 3), 200, np.uint8))
    requests_mock.get("http://frigate.local:5000/api/events/abc/snapshot.jpg", content=jpeg.tobytes())

    image = fetch_snapshot(CONFIG, "abc")

    assert image.shape == (60, 80, 3)
    assert requests_mock.last_request.qs == {"bbox": ["0"], "timestamp": ["0"]}


def test_fetch_snapshot_failure(requests_mock):
    requests_mock.get("http://frigate.local:5000/api/events/abc/snapshot.jpg", status_code=404)

    with pytest.raises(FrigateError):
        fetch_snapshot(CONFIG, "abc")


class FakeEvents:
    def __init__(self, ids, bridge_holder):
        self._ids = list(ids)
        self._holder = bridge_holder
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def next_event(self, timeout):
        if not self._ids:
            self._holder[0].stop()
            return None
        return self._ids.pop(0)


def test_the_bridge_uploads_the_faces_in_each_frigate_snapshot():
    client = MagicMock()
    detector = MagicMock()
    detector.detect.return_value = [Face(10, 10, 60, 60, 0.9)]
    holder = []
    events = FakeEvents(["event-1", "broken", "event-2"], holder)

    def snapshots(_config, event_id):
        if event_id == "broken":
            raise FrigateError("gone")
        return np.full((240, 320, 3), 128, np.uint8)

    bridge = CameraBridge(CONFIG, client=client, detector=detector, frigate_events=events, snapshot_fetcher=snapshots)
    holder.append(bridge)

    bridge.run()

    assert events.started and events.stopped
    assert client.send_frame.call_count == 2  # one face in each good snapshot
    assert bridge.stats["frames_captured"] == 2
    assert bridge.last_error == "gone"
