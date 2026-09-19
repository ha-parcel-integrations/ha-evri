"""Tests for Evri canonical normalization."""

from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.evri.const import (
    CAPABILITIES,
    DOMAIN,
    KNOWN_CAPABILITIES,
    ParcelStatus,
)
from custom_components.evri.parcels import (
    _unmapped_statuses_logged,
    apply_delivered_filter,
    build_history,
    format_dimensions,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    sort_parcels_by_ts,
    to_iso_timestamp,
    tracked_direction,
    tracking_url,
)

from .payloads import ACTIVE_CODE, active_sample, delivered_sample, event, pickup_sample

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("0", ParcelStatus.REGISTERED),
        ("1", ParcelStatus.REGISTERED),
        ("2", ParcelStatus.IN_TRANSIT),
        ("3", ParcelStatus.IN_TRANSIT),
        ("4", ParcelStatus.IN_TRANSIT),
        ("4_COURIER", ParcelStatus.OUT_FOR_DELIVERY),
        ("4_SHOP", ParcelStatus.AT_PICKUP_POINT),
        ("4_LOCKER", ParcelStatus.AT_PICKUP_POINT),
        ("5_COURIER", ParcelStatus.DELIVERED),
        ("5_SHOP", ParcelStatus.DELIVERED),
        ("A", ParcelStatus.RETURNING),
        ("6", ParcelStatus.PROBLEM),
        ("6_INFO", ParcelStatus.PROBLEM),
        ("6_ACTION", ParcelStatus.PROBLEM),
    ],
)
def test_exact_status_map(code, expected):
    assert map_parcel_status(code) is expected


def test_return_override_and_unknown_warning(caplog):
    assert map_parcel_status("3", return_parcel=True) is ParcelStatus.RETURNING
    assert map_parcel_status(None) is ParcelStatus.UNKNOWN
    assert map_parcel_status("5_LOCKER") is ParcelStatus.UNKNOWN
    assert map_event_status("5_LOCKER") is None
    assert len([r for r in caplog.records if "5_LOCKER" in r.message]) == 1
    assert _unmapped_statuses_logged == {"5_LOCKER"}


def test_normalize_exact_shape_and_privacy():
    parcel = normalize_parcel(active_sample())
    assert list(parcel) == CANONICAL_KEYS
    assert parcel["barcode"] == ACTIVE_CODE
    assert parcel["status"] is ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["sender"] == "Example Sender"
    assert parcel["receiver"] is None
    assert parcel["planned_from"] is None
    assert parcel["weight"] is None
    assert "recipient" not in parcel["raw"]
    assert "sender" not in parcel["raw"]
    assert parcel["raw"]["trackingEvents"][0] == {
        "dateTime": "2026-09-17T10:00:00Z",
        "trackingStageCode": "1",
        "trackingPointCode": "EXPECTED",
    }


def test_current_event_selected_by_timestamp_and_history_sorted():
    raw = active_sample()
    raw["trackingEvents"].append(event("2", "2026-09-18T10:00:00Z"))
    parcel = normalize_parcel(raw, include_history=True)
    assert parcel["raw_status"] == "4_COURIER"
    assert [item["raw_status"] for item in parcel["history"]] == ["1", "2", "4_COURIER"]


def test_delivered_timestamp_and_pickup():
    delivered = normalize_parcel(delivered_sample())
    assert delivered["delivered"] is True
    assert delivered["delivered_at"] == "2026-09-18T12:00:00Z"
    pickup = normalize_parcel(pickup_sample())
    assert pickup["status"] is ParcelStatus.AT_PICKUP_POINT
    assert pickup["pickup"] is True
    assert pickup["pickup_point"] is None


def test_pending_and_history_cap():
    parcel = normalize_parcel({"_configuredTrackingCode": "ABC"})
    assert parcel["status"] is ParcelStatus.UNKNOWN
    assert parcel["history"] is None
    events = [event("2", f"2026-09-{day:02d}T00:00:00Z") for day in range(1, 26)]
    assert len(build_history(events)) == 20


def test_nonempty_eta_warns_without_publishing_values(caplog):
    raw = active_sample()
    raw["trackingEvents"][0]["eta"] = {"from": "private", "to": "private"}
    parcel = normalize_parcel(raw)
    assert parcel["planned_from"] is None
    assert "keys=['from', 'to']" in caplog.text
    assert "private" not in caplog.text


def test_tracking_point_code_warns_once(caplog):
    raw = active_sample()
    raw["trackingEvents"][0]["trackingPoint"]["trackingPointCode"] = "UNSEEN_CODE"
    normalize_parcel(raw)
    normalize_parcel(raw)
    assert (
        len(
            [
                record
                for record in caplog.records
                if "tracking_point_code=UNSEEN_CODE" in record.message
            ]
        )
        == 1
    )


@pytest.mark.parametrize(
    "point_code",
    [
        "EXPECTED",
        "ARRIVED_PARCELSHOP",
        "COLLECTED_FROM_PARCELSHOP",
        "PROCESSING_DEPOT",
        "PROCESSING_HUB",
        "COURIER_DELIVERY",
        "LOCAL_DEPOT_INBOUND",
        "DELAY_FIX_NWD",
    ],
)
def test_reviewed_tracking_point_codes_do_not_warn(point_code, caplog):
    raw = active_sample()
    raw["trackingEvents"][0]["trackingPoint"]["trackingPointCode"] = point_code
    normalize_parcel(raw)
    assert "tracking_point_code=" not in caplog.text


def test_helpers(caplog):
    assert parse_iso("2026-09-18T10:00:00Z") is not None
    assert parse_iso("bad") is None
    assert to_iso_timestamp(0) == "1970-01-01T00:00:00+00:00"
    assert to_iso_timestamp("bad") is None
    assert "unparseable" in caplog.text
    assert format_dimensions(1, 2, 3)["text"] == "1 x 2 x 3 cm"
    assert format_dimensions(1, None, 3) is None
    assert tracking_url("ABC").endswith("/ABC/details")
    assert tracking_url(None) is None
    assert tracked_direction({}) == "incoming"


def test_sort_and_delivered_filters():
    parcels = [
        {"barcode": "new", "delivered_at": "2026-09-18T12:00:00Z"},
        {"barcode": "bad", "delivered_at": "bad"},
        {"barcode": "old", "delivered_at": "2026-09-17T12:00:00Z"},
    ]
    assert [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ] == ["new", "old", "bad"]
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={"delivered_filter_type": "parcels", "delivered_filter_amount": 1},
    )
    assert apply_delivered_filter(parcels, entry) == parcels[:1]
    now = datetime.now(timezone.utc)
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={"delivered_filter_type": "days", "delivered_filter_amount": 7},
    )
    dated = [
        {"delivered_at": (now - timedelta(days=1)).isoformat()},
        {"delivered_at": (now - timedelta(days=9)).isoformat()},
    ]
    assert apply_delivered_filter(dated, entry) == dated[:1]


def test_capabilities_are_truthful():
    assert CAPABILITIES == {"url", "history"}
    assert CAPABILITIES <= KNOWN_CAPABILITIES
