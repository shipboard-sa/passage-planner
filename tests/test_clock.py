"""Tests for passage_planner.clock."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from passage_planner.clock import Clock, nearest_civil_zone

NOON_UTC = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)

# A tiny fake resolver: a few boxes, None everywhere else.
_ZONES = {
    "central": ("America/Chicago", lambda la, lo: -95 < lo < -87 and 28 < la < 33),
    "eastern": ("America/New_York", lambda la, lo: -87 < lo < -80 and 24 < la < 32),
    "nassau": ("America/Nassau", lambda la, lo: -80 < lo < -72 and 20 < la < 28),
}


def _resolver(lat: float, lon: float) -> str | None:
    for zone, hit in _ZONES.values():
        if hit(lat, lon):
            return zone
    return None


def test_default_zone_when_no_resolver() -> None:
    c = Clock("America/Chicago")
    assert c.label(NOON_UTC, 27.77, -82.63) == "2026-09-12 07:00 CDT"  # no lookup -> default
    assert c.label(NOON_UTC) == "2026-09-12 07:00 CDT"


def test_per_position_zone_with_resolver() -> None:
    c = Clock("America/Chicago", _resolver)
    assert c.label(NOON_UTC, 30.0, -90.0) == "2026-09-12 07:00 CDT"       # New Orleans
    assert c.label(NOON_UTC, 27.77, -82.63) == "2026-09-12 08:00 EDT"     # Tampa
    assert c.label(NOON_UTC, 25.72, -79.30) == "2026-09-12 08:00 EDT"     # Bimini (Nassau zone)


def test_falls_back_when_position_has_no_zone() -> None:
    c = Clock("Etc/UTC", _resolver)
    # mid-Atlantic: outside every box
    assert c.label(NOON_UTC, 24.0, -60.0) == "2026-09-12 12:00 UTC"


def test_hhmm_and_abbr() -> None:
    c = Clock("America/Chicago", _resolver)
    assert c.hhmm(NOON_UTC, 30.0, -90.0) == "07:00 CDT"
    assert c.abbr(NOON_UTC, 27.77, -82.63) == "EDT"


def test_numeric_ocean_zone_is_spelled_utc() -> None:
    c = Clock("Etc/GMT+5")
    assert c.label(NOON_UTC).endswith(" UTC-05")


def test_localize_naive_to_position_zone() -> None:
    c = Clock("America/Chicago", _resolver)
    naive = datetime(2026, 9, 12, 7, 0)
    aware = c.localize(naive, 30.0, -90.0)
    assert aware.utcoffset().total_seconds() == -5 * 3600  # CDT
    assert aware.astimezone(UTC).hour == 12


def test_bad_default_zone_rejected_at_construction() -> None:
    with pytest.raises((KeyError, ValueError)):
        Clock("Not/AZone")


def test_resolver_errors_fall_back() -> None:
    def boom(lat: float, lon: float) -> str | None:
        raise RuntimeError("polygon data missing")

    c = Clock("America/Chicago", boom)
    assert c.label(NOON_UTC, 30.0, -90.0) == "2026-09-12 07:00 CDT"


def _ocean_lookup(lat: float, lon: float) -> str | None:
    """Land zone only inside a small box around Florida/Bahamas; Etc/GMT+5 in the water."""
    if 24.0 < lat < 27.0 and -80.5 < lon < -77.0:
        return "America/Nassau"
    if lat > 20 and lon > -90:
        return "Etc/GMT+5"
    return "Etc/GMT+2"


def test_ocean_position_takes_the_nearest_civil_zone() -> None:
    # 25.89N 79.82W is the Gulf Stream, in the numeric ocean zone; land is < 1 degree away
    assert _ocean_lookup(25.89, -81.0) == "Etc/GMT+5"
    assert nearest_civil_zone(_ocean_lookup, 25.89, -81.0) == "America/Nassau"


def test_civil_position_is_returned_unchanged() -> None:
    assert nearest_civil_zone(_ocean_lookup, 25.5, -78.0) == "America/Nassau"


def test_open_ocean_keeps_the_numeric_zone() -> None:
    assert nearest_civil_zone(_ocean_lookup, 30.0, -60.0) == "Etc/GMT+5"
    assert nearest_civil_zone(lambda la, lo: None, 30.0, -60.0) is None


def test_clock_uses_the_ocean_resolution_end_to_end() -> None:
    c = Clock("America/Chicago", lambda la, lo: nearest_civil_zone(_ocean_lookup, la, lo))
    assert c.label(NOON_UTC, 25.89, -81.0) == "2026-09-12 08:00 EDT"  # not "07:00 UTC-05"
