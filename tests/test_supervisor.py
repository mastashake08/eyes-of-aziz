import threading

from eyes_of_aziz.config import BridgeConfig
from eyes_of_aziz.status_page import create_status_app
from eyes_of_aziz.supervisor import Supervisor, load_camera_configs


def _config(device_id):
    return BridgeConfig(
        backend_url="https://projectaziz.com",
        device_id=device_id,
        registration_token="token",
        rtsp_url="rtsp://cam/stream",
    )


class FakeBridge:
    def __init__(self, config, behaviour):
        self.device_id = config.device_id
        self.rejected = behaviour == "reject"
        self._behaviour = behaviour
        self._stop = threading.Event()

    def run(self):
        if self._behaviour == "crash":
            raise RuntimeError("camera exploded")
        if self._behaviour == "reject":
            return
        self._stop.wait(5)

    def stop(self):
        self._stop.set()

    def status(self):
        return {"device_id": self.device_id, "state": "running" if self._behaviour == "ok" else "stopped"}


def test_load_camera_configs_reads_each_file_and_reports_bad_ones(tmp_path):
    cameras = tmp_path / "cameras"
    cameras.mkdir()
    base = "EYES_OF_AZIZ_BACKEND_URL=https://projectaziz.com\nEYES_OF_AZIZ_REGISTRATION_TOKEN=t\nEYES_OF_AZIZ_RTSP_URL=rtsp://x\n"
    (cameras / "front.env").write_text(base + "EYES_OF_AZIZ_DEVICE_ID=front\n")
    (cameras / "back.env").write_text(base + "EYES_OF_AZIZ_DEVICE_ID=back\n")
    (cameras / "broken.env").write_text("EYES_OF_AZIZ_DEVICE_ID=broken\n")

    configs, problems = load_camera_configs(cameras, defaults={"EYES_OF_AZIZ_CAPTURE_INTERVAL_SECONDS": "30"})

    assert [config.device_id for config in configs] == ["back", "front"]
    assert all(config.capture_interval_seconds == 30 for config in configs)
    assert len(problems) == 1 and problems[0].startswith("broken.env")


def test_a_crashing_camera_is_restarted_and_the_others_keep_running():
    starts = {"crashy": 0}

    def factory(config):
        if config.device_id == "crashy":
            starts["crashy"] += 1
            return FakeBridge(config, "crash")
        return FakeBridge(config, "ok")

    supervisor = Supervisor([_config("crashy"), _config("steady")], bridge_factory=factory, restart_delay_seconds=0.01)
    thread = threading.Thread(target=supervisor.run)
    thread.start()
    threading.Event().wait(0.2)
    status = {row["device_id"]: row for row in supervisor.status()}
    supervisor.stop()
    thread.join(2)

    assert not thread.is_alive()
    assert starts["crashy"] > 1
    assert status["steady"]["state"] == "running"
    assert "camera exploded" in status["crashy"]["last_error"]


def test_a_rejected_camera_is_not_restarted():
    starts = []

    def factory(config):
        starts.append(config.device_id)
        return FakeBridge(config, "reject")

    supervisor = Supervisor([_config("refused")], bridge_factory=factory, restart_delay_seconds=0.01)
    supervisor.run()  # returns by itself: nothing left to run

    assert starts == ["refused"]


def test_status_page_lists_each_camera():
    app = create_status_app(lambda: [
        {"device_id": "front-door", "state": "running", "last_upload_at": None, "last_heartbeat_at": None,
         "frames_captured": 5, "frames_with_faces": 1, "uploads": 1, "has_location": True},
        {"device_id": "garage", "state": "no_stream", "last_error": "Can't read the camera's stream; retrying.",
         "has_location": False},
    ])
    client = app.test_client()

    page = client.get("/").data
    assert b"front-door" in page and b"Online" in page
    assert b"Can&#39;t reach camera" in page and b"no location set" in page
    assert client.get("/status.json").get_json()["cameras"][0]["device_id"] == "front-door"
