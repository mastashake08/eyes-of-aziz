import pytest

from eyes_of_aziz.config import BridgeConfig, ConfigError

REQUIRED_ENV = {
    "EYES_OF_AZIZ_BACKEND_URL": "https://projectaziz.com",
    "EYES_OF_AZIZ_DEVICE_ID": "front-door-camera-1",
    "EYES_OF_AZIZ_REGISTRATION_TOKEN": "secret-token",
    "EYES_OF_AZIZ_RTSP_URL": "rtsp://192.168.1.50:554/stream1",
}


def test_from_env_with_only_required_vars_uses_defaults():
    config = BridgeConfig.from_env(REQUIRED_ENV)

    assert config.backend_url == "https://projectaziz.com"
    assert config.device_id == "front-door-camera-1"
    assert config.registration_token == "secret-token"
    assert config.rtsp_url == "rtsp://192.168.1.50:554/stream1"
    assert config.capture_interval_seconds == 10.0
    assert config.jpeg_quality == 85


def test_from_env_strips_trailing_slash_from_backend_url():
    env = {**REQUIRED_ENV, "EYES_OF_AZIZ_BACKEND_URL": "https://projectaziz.com/"}

    config = BridgeConfig.from_env(env)

    assert config.backend_url == "https://projectaziz.com"


def test_frames_url_builds_the_correct_endpoint():
    config = BridgeConfig.from_env(REQUIRED_ENV)

    assert config.frames_url == "https://projectaziz.com/api/field-devices/front-door-camera-1/frames"


@pytest.mark.parametrize("missing_var", list(REQUIRED_ENV))
def test_from_env_raises_when_a_required_var_is_missing(missing_var):
    env = {k: v for k, v in REQUIRED_ENV.items() if k != missing_var}

    with pytest.raises(ConfigError, match=missing_var):
        BridgeConfig.from_env(env)


def test_from_env_rejects_a_non_numeric_capture_interval():
    env = {**REQUIRED_ENV, "EYES_OF_AZIZ_CAPTURE_INTERVAL_SECONDS": "not-a-number"}

    with pytest.raises(ConfigError):
        BridgeConfig.from_env(env)


def test_from_env_rejects_a_zero_capture_interval():
    env = {**REQUIRED_ENV, "EYES_OF_AZIZ_CAPTURE_INTERVAL_SECONDS": "0"}

    with pytest.raises(ConfigError):
        BridgeConfig.from_env(env)


def test_from_env_rejects_an_out_of_range_jpeg_quality():
    env = {**REQUIRED_ENV, "EYES_OF_AZIZ_JPEG_QUALITY": "101"}

    with pytest.raises(ConfigError):
        BridgeConfig.from_env(env)


def _base_env(**extra):
    return {
        "EYES_OF_AZIZ_BACKEND_URL": "https://projectaziz.com",
        "EYES_OF_AZIZ_DEVICE_ID": "cam",
        "EYES_OF_AZIZ_REGISTRATION_TOKEN": "token",
        "EYES_OF_AZIZ_RTSP_URL": "rtsp://cam/stream",
        **extra,
    }


def test_location_and_heartbeat_settings():
    config = BridgeConfig.from_env(_base_env(
        EYES_OF_AZIZ_LATITUDE="38.2527",
        EYES_OF_AZIZ_LONGITUDE="-85.7585",
        EYES_OF_AZIZ_HEARTBEAT_INTERVAL_SECONDS="60",
    ))

    assert (config.latitude, config.longitude) == (38.2527, -85.7585)
    assert config.heartbeat_interval_seconds == 60
    assert config.status_url == "https://projectaziz.com/api/field-devices/cam/status"


def test_location_is_optional():
    config = BridgeConfig.from_env(_base_env())

    assert config.latitude is None and config.longitude is None
    assert config.heartbeat_interval_seconds == 300


@pytest.mark.parametrize("extra", [
    {"EYES_OF_AZIZ_LATITUDE": "38.2"},
    {"EYES_OF_AZIZ_LATITUDE": "91", "EYES_OF_AZIZ_LONGITUDE": "0"},
    {"EYES_OF_AZIZ_LATITUDE": "0", "EYES_OF_AZIZ_LONGITUDE": "181"},
    {"EYES_OF_AZIZ_HEARTBEAT_INTERVAL_SECONDS": "0"},
])
def test_bad_location_or_heartbeat_settings_are_refused(extra):
    with pytest.raises(ConfigError):
        BridgeConfig.from_env(_base_env(**extra))


def test_face_settings_default_to_faces_mode():
    config = BridgeConfig.from_env(_base_env())

    assert config.upload_mode == "faces"
    assert config.max_faces_per_frame == 5


@pytest.mark.parametrize("extra", [
    {"EYES_OF_AZIZ_UPLOAD_MODE": "everything"},
    {"EYES_OF_AZIZ_FACE_SCORE_THRESHOLD": "0"},
    {"EYES_OF_AZIZ_MAX_FACES_PER_FRAME": "0"},
    {"EYES_OF_AZIZ_MOTION_THRESHOLD": "2"},
])
def test_bad_face_settings_are_refused(extra):
    with pytest.raises(ConfigError):
        BridgeConfig.from_env(_base_env(**extra))


def test_a_frigate_camera_needs_no_rtsp_url():
    env = _base_env(
        EYES_OF_AZIZ_SOURCE="frigate",
        EYES_OF_AZIZ_FRIGATE_URL="http://frigate:5000",
        EYES_OF_AZIZ_FRIGATE_CAMERA="driveway",
        EYES_OF_AZIZ_MQTT_URL="mqtt://broker:1883",
    )
    del env["EYES_OF_AZIZ_RTSP_URL"]

    config = BridgeConfig.from_env(env)

    assert config.source == "frigate"
    assert config.frigate_camera == "driveway"
    assert config.frigate_topic_prefix == "frigate"


def test_a_frigate_camera_needs_its_frigate_settings():
    with pytest.raises(ConfigError, match="EYES_OF_AZIZ_FRIGATE_URL"):
        BridgeConfig.from_env(_base_env(EYES_OF_AZIZ_SOURCE="frigate"))


def test_an_unknown_source_is_refused():
    with pytest.raises(ConfigError):
        BridgeConfig.from_env(_base_env(EYES_OF_AZIZ_SOURCE="webcam"))
