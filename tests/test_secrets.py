import pytest
from keyring.errors import KeyringError

from eyes_of_aziz import secrets
from eyes_of_aziz.config import BridgeConfig, ConfigError
from eyes_of_aziz.setup import write_camera_env_file


def test_keychain_is_used_on_macos_and_windows_only(monkeypatch):
    monkeypatch.undo()  # the real check, not the test stand-in
    assert secrets.keychain_supported("darwin")
    assert secrets.keychain_supported("win32")
    assert not secrets.keychain_supported("linux")


def test_env_file_keeps_secrets_out_when_the_keychain_is_available(tmp_path, keychain_on):
    path = write_camera_env_file(
        tmp_path, device_id="porch", backend_url="https://projectaziz.com",
        registration_token="reg-token", rtsp_url="rtsp://admin:pw@10.0.0.2/1",
    )

    content = path.read_text()
    assert "reg-token" not in content
    assert "admin:pw" not in content
    assert "EYES_OF_AZIZ_SECRETS=keychain" in content
    assert secrets.load("porch") == {
        "EYES_OF_AZIZ_REGISTRATION_TOKEN": "reg-token",
        "EYES_OF_AZIZ_RTSP_URL": "rtsp://admin:pw@10.0.0.2/1",
    }


def test_env_file_falls_back_to_the_file_when_the_keychain_fails(tmp_path, keychain_on, monkeypatch):
    def broken(*_args):
        raise KeyringError("locked")

    monkeypatch.setattr(keychain_on, "set_password", broken)
    content = write_camera_env_file(
        tmp_path, device_id="porch", backend_url="https://projectaziz.com",
        registration_token="reg-token", rtsp_url="rtsp://10.0.0.2/1",
    ).read_text()

    assert "EYES_OF_AZIZ_REGISTRATION_TOKEN=reg-token" in content
    assert "EYES_OF_AZIZ_SECRETS" not in content


def test_config_reads_secrets_back_from_the_keychain(keychain_on):
    secrets.store("porch", {
        "EYES_OF_AZIZ_REGISTRATION_TOKEN": "reg-token",
        "EYES_OF_AZIZ_RTSP_URL": "rtsp://10.0.0.2/1",
    })

    config = BridgeConfig.from_env({
        "EYES_OF_AZIZ_BACKEND_URL": "https://projectaziz.com",
        "EYES_OF_AZIZ_DEVICE_ID": "porch",
        "EYES_OF_AZIZ_SECRETS": "keychain",
    })

    assert config.registration_token == "reg-token"
    assert config.rtsp_url == "rtsp://10.0.0.2/1"


def test_config_says_what_to_do_when_the_keychain_has_no_token(keychain_on):
    with pytest.raises(ConfigError, match="Register the camera again"):
        BridgeConfig.from_env({
            "EYES_OF_AZIZ_BACKEND_URL": "https://projectaziz.com",
            "EYES_OF_AZIZ_DEVICE_ID": "porch",
            "EYES_OF_AZIZ_SECRETS": "keychain",
        })


def test_forget_removes_a_cameras_secrets(keychain_on):
    secrets.store("porch", {"EYES_OF_AZIZ_REGISTRATION_TOKEN": "reg-token"})
    secrets.forget("porch")
    assert secrets.load("porch") == {}
