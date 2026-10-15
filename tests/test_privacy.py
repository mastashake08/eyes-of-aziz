from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest

from eyes_of_aziz.__main__ import _pause_or_resume
from eyes_of_aziz.bridge import CameraBridge
from eyes_of_aziz.config import BridgeConfig, ConfigError
from eyes_of_aziz.privacy import (
    PrivacyConfigError,
    apply_masks,
    parse_masks,
    parse_schedule,
    pause_file,
    set_paused,
)
from eyes_of_aziz.status_page import create_status_app
from tests.test_bridge import CONFIG, FakeCapture

# 2026-10-12 is a Monday.
MONDAY = datetime.fromisoformat("2026-10-12T00:00")  # schedules are in local time, so no timezone


def at(day_offset, hour, minute=0):
    return MONDAY.replace(day=12 + day_offset, hour=hour, minute=minute)


# Masks

def test_masks_parse_as_fractions():
    assert parse_masks("0.7,0,0.3,0.5; 0,0.5,1,0.5") == ((0.7, 0, 0.3, 0.5), (0, 0.5, 1, 0.5))
    assert parse_masks("") == ()
    assert parse_masks(None) == ()


@pytest.mark.parametrize("text", ["1,2,3", "0.5,0.5,0.6,0.1", "a,b,c,d", "0,0,0,0.5"])
def test_bad_masks_are_rejected(text):
    with pytest.raises(PrivacyConfigError):
        parse_masks(text)


def test_masked_areas_are_blacked_out_and_the_original_is_untouched():
    frame = np.full((100, 200, 3), 255, dtype=np.uint8)
    masked = apply_masks(frame, ((0.5, 0, 0.5, 0.5),))

    assert masked[:50, 100:].max() == 0
    assert masked[50:, :].min() == 255
    assert masked[:, :100].min() == 255
    assert frame.min() == 255


def test_a_face_under_a_mask_is_never_uploaded():
    photo = cv2.imread("tests/fixtures/three-faces.jpg")
    client = MagicMock()
    config = replace(CONFIG, upload_mode="faces", privacy_masks=((0, 0, 1, 1),))
    bridge = CameraBridge(config, client=client)

    bridge._handle_frame(photo, datetime.now(timezone.utc), check_motion=False)

    client.send_frame.assert_not_called()


def test_masks_and_schedule_come_from_the_environment():
    config = BridgeConfig.from_env({
        "EYES_OF_AZIZ_BACKEND_URL": "https://projectaziz.com",
        "EYES_OF_AZIZ_DEVICE_ID": "porch",
        "EYES_OF_AZIZ_REGISTRATION_TOKEN": "t",
        "EYES_OF_AZIZ_RTSP_URL": "rtsp://x",
        "EYES_OF_AZIZ_PRIVACY_MASKS": "0,0,0.5,0.5",
        "EYES_OF_AZIZ_SCHEDULE": "mon-fri 18:00-07:00",
    })
    assert config.privacy_masks == ((0, 0, 0.5, 0.5),)
    assert not config.schedule.always


def test_a_bad_schedule_is_a_config_error():
    with pytest.raises(ConfigError, match="weekends please"):
        BridgeConfig.from_env({
            "EYES_OF_AZIZ_BACKEND_URL": "https://projectaziz.com",
            "EYES_OF_AZIZ_DEVICE_ID": "porch",
            "EYES_OF_AZIZ_REGISTRATION_TOKEN": "t",
            "EYES_OF_AZIZ_RTSP_URL": "rtsp://x",
            "EYES_OF_AZIZ_SCHEDULE": "weekends please",
        })


# Schedules

def test_no_schedule_means_always():
    assert parse_schedule("").is_active(at(0, 3))


def test_a_daytime_window():
    schedule = parse_schedule("mon-fri 09:00-17:00")
    assert schedule.is_active(at(0, 9))
    assert schedule.is_active(at(4, 16, 59))
    assert not schedule.is_active(at(0, 17))
    assert not schedule.is_active(at(5, 12))  # Saturday


def test_an_overnight_window_runs_into_the_next_morning():
    schedule = parse_schedule("fri 22:00-06:00")
    assert schedule.is_active(at(4, 23))  # Friday night
    assert schedule.is_active(at(5, 5, 59))  # Saturday morning
    assert not schedule.is_active(at(5, 6))
    assert not schedule.is_active(at(4, 5))  # Friday morning belongs to Thursday


