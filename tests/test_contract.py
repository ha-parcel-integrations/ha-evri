"""The parcel contract's checks, run over every normalised sample."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.evri.const import ParcelStatus
from custom_components.evri.parcels import normalize_parcel, parse_iso, planned_window

from .payloads import active_sample, delivered_sample, event, pickup_sample

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


def _collected() -> dict:
    sample = pickup_sample()
    sample["parcelShopDelivery"] = False
    sample["trackingEvents"] = [
        event("5_SHOP", "2026-09-19T16:00:00Z", "COLLECTED_FROM_PARCELSHOP"),
        event("4_SHOP", "2026-09-19T09:00:00Z", "ARRIVED_PARCELSHOP"),
        event("2", "2026-09-18T09:00:00Z"),
    ]
    return sample


def _returning() -> dict:
    sample = active_sample(stage="4")
    sample["returnParcel"] = True
    return sample


def _flagged_in_transit() -> dict:
    sample = active_sample(stage="3")
    sample["parcelShopDelivery"] = True
    return sample


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "at_pickup_point": pickup_sample,
    "collected": _collected,
    "returning": _returning,
    "flagged_in_transit": _flagged_in_transit,
    "problem": lambda: active_sample(stage="6"),
    "bare": lambda: {"_configuredTrackingCode": "H00AAA0000000009"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_collected_pickup_parcel_stays_pickup():
    parcel = normalize_parcel(_collected())
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-09-19T16:00:00Z"
    assert parcel["pickup"] is True


def test_a_collection_scan_alone_marks_the_route_as_pickup():
    sample = _collected()
    sample["trackingEvents"] = sample["trackingEvents"][:1]
    assert normalize_parcel(sample)["pickup"] is True


def test_a_home_delivery_is_not_pickup():
    assert normalize_parcel(delivered_sample())["pickup"] is False


def test_the_route_flag_makes_an_in_flight_parcel_pickup():
    parcel = normalize_parcel(_flagged_in_transit())
    assert parcel["status"] is ParcelStatus.IN_TRANSIT
    assert parcel["pickup"] is True


def test_a_returning_parcel_is_not_delivered():
    parcel = normalize_parcel(_returning())
    assert parcel["status"] is ParcelStatus.RETURNING
    assert parcel["delivered"] is False


def test_a_moment_ends_at_the_end_of_that_day():
    zone = ZoneInfo("Europe/London")
    start, end = planned_window("2026-09-18T13:00:00Z", None, zone=zone)
    assert parse_iso(start) == datetime(2026, 9, 18, 13, 0, tzinfo=UTC)
    stop = datetime.fromisoformat(end)
    assert (stop.hour, stop.minute, stop.second, stop.microsecond) == (23, 59, 59, 0)
    assert stop.date() == datetime(2026, 9, 18).date()


def test_a_date_covers_the_whole_day():
    start, end = planned_window("2026-09-18", None, zone=ZoneInfo("Europe/London"))
    first = datetime.fromisoformat(start)
    last = datetime.fromisoformat(end)
    assert (first.hour, first.minute, first.second) == (0, 0, 0)
    assert (last.hour, last.minute, last.second, last.microsecond) == (23, 59, 59, 0)
    assert first.date() == last.date()
