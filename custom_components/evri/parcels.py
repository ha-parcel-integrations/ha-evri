"""Pure helpers for Evri parcel normalization and filtering."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta, tzinfo
import logging
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
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Normalize ISO strings or epoch milliseconds to an ISO timestamp."""
    global _unparseable_timestamp_logged  # noqa: PLW0603 - one-shot warning flag
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
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
        "text": f"{round(length)} x {round(width)} x {round(height)} cm",
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


def _moment(value: Any, zone: tzinfo) -> tuple[datetime, bool, str] | None:
    """Return ``(aware datetime, date_only, text)`` for a carrier time, or ``None``.

    A naive time or a bare date gets ``zone``; an aware time keeps its offset
    and, when it came in as a string, its own spelling.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time.min, tzinfo=zone)
        return moment, True, moment.isoformat()
    elif isinstance(value, str) and value:
        try:
            if len(value) == 10:
                moment = datetime.combine(
                    date.fromisoformat(value), time.min, tzinfo=zone
                )
                return moment, True, moment.isoformat()
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo:
            return parsed, False, value
    else:
        return None
    moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    return moment, False, moment.isoformat()


def _end_of_day(moment: datetime, zone: tzinfo) -> datetime:
    local = moment.astimezone(zone)
    return datetime.combine(local.date(), time(23, 59, 59), tzinfo=zone)


def planned_window(
    start: Any, end: Any, *, zone: tzinfo
) -> tuple[str | None, str | None]:
    """Return ``(planned_from, planned_to)`` in the canonical window shape.

    ``start`` and ``end`` are ISO strings, dates or datetimes, or ``None``; an
    epoch number must be converted first (see ``to_iso_timestamp``). A window
    keeps both ends. One moment with a time ends at ``23:59:59`` the same local
    day; a date covers ``00:00:00`` to ``23:59:59``; two dates run from the
    first day's start to the last day's end. Equal values are one moment,
    reversed values are swapped, and ``planned_to`` is never set without
    ``planned_from``. ``zone`` is the carrier's own zone: it dates naive times
    and bare dates and decides which day a moment ends on, so a ``Z`` moment
    still ends on the carrier's local day. When nothing is left of that day
    after the moment, there is no ``planned_to``.
    """
    first = _moment(start, zone)
    last = _moment(end, zone)
    if first is None:
        first, last = last, None
    if first is None:
        return None, None

    def closing(moment: tuple[datetime, bool, str]) -> datetime:
        return _end_of_day(moment[0], zone) if moment[1] else moment[0]

    if last is not None and closing(last) < first[0]:
        first, last = last, first
    if last is not None and closing(last) == first[0]:
        last = None

    if last is None:
        end_of_day = _end_of_day(first[0], zone)
        return first[2], end_of_day.isoformat() if end_of_day > first[0] else None
    return first[2], closing(last).isoformat() if last[1] else last[2]


_NO_WINDOW_STATUSES = frozenset(
    {ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED}
)


def planned_for_status(
    status: ParcelStatus, planned_from: str | None, planned_to: str | None
) -> tuple[str | None, str | None]:
    """Empty the delivery window once the parcel is at its destination or done."""
    if status in _NO_WINDOW_STATUSES:
        return None, None
    return planned_from, planned_to


def is_pickup(
    route_flag: bool | None,
    status: ParcelStatus,
    event_statuses: Iterable[ParcelStatus | None] = (),
) -> bool:
    """Whether the delivery route ends at a pickup point.

    The carrier's own route flag wins; otherwise the parcel having reached
    ``AT_PICKUP_POINT`` at any point counts, so the value survives collection.
    """
    if route_flag:
        return True
    return status is ParcelStatus.AT_PICKUP_POINT or any(
        event is ParcelStatus.AT_PICKUP_POINT for event in event_statuses
    )


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
    global _eta_shape_logged  # noqa: PLW0603 - one-shot warning flag
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
    # 5_SHOP is the collection scan: it keeps the route a pickup after collection.
    pickup = is_pickup(
        bool(raw.get("parcelShopDelivery"))
        or any(_stage_code(event) == "5_SHOP" for _, event in ordered),
        status,
        (
            map_event_status(_stage_code(event), return_parcel=return_parcel)
            for _, event in ordered
        ),
    )
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
        return (parsed is None, parsed or datetime.max.replace(tzinfo=UTC))

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
    cutoff = datetime.now(UTC) - timedelta(days=amount)
    return [
        parcel
        for parcel in parcels
        if (parse_iso(parcel.get("delivered_at")) or datetime.min.replace(tzinfo=UTC))
        >= cutoff
    ]
