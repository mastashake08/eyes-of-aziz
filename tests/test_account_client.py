import pytest

from eyes_of_aziz.account_client import AccountClient, AccountError

BACKEND_URL = "https://projectaziz.com"


def test_login_returns_the_bearer_token(requests_mock):
    requests_mock.post(f"{BACKEND_URL}/api/login", json={"token": "the-token", "user": {}})

    client = AccountClient(BACKEND_URL)
    token = client.login("person@example.com", "correct-password")

    assert token == "the-token"
    assert requests_mock.last_request.json() == {
        "email": "person@example.com",
        "password": "correct-password",
    }


def test_login_raises_with_the_specific_validation_message(requests_mock):
    requests_mock.post(
        f"{BACKEND_URL}/api/login",
        status_code=422,
        json={
            "message": "The given data was invalid.",
            "errors": {"email": ["The provided credentials are incorrect."]},
        },
    )

    client = AccountClient(BACKEND_URL)

    with pytest.raises(AccountError, match="provided credentials are incorrect"):
        client.login("person@example.com", "wrong-password")


def test_login_raises_if_the_response_has_no_token(requests_mock):
    requests_mock.post(f"{BACKEND_URL}/api/login", json={"user": {}})

    client = AccountClient(BACKEND_URL)

    with pytest.raises(AccountError, match="no token"):
        client.login("person@example.com", "correct-password")


def test_register_camera_sends_device_type_camera_bridge(requests_mock):
    requests_mock.post(
        f"{BACKEND_URL}/api/field-devices/register",
        json={"status": "registered", "device": {"device_id": "front-door", "registration_token": "abc"}},
    )

    client = AccountClient(BACKEND_URL)
    device = client.register_camera("bearer-token", "front-door")

    assert device == {"device_id": "front-door", "registration_token": "abc"}
    request = requests_mock.last_request
    assert request.json() == {"device_id": "front-door", "device_type": "camera_bridge"}
    assert request.headers["Authorization"] == "Bearer bearer-token"


def test_register_camera_raises_on_a_duplicate_device_id(requests_mock):
    requests_mock.post(
        f"{BACKEND_URL}/api/field-devices/register",
        status_code=422,
        json={
            "message": "The given data was invalid.",
            "errors": {"device_id": ["The device id has already been taken."]},
        },
    )

    client = AccountClient(BACKEND_URL)

    with pytest.raises(AccountError, match="already been taken"):
        client.register_camera("bearer-token", "front-door")
