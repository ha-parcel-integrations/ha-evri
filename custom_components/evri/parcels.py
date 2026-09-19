"""Pure helpers for Evri parcel normalization and filtering."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_DIRECTION,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DEFAULT_DIRECTION,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)
NEW_ISSUE_URL = "https://github.com/ha-parcel-integrations/ha-evri/issues/new?template=unrecognised_status.yml"

_STATUS_MAP = {
    "0": ParcelStatus.REGISTERED,
    "1": ParcelStatus.REGISTERED,
    "2": ParcelStatus.IN_TRANSIT,
    "3": ParcelStatus.IN_TRANSIT,
    "4": ParcelStatus.IN_TRANSIT,
    "4_COURIER": ParcelStatus.OUT_FOR_DELIVERY,
    "4_SHOP": ParcelStatus.AT_PICKUP_POINT,
    "4_LOCKER": ParcelStatus.AT_PICKUP_POINT,
    "5_COURIER": ParcelStatus.DELIVERED,
    "5_SHOP": ParcelStatus.DELIVERED,
    "A": ParcelStatus.RETURNING,
    "6": ParcelStatus.PROBLEM,
    "6_INFO": ParcelStatus.PROBLEM,
    "6_ACTION": ParcelStatus.PROBLEM,
}

# Tracking-point codes describe the operational scan within a stage. They are
# retained in the privacy-minimal raw timeline, but do not override the
# canonical status derived from trackingStageCode.
_KNOWN_TRACKING_POINT_CODES = frozenset(
    {
        "EXPECTED",
        "ARRIVED_PARCELSHOP",
        "COLLECTED_FROM_PARCELSHOP",
        "PROCESSING_DEPOT",
        "PROCESSING_HUB",
        "COURIER_DELIVERY",
        "LOCAL_DEPOT_INBOUND",
        "DELAY_FIX_NWD",
    }
)
_unmapped_statuses_logged: set[str] = set()
_tracking_point_codes_logged: set[str] = set()
_unparseable_timestamp_logged = False
_eta_shape_logged = False


def _warn_unmapped_status(code: str) -> None:
    if code in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(code)
    _LOGGER.warning(
        "Unrecognised Evri status — help us map it. Open an issue and paste "
        "this line: %s\n  status=%s → reported as 'unknown'",
        NEW_ISSUE_URL,
        code,
    )


def _warn_tracking_point_once(code: str) -> None:
    """Request privacy-safe reports until the point-code catalogue is known."""
    if code in _KNOWN_TRACKING_POINT_CODES or code in _tracking_point_codes_logged:
        return
    _tracking_point_codes_logged.add(code)
    _LOGGER.warning(
        "Unreviewed Evri tracking point code — help us catalogue it. Open an issue "
        "and paste this line: %s\n  tracking_point_code=%s",
        NEW_ISSUE_URL,
        code,
    )


def map_parcel_status(code: str | None, *, return_parcel: bool = False) -> ParcelStatus:
    """Map an exact Evri tracking-stage code to the canonical enum."""
    if not code:
        return ParcelStatus.UNKNOWN
    if return_parcel and code in {"3", "4"}:
        return ParcelStatus.RETURNING
    mapped = _STATUS_MAP.get(str(code))
    if mapped is None:
        _warn_unmapped_status(str(code))
        return ParcelStatus.UNKNOWN
    return mapped


def map_event_status(
    code: str | None, *, return_parcel: bool = False
) -> ParcelStatus | None:
    """Map a history stage, retaining null for an unknown mapping."""
    if not code:
        return None
    status = map_parcel_status(code, return_parcel=return_parcel)
    return None if status is ParcelStatus.UNKNOWN else status


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO timestamp into an aware datetime when possible."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Normalize ISO strings or epoch milliseconds to an ISO timestamp."""
    global _unparseable_timestamp_logged
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    elif isinstance(value, str) and parse_iso(value) is not None:
        return value
    if not _unparseable_timestamp_logged:
        _unparseable_timestamp_logged = True
        _LOGGER.warning("Evri returned an unparseable event timestamp; value omitted")
    return None


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return canonical centimetre dimensions when every side is present."""
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{int(length)} x {int(width)} x {int(height)} cm",
    }


def _stage_code(event: dict[str, Any]) -> str | None:
    stage = event.get("trackingStage")
    if not isinstance(stage, dict):
        return None
    code = stage.get("trackingStageCode")
    return str(code) if code is not None else None


def _events(raw: dict[str, Any]) -> list[dict[str, Any]]:
    value = raw.get("trackingEvents")
    return (
        [event for event in value if isinstance(event, dict)]
        if isinstance(value, list)
        else []
    )


def _ordered_events(raw: dict[str, Any]) -> list[tuple[datetime, dict[str, Any]]]:
    result: list[tuple[datetime, dict[str, Any]]] = []
    for event in _events(raw):
        parsed = parse_iso(to_iso_timestamp(event.get("dateTime")))
        if parsed is not None:
            result.append((parsed, event))
    result.sort(key=lambda item: item[0])
    return result


def build_history(
    events: list | None,
    *,
    return_parcel: bool = False,
    max_events: int = HISTORY_MAX_EVENTS,
) -> list[dict]:
    """Build sorted, capped canonical history entries."""
    ordered: list[tuple[datetime, dict]] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = to_iso_timestamp(event.get("dateTime"))
        parsed = parse_iso(timestamp)
        if timestamp is None or parsed is None:
            continue
        raw_status = _stage_code(event)
        ordered.append(
            (
                parsed,
                {
                    "timestamp": timestamp,
                    "status": map_event_status(raw_status, return_parcel=return_parcel),
                    "raw_status": raw_status,
                },
            )
        )
    ordered.sort(key=lambda item: item[0])
    return [entry for _, entry in ordered[-max_events:]]


def tracking_url(tracking_code: str | None) -> str | None:
    """Return Evri's human-facing parcel deep link."""
    return TRACKING_URL.format(tracking_code=tracking_code) if tracking_code else None


