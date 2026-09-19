"""Synthetic Evri payloads preserving the live-confirmed schema."""

from __future__ import annotations

ACTIVE_CODE = "H00AAA0000000001"
DELIVERED_CODE = "H00AAA0000000002"
POSTCODE = "SW1A1AA"


def event(stage: str, timestamp: str, point: str = "HUB") -> dict:
    """Build one privacy-safe tracking event."""
    return {
        "dateTime": timestamp,
        "trackingStage": {"trackingStageCode": stage},
        "trackingPoint": {"trackingPointCode": point, "description": "discard me"},
        "eta": None,
    }


def active_sample(code: str = ACTIVE_CODE, stage: str = "4_COURIER") -> dict:
    """Build an active synthetic parcel response."""
    return {
        "_configuredTrackingCode": code,
        "discriminator": "REDACTED",
        "creationDate": "2026-09-17T10:00:00Z",
        "serviceType": "STANDARD",
        "returnParcel": False,
        "parcelShopDelivery": stage in {"4_SHOP", "4_LOCKER"},
        "collectionParcel": False,
        "sender": {"displayName": "Example Sender", "clientId": 999},
        "recipient": {"name": "Must Not Ship"},
        "trackingEvents": [
            event(stage, "2026-09-18T12:00:00Z", "COURIER_DELIVERY"),
            event("1", "2026-09-17T10:00:00Z", "EXPECTED"),
        ],
    }


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """Build a delivered synthetic parcel response."""
    sample = active_sample(code, "5_COURIER")
    sample["trackingEvents"].insert(0, event("4_COURIER", "2026-09-18T08:00:00Z"))
    return sample


def pickup_sample(code: str = ACTIVE_CODE) -> dict:
    """Build a parcel waiting at a pickup point."""
    return active_sample(code, "4_SHOP")
