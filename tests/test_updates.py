import requests

from eyes_of_aziz.status_page import create_status_app
from eyes_of_aziz.updates import (
    Release,
    UpdateChecker,
    check_for_update,
    parse_version,
    updates_enabled,
)
from eyes_of_aziz.webapp import create_app


class _Response:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._data


class _Session:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


def _latest(tag):
    return _Session(_Response({"tag_name": tag, "html_url": f"https://github.com/mastashake08/eyes-of-aziz/releases/tag/{tag}"}))


def test_parse_version():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("0.10.0") == (0, 10, 0)
    assert parse_version("nightly") is None


def test_a_newer_release_is_reported():
    release = check_for_update("0.2.0", session=_latest("v0.10.0"))
    assert release == Release("0.10.0", "https://github.com/mastashake08/eyes-of-aziz/releases/tag/v0.10.0")


def test_the_same_or_an_older_release_is_not():
    assert check_for_update("0.2.0", session=_latest("v0.2.0")) is None
    assert check_for_update("0.2.0", session=_latest("v0.1.9")) is None


def test_github_being_unreachable_is_not_an_error():
    assert check_for_update("0.2.0", session=_Session(error=requests.ConnectionError("offline"))) is None
    assert check_for_update("0.2.0", session=_Session(_Response({}, status=404))) is None


def test_it_can_be_turned_off():
    assert updates_enabled({})
    assert not updates_enabled({"EYES_OF_AZIZ_UPDATE_CHECK": "0"})
    assert not updates_enabled({"EYES_OF_AZIZ_UPDATE_CHECK": "off"})


def test_checker_logs_a_new_release_once(caplog):
    release = Release("9.0.0", "https://example.com/r")
    checker = UpdateChecker(check=lambda: release)

    checker.check_now()
    checker.check_now()

    assert checker.available == release
    assert sum("9.0.0 is available" in r.message for r in caplog.records) == 1


def test_status_page_shows_the_banner():
    app = create_status_app(list, get_update=lambda: Release("9.0.0", "https://example.com/r"))
    page = app.test_client().get("/").get_data(as_text=True)

    assert "Eyes Of Aziz 9.0.0 is available" in page
    assert app.test_client().get("/status.json").get_json()["update_available"] == "9.0.0"


def test_status_page_has_no_banner_when_up_to_date():
    page = create_status_app(list).test_client().get("/").get_data(as_text=True)
    assert "is available" not in page


def test_setup_dashboard_shows_the_banner():
    app = create_app(get_update=lambda: Release("9.0.0", "https://example.com/r"))
    page = app.test_client().get("/login").get_data(as_text=True)
    assert "Eyes Of Aziz 9.0.0 is available" in page