def _safe_raw(
    raw: dict[str, Any], ordered: list[tuple[datetime, dict[str, Any]]]
) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key in (
        "discriminator",
        "creationDate",
        "serviceType",
        "returnParcel",
        "parcelShopDelivery",
        "collectionParcel",
    ):
        if key in raw:
            safe[key] = raw[key]
    safe["trackingEvents"] = []
    for _, event in ordered:
        point = event.get("trackingPoint")
        point_code = point.get("trackingPointCode") if isinstance(point, dict) else None
        safe["trackingEvents"].append(
            {
                "dateTime": to_iso_timestamp(event.get("dateTime")),
                "trackingStageCode": _stage_code(event),
                "trackingPointCode": point_code,
            }
        )
    return safe


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return the exact suite canonical shape with a privacy-minimal raw block."""
    global _eta_shape_logged
    barcode = raw.get("_configuredTrackingCode")
    if not _eta_shape_logged:
        eta = next(
            (
                event.get("eta")
                for event in _events(raw)
                if event.get("eta") is not None
            ),
            None,
        )
        if eta is not None:
            _eta_shape_logged = True
            keys = sorted(eta) if isinstance(eta, dict) else [type(eta).__name__]
            _LOGGER.warning(
                "Evri returned a non-empty ETA shape; values remain omitted pending review (keys=%s)",
                keys,
            )
    ordered = _ordered_events(raw)
    for _, event in ordered:
        point = event.get("trackingPoint")
        point_code = point.get("trackingPointCode") if isinstance(point, dict) else None
        if point_code:
            _warn_tracking_point_once(str(point_code))
    current = ordered[-1][1] if ordered else {}
    raw_status = _stage_code(current)
    return_parcel = bool(raw.get("returnParcel"))
    status = map_parcel_status(raw_status, return_parcel=return_parcel)
    delivered = status is ParcelStatus.DELIVERED
    delivered_at = None
    if delivered:
        for _, event in reversed(ordered):
            code = _stage_code(event)
            if (
                map_parcel_status(code, return_parcel=return_parcel)
                is ParcelStatus.DELIVERED
            ):
                delivered_at = to_iso_timestamp(event.get("dateTime"))
                break
    sender = raw.get("sender")
    sender_name = sender.get("displayName") if isinstance(sender, dict) else None
    pickup = bool(raw.get("parcelShopDelivery")) or raw_status in {"4_SHOP", "4_LOCKER"}
    return {
        "carrier": "Evri",
        "barcode": barcode,
        "sender": sender_name,
        "receiver": None,
        "status": status,
        "raw_status": raw_status,
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": None,
        "planned_to": None,
        "pickup": pickup,
        "pickup_point": None,
        "url": tracking_url(barcode),
        "weight": None,
        "dimensions": None,
        "history": build_history(_events(raw), return_parcel=return_parcel)
        if include_history
        else None,
        "raw": _safe_raw(raw, ordered),
    }


def tracked_direction(parcel: dict[str, Any]) -> str:
    """Return a stored direction, defaulting legacy entries to incoming."""
    return str(parcel.get(CONF_DIRECTION) or DEFAULT_DIRECTION)


def sort_parcels_by_ts(
    parcels: list[dict], field: str, *, descending: bool = False
) -> list[dict]:
    """Sort parseable timestamps while leaving missing values last."""

    def key(parcel: dict) -> tuple[bool, datetime]:
        parsed = parse_iso(parcel.get(field))
        return (parsed is None, parsed or datetime.max.replace(tzinfo=timezone.utc))

    known = [parcel for parcel in parcels if parse_iso(parcel.get(field)) is not None]
    unknown = [parcel for parcel in parcels if parse_iso(parcel.get(field)) is None]
    return sorted(known, key=key, reverse=descending) + unknown


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Apply the hub's delivered retention setting."""
    filter_type = entry.options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        entry.options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "parcels":
        return parcels[:amount]
    cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
    return [
        parcel
        for parcel in parcels
        if (
            parse_iso(parcel.get("delivered_at"))
            or datetime.min.replace(tzinfo=timezone.utc)
        )
        >= cutoff
    ]