def test_days_without_times_are_all_day_and_ranges_wrap():
    schedule = parse_schedule("sat,sun; fri-mon 12:00-13:00")
    assert schedule.is_active(at(5, 0))
    assert schedule.is_active(at(6, 23, 59))
    assert schedule.is_active(at(0, 12, 30))  # Monday, from the wrapped range
    assert not schedule.is_active(at(1, 12, 30))  # Tuesday


@pytest.mark.parametrize("text", ["someday", "mon 25:00-26:00", "mon 09:00-09:00", "mon 9-5"])
def test_bad_schedules_are_rejected(text):
    with pytest.raises(PrivacyConfigError):
        parse_schedule(text)


# Pausing

def _run_bridge(monkeypatch, config, local_now=datetime.now, reads=3):
    client = MagicMock()
    bridge = CameraBridge(config, client=client, local_now=local_now)
    opened = []

    def capture(_url):
        opened.append(FakeCapture([(True, np.zeros((4, 4, 3), dtype=np.uint8))] * reads, on_stop=bridge.stop))
        return opened[-1]

    monkeypatch.setattr("eyes_of_aziz.bridge.cv2.VideoCapture", capture)
    return bridge, client, opened


def test_a_paused_camera_sends_nothing_and_lets_go_of_the_stream(monkeypatch, tmp_path):
    set_paused(tmp_path, CONFIG.device_id, True)
    config = replace(CONFIG, pause_file=str(pause_file(tmp_path, CONFIG.device_id)))
    bridge, client, opened = _run_bridge(monkeypatch, config)
    monkeypatch.setattr(bridge._stop_event, "wait", lambda _t: (bridge.stop(), True)[1])

    bridge.run()

    assert bridge.pause_reason == "Paused"
    assert opened == []
    client.send_frame.assert_not_called()
    client.send_status.assert_called_once()  # still reports in


def test_outside_its_schedule_the_camera_waits(monkeypatch):
    config = replace(CONFIG, schedule=parse_schedule("mon 09:00-17:00"))
    bridge, client, _opened = _run_bridge(monkeypatch, config, local_now=lambda: at(0, 20))
    monkeypatch.setattr(bridge._stop_event, "wait", lambda _t: (bridge.stop(), True)[1])

    bridge.run()

    assert bridge.pause_reason == "Outside its schedule"
    assert bridge.status()["pause_reason"] is None  # stopped now
    client.send_frame.assert_not_called()


def test_resuming_reopens_the_stream(monkeypatch, tmp_path):
    set_paused(tmp_path, CONFIG.device_id, True)
    config = replace(CONFIG, pause_file=str(pause_file(tmp_path, CONFIG.device_id)))
    bridge, client, opened = _run_bridge(monkeypatch, config, reads=2)
    real_wait = bridge._stop_event.wait

    def wait(timeout):
        set_paused(tmp_path, CONFIG.device_id, False)  # someone presses Resume
        return real_wait(0)

    monkeypatch.setattr(bridge._stop_event, "wait", wait)
    bridge.run()

    assert len(opened) == 1
    assert client.send_frame.call_count == 2


def test_pause_and_resume_from_the_command_line(tmp_path, capsys):
    (tmp_path / "porch.env").write_text("")
    (tmp_path / "yard.env").write_text("")

    assert _pause_or_resume(tmp_path, "all", paused=True) == 0
    assert pause_file(tmp_path, "porch").exists() and pause_file(tmp_path, "yard").exists()
    assert _pause_or_resume(tmp_path, "yard", paused=False) == 0
    assert not pause_file(tmp_path, "yard").exists()
    assert _pause_or_resume(tmp_path, "garage", paused=True) == 1
    assert "Cameras: porch, yard" in capsys.readouterr().out


def test_status_page_pause_button():
    calls = []
    app = create_status_app(
        lambda: [{"device_id": "porch", "state": "running"}], set_paused=lambda d, p: calls.append((d, p))
    )
    client = app.test_client()

    assert "/cameras/porch/pause" in client.get("/").get_data(as_text=True)
    assert client.post("/cameras/porch/pause").status_code == 303
    assert client.post("/cameras/porch/resume", headers={"Origin": "http://localhost"}).status_code == 303
    assert calls == [("porch", True), ("porch", False)]


def test_status_page_refuses_other_sites_and_unknown_cameras():
    calls = []
    app = create_status_app(
        lambda: [{"device_id": "porch", "state": "running"}], set_paused=lambda d, p: calls.append((d, p))
    )
    client = app.test_client()

    assert client.post("/cameras/porch/pause", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/cameras/garage/pause").status_code == 404
    assert client.post("/cameras/porch/explode").status_code == 404
    assert calls == []
