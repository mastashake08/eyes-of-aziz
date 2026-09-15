
from eyes_of_aziz.__main__ import resolve_env_file


def test_explicit_path_always_wins(tmp_path):
    (tmp_path / ".env").write_text("x")
    (tmp_path / "cameras").mkdir()
    (tmp_path / "cameras" / "a.env").write_text("x")

    path, error = resolve_env_file("/explicit/path.env", tmp_path)

    assert path == "/explicit/path.env"
    assert error is None


def test_falls_back_to_default_dotenv_when_one_exists(tmp_path):
    (tmp_path / ".env").write_text("x")

    path, error = resolve_env_file(None, tmp_path)

    assert path is None
    assert error is None


def test_auto_selects_the_only_camera_config(tmp_path):
    cameras = tmp_path / "cameras"
    cameras.mkdir()
    (cameras / "front-door.env").write_text("x")

    path, error = resolve_env_file(None, tmp_path)

    assert path == str(cameras / "front-door.env")
    assert error is None


def test_errors_with_all_options_when_multiple_camera_configs_exist(tmp_path):
    cameras = tmp_path / "cameras"
    cameras.mkdir()
    (cameras / "front-door.env").write_text("x")
    (cameras / "back-door.env").write_text("x")

    path, error = resolve_env_file(None, tmp_path)

    assert path is None
    assert "front-door.env" in error
    assert "back-door.env" in error


def test_no_config_anywhere_falls_through_without_error(tmp_path):
    path, error = resolve_env_file(None, tmp_path)

    assert path is None
    assert error is None
