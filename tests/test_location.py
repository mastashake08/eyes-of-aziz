import pytest

from eyes_of_aziz.location import NOMINATIM_URL, LocationError, parse_location


def test_blank_means_no_location():
    assert parse_location("   ") is None


@pytest.mark.parametrize("text", ["38.2527, -85.7585", "38.2527 -85.7585", " 38.2527,-85.7585 "])
def test_coordinates_are_read_directly(text):
    assert parse_location(text) == (38.2527, -85.7585)


def test_out_of_range_coordinates_are_refused():
    with pytest.raises(LocationError):
        parse_location("95, 10")


def test_an_address_is_looked_up(requests_mock):
    requests_mock.get(NOMINATIM_URL, json=[{"lat": "38.2527", "lon": "-85.7585"}])

    assert parse_location("Louisville, KY") == (38.2527, -85.7585)
    request = requests_mock.last_request
    assert request.qs["q"] == ["louisville, ky"]
    assert request.headers["User-Agent"].startswith("eyes-of-aziz/")


def test_an_address_that_cannot_be_found(requests_mock):
    requests_mock.get(NOMINATIM_URL, json=[])

    with pytest.raises(LocationError, match="Couldn't find"):
        parse_location("Nowhere Special")


def test_the_lookup_service_failing(requests_mock):
    requests_mock.get(NOMINATIM_URL, status_code=503)

    with pytest.raises(LocationError, match="Couldn't look up"):
        parse_location("Louisville, KY")
