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
