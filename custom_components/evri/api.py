"""Client for Evri's public parcel-tracking platform."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import DETAIL_URL, REFERENCE_URL

_LOGGER = logging.getLogger(__name__)

# Legacy public application credential shipped by Evri's tracking website. It
# is not a user's secret. Evri can reject this at either its API gateway or its
# CloudFront/WAF edge; keep those failures distinct so the latter is not
# misleadingly reported as a bad credential.
_WEBSITE_API_KEY = "0DExZiK9in2ihGce7cDPrnpQ4s4nIpWG"


class EvriApiError(Exception):
    """Raised for an unexpected Evri response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store response metadata used by coordinator backoff."""
        super().__init__(f"Evri API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


class EvriCredentialError(EvriApiError):
    """The carrier rejected its own public website credential."""


class EvriApiClient:
    """Resolve tracking codes to opaque URNs and retrieve parcel details."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize with Home Assistant's managed HTTP session."""
        self._session = session
        self._urn_cache: dict[str, str] = {}
        self._schema_warnings: set[str] = set()

    def _warn_schema_once(self, key: str, message: str) -> None:
        if key not in self._schema_warnings:
            self._schema_warnings.add(key)
            _LOGGER.warning("Evri tracking response changed: %s", message)

    @staticmethod
    def _retry_after(response: aiohttp.ClientResponse) -> float | None:
        value = response.headers.get("Retry-After")
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    async def _json_get(
        self, url: str, *, params: dict[str, str] | None = None
    ) -> tuple[int, Any]:
        async with self._session.get(
            url, params=params, headers={"Apikey": _WEBSITE_API_KEY}
        ) as response:
            status = response.status
            server = response.headers.get("Server", "")
            content_type = response.headers.get("Content-Type", "")
            if status == 403 and (
                server.casefold() == "cloudfront"
                or "text/html" in content_type.casefold()
            ):
                raise EvriApiError(
                    "request blocked by Evri's CloudFront/WAF edge (HTTP 403)",
                    status_code=status,
                )
            if status in (401, 403):
                raise EvriCredentialError(
                    "public website credential rejected", status_code=status
                )
            if status == 429:
                raise EvriApiError(
                    "HTTP 429", status_code=429, retry_after=self._retry_after(response)
                )
            try:
                payload = await response.json(content_type=None)
            except (ValueError, aiohttp.ContentTypeError) as err:
                raise EvriApiError("unparseable response", status_code=status) from err
            return status, payload

    async def _resolve_urn(self, tracking_code: str) -> str | None:
        status, payload = await self._json_get(
            REFERENCE_URL.format(tracking_code=tracking_code)
        )
        if status == 400 and isinstance(payload, dict):
            errors = payload.get("errors")
            if isinstance(errors, list) and any(
                isinstance(error, dict)
                and str(error.get("message", "")).startswith("Invalid reference:")
                for error in errors
            ):
                return None
        if status != 200:
            raise EvriApiError(f"HTTP {status}", status_code=status)
        if not isinstance(payload, dict):
            raise EvriApiError("unexpected reference response")
        identifiers = payload.get("parcelIdentifiers")
        if not isinstance(identifiers, list):
            raise EvriApiError("missing parcelIdentifiers")
        for identifier in identifiers:
            if isinstance(identifier, dict) and isinstance(identifier.get("urn"), str):
                return identifier["urn"]
        self._warn_schema_once("empty_identifiers", "reference contained no URN")
        return None

    async def _get_detail(
        self, tracking_code: str, postcode: str, urn: str
    ) -> dict[str, Any] | None:
        status, payload = await self._json_get(
            DETAIL_URL, params={"uniqueIds": urn, "postcode": postcode}
        )
        if status != 200:
            raise EvriApiError(f"HTTP {status}", status_code=status)
        if not isinstance(payload, dict):
            raise EvriApiError("unexpected detail response")
        results = payload.get("results")
        if not isinstance(results, list):
            raise EvriApiError("missing results")
        dict_results = [result for result in results if isinstance(result, dict)]
        if not dict_results:
            self._warn_schema_once(
                "empty_results", "detail response contained no parcel"
            )
            return None
        for result in dict_results:
            identifiers = result.get("parcelIdentifiers")
            if not isinstance(identifiers, list):
                continue
            for identifier in identifiers:
                if not isinstance(identifier, dict):
                    continue
                value = identifier.get("value") or identifier.get("barcode")
                if isinstance(value, str) and value.upper() == tracking_code:
                    return result
        if len(dict_results) == 1:
            self._warn_schema_once(
                "sole_result", "accepted a sole result without a matching barcode"
            )
            return dict_results[0]
        raise EvriApiError("no matching parcel in detail response")

    async def async_get_parcel(
        self, tracking_code: str, postcode: str
    ) -> dict[str, Any] | None:
        """Return one raw parcel, or ``None`` when Evri does not know it."""
        urn = self._urn_cache.get(tracking_code)
        was_cached = urn is not None
        if urn is None:
            urn = await self._resolve_urn(tracking_code)
            if urn is None:
                return None
            self._urn_cache[tracking_code] = urn
        parcel = await self._get_detail(tracking_code, postcode, urn)
        if parcel is None and was_cached:
            self._urn_cache.pop(tracking_code, None)
            urn = await self._resolve_urn(tracking_code)
            if urn is None:
                return None
            self._urn_cache[tracking_code] = urn
            parcel = await self._get_detail(tracking_code, postcode, urn)
        if parcel is not None:
            parcel = dict(parcel)
            parcel["_configuredTrackingCode"] = tracking_code
        return parcel
