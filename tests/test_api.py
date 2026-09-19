"""Tests for the two-leg Evri API client."""

import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.evri.api import EvriApiClient, EvriApiError, EvriCredentialError
from custom_components.evri.const import DETAIL_URL, REFERENCE_URL

CODE = "H00AAA0000000001"
POSTCODE = "SW1A1AA"
URN = "urn:parcel:synthetic"


def _response(status, body, headers=None):
    response = AsyncMock()
    response.status = status
    response.headers = headers or {}
    if isinstance(body, str):
        response.json = AsyncMock(side_effect=json.JSONDecodeError("bad", body, 0))
    else:
        response.json = AsyncMock(return_value=body)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


def _session(*responses):
    session = MagicMock()
    session.get = MagicMock(side_effect=list(responses))
    return session


def _reference(urn=URN):
    return {"parcelIdentifiers": [{"urn": urn}]}


def _detail(match=True):
    identifier = {"value": CODE if match else "OTHER"}
    return {"results": [{"parcelIdentifiers": [identifier], "trackingEvents": []}]}


async def test_two_leg_lookup_headers_params_and_cache():
    session = _session(
        _response(200, _reference()),
        _response(200, _detail()),
        _response(200, _detail()),
    )
    client = EvriApiClient(session)
    first = await client.async_get_parcel(CODE, POSTCODE)
    second = await client.async_get_parcel(CODE, POSTCODE)
    assert first["_configuredTrackingCode"] == CODE
    assert second is not None
    assert session.get.call_count == 3
    assert session.get.call_args_list[0].args[0] == REFERENCE_URL.format(
        tracking_code=CODE
    )
    assert session.get.call_args_list[1].args[0] == DETAIL_URL
    assert session.get.call_args_list[1].kwargs["params"] == {
        "uniqueIds": URN,
        "postcode": POSTCODE,
    }
    assert "Apikey" in session.get.call_args_list[0].kwargs["headers"]


async def test_confirmed_invalid_reference_returns_none():
    client = EvriApiClient(
        _session(
            _response(400, {"errors": [{"message": f"Invalid reference: {CODE}"}]})
        )
    )
    assert await client.async_get_parcel(CODE, POSTCODE) is None


async def test_empty_reference_and_results_warn_once(caplog):
    client = EvriApiClient(_session(_response(200, {"parcelIdentifiers": []})))
    assert await client.async_get_parcel(CODE, POSTCODE) is None
    assert "no URN" in caplog.text
    client = EvriApiClient(
        _session(_response(200, _reference()), _response(200, {"results": []}))
    )
    assert await client.async_get_parcel(CODE, POSTCODE) is None
    assert "no parcel" in caplog.text


async def test_cached_urn_is_evicted_and_resolved_once():
    session = _session(
        _response(200, _reference("old")),
        _response(200, _detail()),
        _response(200, {"results": []}),
        _response(200, _reference("new")),
        _response(200, _detail()),
    )
    client = EvriApiClient(session)
    await client.async_get_parcel(CODE, POSTCODE)
    parcel = await client.async_get_parcel(CODE, POSTCODE)
    assert parcel is not None
    assert client._urn_cache[CODE] == "new"


async def test_sole_result_fallback_warns_without_identifier(caplog):
    client = EvriApiClient(
        _session(_response(200, _reference()), _response(200, _detail(match=False)))
    )
    assert await client.async_get_parcel(CODE, POSTCODE) is not None
    assert "sole result" in caplog.text
    assert CODE not in caplog.text


@pytest.mark.parametrize("status", [401, 403])
async def test_credential_rejection(status):
    client = EvriApiClient(_session(_response(status, {})))
    with pytest.raises(EvriCredentialError):
        await client.async_get_parcel(CODE, POSTCODE)


@pytest.mark.parametrize(
    "headers",
    [
        {"Server": "CloudFront"},
        {"Content-Type": "text/html; charset=iso-8859-1"},
    ],
)
async def test_cloudfront_forbidden_is_not_reported_as_bad_credential(headers):
    client = EvriApiClient(_session(_response(403, "blocked", headers)))
    with pytest.raises(EvriApiError, match="CloudFront/WAF") as exc:
        await client.async_get_parcel(CODE, POSTCODE)
    assert not isinstance(exc.value, EvriCredentialError)
    assert exc.value.status_code == 403


async def test_rate_limit_and_retry_after():
    client = EvriApiClient(_session(_response(429, {}, {"Retry-After": "12"})))
    with pytest.raises(EvriApiError) as exc:
        await client.async_get_parcel(CODE, POSTCODE)
    assert exc.value.status_code == 429
    assert exc.value.retry_after == 12


@pytest.mark.parametrize("header", [None, "tomorrow"])
async def test_rate_limit_without_numeric_retry_after(header):
    headers = {} if header is None else {"Retry-After": header}
    client = EvriApiClient(_session(_response(429, {}, headers)))
    with pytest.raises(EvriApiError) as exc:
        await client.async_get_parcel(CODE, POSTCODE)
    assert exc.value.retry_after is None


@pytest.mark.parametrize(
    ("status", "body"), [(500, {}), (200, "html"), (200, []), (200, {})]
)
async def test_unexpected_responses(status, body):
    client = EvriApiClient(_session(_response(status, body)))
    with pytest.raises(EvriApiError):
        await client.async_get_parcel(CODE, POSTCODE)


async def test_network_error_propagates():
    session = MagicMock()
    session.get.side_effect = aiohttp.ClientError("boom")
    with pytest.raises(aiohttp.ClientError):
        await EvriApiClient(session).async_get_parcel(CODE, POSTCODE)


@pytest.mark.parametrize(
    ("status", "payload", "message"),
    [
        (503, {}, "HTTP 503"),
        (200, [], "unexpected detail"),
        (200, {}, "missing results"),
    ],
)
async def test_detail_rejects_bad_envelopes(status, payload, message):
    client = EvriApiClient(_session(_response(status, payload)))
    with pytest.raises(EvriApiError, match=message):
        await client._get_detail(CODE, POSTCODE, URN)


async def test_detail_rejects_multiple_nonmatching_results():
    payload = {"results": [{"parcelIdentifiers": None}, {"parcelIdentifiers": ["bad"]}]}
    client = EvriApiClient(_session(_response(200, payload)))
    with pytest.raises(EvriApiError, match="no matching"):
        await client._get_detail(CODE, POSTCODE, URN)


async def test_cached_urn_reresolve_can_become_unknown():
    client = EvriApiClient(
        _session(
            _response(200, {"results": []}),
            _response(400, {"errors": [{"message": "Invalid reference: synthetic"}]}),
        )
    )
    client._urn_cache[CODE] = URN
    assert await client.async_get_parcel(CODE, POSTCODE) is None
