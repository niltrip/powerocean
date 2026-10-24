"""test_api: error classification of the EcoFlow API client."""

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.powerocean.api import (
    AuthenticationError,
    EcoflowApi,
    EcoflowApiError,
    EcoflowConnectionError,
    RegionDetectionError,
    validate_response,
)

SN = "SN_INVERTERBOX01"
LOGIN_URL = "https://api.ecoflow.com/auth/login"
EU_URL = f"https://api-e.ecoflow.com/provider-service/user/device/detail?sn={SN}"
US_URL = f"https://api-a.ecoflow.com/provider-service/user/device/detail?sn={SN}"

LOGIN_OK = {"code": "0", "message": "Success", "data": {"token": "token123"}}
DATA_OK = {"code": "0", "message": "Success", "data": {"quota": {}}}


@pytest.fixture
async def api(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,  # must be active before session creation
) -> EcoflowApi:
    return EcoflowApi(
        serialnumber=SN,
        username="test@example.com",
        password="secret",
        variant="83",
        session=async_get_clientsession(hass),
    )


async def test_authorize_success_detects_eu_region(
    api: EcoflowApi, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(LOGIN_URL, json=LOGIN_OK)
    aioclient_mock.get(EU_URL, json=DATA_OK)

    await api.async_authorize()

    assert api.token == "token123"
    assert api.api_host == "api-e.ecoflow.com"


async def test_authorize_falls_back_to_us_region(
    api: EcoflowApi, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(LOGIN_URL, json=LOGIN_OK)
    aioclient_mock.get(EU_URL, status=404)
    aioclient_mock.get(US_URL, json=DATA_OK)

    await api.async_authorize()

    assert api.api_host == "api-a.ecoflow.com"


async def test_authorize_wrong_credentials(
    api: EcoflowApi, aioclient_mock: AiohttpClientMocker
) -> None:
    """EcoFlow answers wrong credentials with HTTP 200 and no token."""
    aioclient_mock.post(
        LOGIN_URL, json={"code": "2026", "message": "Wrong password", "data": None}
    )

    with pytest.raises(AuthenticationError):
        await api.async_authorize()


@pytest.mark.parametrize("exc", [TimeoutError(), aiohttp.ClientConnectionError()])
async def test_authorize_network_error_is_not_auth_error(
    api: EcoflowApi, aioclient_mock: AiohttpClientMocker, exc: Exception
) -> None:
    """A network outage must not be reported as invalid credentials."""
    aioclient_mock.post(LOGIN_URL, exc=exc)

    with pytest.raises(EcoflowConnectionError):
        await api.async_authorize()


async def test_authorize_server_error_is_connection_error(
    api: EcoflowApi, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(LOGIN_URL, status=503)

    with pytest.raises(EcoflowConnectionError):
        await api.async_authorize()


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, AuthenticationError), (403, AuthenticationError), (400, EcoflowApiError)],
)
async def test_authorize_http_error(
    api: EcoflowApi,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    error: type[Exception],
) -> None:
    aioclient_mock.post(LOGIN_URL, status=status)

    with pytest.raises(EcoflowApiError) as err:
        await api.async_authorize()
    assert type(err.value) is error


async def test_region_detection_unreachable_is_connection_error(
    api: EcoflowApi, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(LOGIN_URL, json=LOGIN_OK)
    aioclient_mock.get(EU_URL, exc=TimeoutError())
    aioclient_mock.get(US_URL, exc=TimeoutError())

    with pytest.raises(EcoflowConnectionError):
        await api.async_authorize()


async def test_region_detection_device_unknown(
    api: EcoflowApi, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(LOGIN_URL, json=LOGIN_OK)
    aioclient_mock.get(EU_URL, status=404)
    aioclient_mock.get(US_URL, status=404)

    with pytest.raises(RegionDetectionError):
        await api.async_authorize()


async def test_fetch_raw_success(
    api: EcoflowApi, aioclient_mock: AiohttpClientMocker
) -> None:
    api.token = "token123"
    api.api_host = "api-e.ecoflow.com"
    aioclient_mock.get(EU_URL, json=DATA_OK)

    assert await api.fetch_raw() == DATA_OK


@pytest.mark.parametrize("status", [401, 403])
async def test_fetch_raw_rejected_token(
    api: EcoflowApi, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    api.token = "expired"
    api.api_host = "api-e.ecoflow.com"
    aioclient_mock.get(EU_URL, status=status)

    with pytest.raises(AuthenticationError):
        await api.fetch_raw()


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (500, EcoflowConnectionError),
        (503, EcoflowConnectionError),
        (404, EcoflowApiError),
    ],
)
async def test_fetch_raw_http_error(
    api: EcoflowApi,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    error: type[Exception],
) -> None:
    api.token = "token123"
    api.api_host = "api-e.ecoflow.com"
    aioclient_mock.get(EU_URL, status=status)

    with pytest.raises(EcoflowApiError) as err:
        await api.fetch_raw()
    assert type(err.value) is error


async def test_fetch_raw_error_payload(
    api: EcoflowApi, aioclient_mock: AiohttpClientMocker
) -> None:
    """Errors reported with HTTP 200 must not be treated as data."""
    api.token = "expired"
    api.api_host = "api-e.ecoflow.com"
    aioclient_mock.get(EU_URL, json={"code": "8521", "message": "token expired"})

    with pytest.raises(EcoflowApiError) as err:
        await api.fetch_raw()
    assert not isinstance(err.value, EcoflowConnectionError)


async def test_fetch_raw_timeout(
    api: EcoflowApi, aioclient_mock: AiohttpClientMocker
) -> None:
    api.token = "token123"
    api.api_host = "api-e.ecoflow.com"
    aioclient_mock.get(EU_URL, exc=TimeoutError())

    with pytest.raises(EcoflowConnectionError):
        await api.fetch_raw()


async def test_fetch_raw_without_region(api: EcoflowApi) -> None:
    with pytest.raises(EcoflowApiError):
        await api.fetch_raw()


@pytest.mark.parametrize(
    "response",
    [
        [],
        {"code": "0"},
        {"code": "0", "data": []},
        {"code": "1001", "data": {}},
    ],
)
def test_validate_response_rejects_invalid(response) -> None:
    with pytest.raises(EcoflowApiError):
        validate_response(response)


def test_validate_response_without_code_field() -> None:
    """Older responses without 'code' are still accepted."""
    response = {"data": {"quota": {}}}
    assert validate_response(response) == response
