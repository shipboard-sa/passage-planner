"""Spherical-earth geodesy for route work.

Purpose: great-circle distance/bearing, wind-angle and current-component math
    shared across the route pipeline.
Version: v2 (2026-09-22) - added `current_component_kn()` (weather-routing
    step 2).
Scope: PRODUCTION - passage-planner's geometry primitives, used by `route.py`,
    `passage.py` and `analysis.py`.
Architecture: passage-planner subsystem. Pure functions, no I/O.

All angles are degrees on the public surface; the WGS-84 mean radius is used for
distances. Accuracy on the scale of a coastal or Gulf passage is within a few
tenths of a percent of the ellipsoidal value — well inside the noise of a
forecast-driven ETA — and the closed forms here are cheap and branch-free.

Bearings are degrees true in [0, 360). Distances are nautical miles.
"""

from __future__ import annotations

import math
from typing import Final

EARTH_RADIUS_NM: Final[float] = 3440.065  # WGS-84 mean radius, nautical miles
_DEG2RAD: Final[float] = math.pi / 180.0
_RAD2DEG: Final[float] = 180.0 / math.pi


def great_circle_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, nautical miles.

    Uses the haversine form, which stays well-conditioned for the short legs a
    route is made of (the law-of-cosines form loses precision under ~1 nm).
    """
    phi1 = lat1 * _DEG2RAD
    phi2 = lat2 * _DEG2RAD
    dphi = (lat2 - lat1) * _DEG2RAD
    dlambda = (lon2 - lon1) * _DEG2RAD
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_NM * math.asin(min(1.0, math.sqrt(a)))


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, degrees true."""
    phi1 = lat1 * _DEG2RAD
    phi2 = lat2 * _DEG2RAD
    dlambda = (lon2 - lon1) * _DEG2RAD
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    theta = math.atan2(y, x) * _RAD2DEG
    return (theta + 360.0) % 360.0


def destination_point(
    lat: float, lon: float, bearing_deg: float, distance_nm: float
) -> tuple[float, float]:
    """Point reached by steering ``bearing_deg`` for ``distance_nm`` from (lat, lon)."""
    delta = distance_nm / EARTH_RADIUS_NM
    theta = bearing_deg * _DEG2RAD
    phi1 = lat * _DEG2RAD
    lambda1 = lon * _DEG2RAD
    sin_phi2 = math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(theta)
    phi2 = math.asin(max(-1.0, min(1.0, sin_phi2)))
    y = math.sin(theta) * math.sin(delta) * math.cos(phi1)
    x = math.cos(delta) - math.sin(phi1) * sin_phi2
    lambda2 = lambda1 + math.atan2(y, x)
    lat2 = phi2 * _RAD2DEG
    lon2 = ((lambda2 * _RAD2DEG) + 540.0) % 360.0 - 180.0
    return lat2, lon2


def relative_wind_angle_deg(course_deg: float, wind_from_deg: float) -> float:
    """True wind angle off the bow, 0..180 degrees.

    ``wind_from_deg`` is the compass direction the wind blows FROM (met
    convention). The result is the acute-or-obtuse angle between the boat's
    course and the wind, with no port/starboard sign — the polar is assumed
    symmetric (see Polar-Methodology.md), and the caller adds the tack.
    """
    diff = abs((wind_from_deg - course_deg + 180.0) % 360.0 - 180.0)
    return diff


def current_component_kn(course_deg: float, current_to_deg: float, current_kn: float) -> float:
    """Along-course component of a current vector, knots. Positive is favorable
    (pushes the boat along its course), negative is foul.

    ``current_to_deg`` is the direction the current flows TOWARD (oceanographic
    convention) — the opposite sense from ``wind_from_deg``'s meteorological
    "from". This is a projection onto the drawn course, not a set-and-drift
    steering-triangle solve: it does not correct the heading needed to make
    good that course through a cross-current. See
    wiki/Design-Decisions.md#currents-correct-leg-speed-along-the-drawn-course-not-a-steering-triangle.
    """
    return current_kn * math.cos(math.radians(current_to_deg - course_deg))


def point_of_sail(twa_deg: float) -> str:
    """Coarse point-of-sail label from a 0..180 true wind angle."""
    a = abs(twa_deg)
    if a < 30.0:
        return "dead upwind"
    if a < 55.0:
        return "close-hauled"
    if a < 80.0:
        return "close reach"
    if a < 100.0:
        return "beam reach"
    if a < 140.0:
        return "broad reach"
    return "running"
