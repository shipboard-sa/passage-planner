"""Render passage times in the local wall-clock zone of a position.

GPS/PNT gives precise UTC; it does not give *local* time. Local time is UTC
plus the offset of whichever civil time zone a position sits in, so turning a
route point into a local time is a lat/lon -> IANA-zone polygon lookup, not
arithmetic on the fix. :class:`Clock` does that lookup (via an injected
resolver so the briefing layer keeps no hard dependency on the polygon data)
and formats the result; with no resolver, or for a point no zone covers, it
falls back to a configured default zone.

Because the lookup is per-position, a passage that crosses a zone boundary
(e.g. the Central/Eastern line off Apalachicola) shows each waypoint in its
own zone automatically.

Purpose: Render passage times in the civil zone local to a position.
Version: v2 (2026-09-21) - open-water positions resolve to the nearest civil zone
    instead of the fixed ``Etc/GMT+N`` ocean zone (which has no daylight time and
    labelled mid-passage legs ``UTC-05`` while the rest read ``EDT``); v1 was the
    per-position lookup.
Scope: PRODUCTION - used by briefing, watch plan and float plan.
Architecture: Built by service.py and cli.py with :func:`timezonefinder_resolver`;
    passed into briefing.py (and the modules it renders). No I/O beyond the offline
    polygon data.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

ZoneResolver = Callable[[float, float], str | None]


def _abbr(local: datetime) -> str:
    """Zone abbreviation, with the numeric ocean zones spelled as ``UTC-06``."""
    name = local.strftime("%Z")
    if name and name[0] in "+-" and name[1:].isdigit():
        return f"UTC{name}"
    return name or "UTC"


class Clock:
    """Format timezone-aware datetimes in the civil zone local to a position."""

    __slots__ = ("_cache", "_default", "_resolver")

    def __init__(self, default_zone: str = "UTC", resolver: ZoneResolver | None = None) -> None:
        # Validate the default now so a bad config fails at load, not mid-briefing.
        ZoneInfo(default_zone)
        self._default = default_zone
        self._resolver = resolver
        self._cache: dict[tuple[float, float], str] = {}

    @property
    def default_zone(self) -> str:
        return self._default

    def zone_at(self, lat: float | None = None, lon: float | None = None) -> str:
        """IANA zone name for a position, or the default when unknown."""
        if lat is None or lon is None or self._resolver is None:
            return self._default
        key = (round(lat, 3), round(lon, 3))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            found = self._resolver(lat, lon)
        except (ValueError, RuntimeError):
            found = None
        zone = found or self._default
        try:
            ZoneInfo(zone)
        except (KeyError, ValueError):
            zone = self._default
        self._cache[key] = zone
        return zone

    def label(
        self, dt: datetime, lat: float | None = None, lon: float | None = None
    ) -> str:
        """``2026-09-12 07:00 CDT`` — date, local time, zone abbreviation."""
        local = dt.astimezone(ZoneInfo(self.zone_at(lat, lon)))
        return f"{local:%Y-%m-%d %H:%M} {_abbr(local)}"

    def hhmm(
        self, dt: datetime, lat: float | None = None, lon: float | None = None
    ) -> str:
        """``07:00 CDT`` — local time and zone abbreviation only."""
        local = dt.astimezone(ZoneInfo(self.zone_at(lat, lon)))
        return f"{local:%H:%M} {_abbr(local)}"

    def abbr(
        self, dt: datetime, lat: float | None = None, lon: float | None = None
    ) -> str:
        """Zone abbreviation only (``CDT``, ``EST``, ``UTC-06``) for ``dt`` at a position."""
        return _abbr(dt.astimezone(ZoneInfo(self.zone_at(lat, lon))))

    def localize(
        self, naive: datetime, lat: float | None = None, lon: float | None = None
    ) -> datetime:
        """Attach the zone local to a position to a naive datetime."""
        return naive.replace(tzinfo=ZoneInfo(self.zone_at(lat, lon)))


# Rings probed around an open-water point, in degrees of latitude (~60 NM per degree).
_OCEAN_PROBE_RADII_DEG = (0.5, 1.0, 2.0, 3.0)
_OCEAN_PROBE_BEARINGS = tuple(range(0, 360, 45))


def nearest_civil_zone(
    lookup: Callable[[float, float], str | None], lat: float, lon: float
) -> str | None:
    """Zone for a position, resolving open water to the nearest civil zone.

    ``timezonefinder`` returns a fixed ``Etc/GMT±N`` zone for international waters.
    Those have no daylight time, so a leg in the Gulf Stream would read ``UTC-05``
    while its neighbours read ``EDT``. This probes rings of growing radius around the
    point and returns the most common non-``Etc/`` zone on the first ring that finds
    one. With no land zone within the largest ring (open ocean) the ``Etc/`` zone is
    returned unchanged, which is the right answer there.
    """
    found = lookup(lat, lon)
    if found is None or not found.startswith("Etc/"):
        return found
    for radius in _OCEAN_PROBE_RADII_DEG:
        votes: dict[str, int] = {}
        for bearing in _OCEAN_PROBE_BEARINGS:
            rad = math.radians(bearing)
            plat = lat + radius * math.cos(rad)
            plon = lon + radius * math.sin(rad) / max(math.cos(math.radians(lat)), 0.1)
            if not -90.0 <= plat <= 90.0:
                continue
            zone = lookup(plat, ((plon + 180.0) % 360.0) - 180.0)
            if zone and not zone.startswith("Etc/"):
                votes[zone] = votes.get(zone, 0) + 1
        if votes:
            return max(votes, key=lambda z: votes[z])
    return found


def timezonefinder_resolver() -> ZoneResolver:
    """A :data:`ZoneResolver` backed by the offline ``timezonefinder`` polygons.

    Import is deferred so environments that never build a briefing (and CI jobs
    that stub the forecast) don't pay the polygon-load cost.
    """
    from timezonefinder import TimezoneFinder

    finder = TimezoneFinder()

    def lookup(lat: float, lon: float) -> str | None:
        return finder.timezone_at(lat=lat, lng=lon)

    def resolve(lat: float, lon: float) -> str | None:
        return nearest_civil_zone(lookup, lat, lon)

    return resolve
