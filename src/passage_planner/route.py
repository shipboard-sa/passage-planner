"""GPX route ingest and the in-memory route model.

A planning route is an ordered list of waypoints (from a Garmin MFD / ActiveCaptain
/ OpenCPN GPX export) turned into a sequence of :class:`Leg` objects carrying
great-circle distance and initial bearing, plus helpers the weather and analysis
layers need — a corridor bounding box and position-at-distance interpolation.

The parser is namespace-agnostic (it matches on XML local names) and reads a
``<rte>`` if present, otherwise falls back to the first ``<trk>``. It does not
depend on any third-party GPX library.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .errors import RouteContentError, RouteFileError
from .geo import great_circle_nm, initial_bearing_deg

# Two waypoints closer than this are treated as a duplicate click, not a leg.
_MIN_LEG_NM: float = 0.02


@dataclass(frozen=True, slots=True)
class Waypoint:
    name: str
    lat: float
    lon: float
    description: str | None = None

    def __post_init__(self) -> None:
        if not -90.0 <= self.lat <= 90.0:
            raise RouteContentError(f"waypoint {self.name!r} latitude out of range: {self.lat}")
        if not -180.0 <= self.lon <= 180.0:
            raise RouteContentError(f"waypoint {self.name!r} longitude out of range: {self.lon}")


@dataclass(frozen=True, slots=True)
class Leg:
    index: int
    start: Waypoint
    end: Waypoint
    distance_nm: float
    bearing_deg: float
    cumulative_start_nm: float
    cumulative_end_nm: float


@dataclass(frozen=True, slots=True)
class Route:
    name: str
    waypoints: tuple[Waypoint, ...]
    legs: tuple[Leg, ...]

    @property
    def total_nm(self) -> float:
        return self.legs[-1].cumulative_end_nm if self.legs else 0.0

    def bounding_box(self, margin_deg: float = 1.0) -> tuple[float, float, float, float]:
        """(min_lat, min_lon, max_lat, max_lon) over all waypoints, padded.

        ``margin_deg`` widens the box so a corridor forecast pull covers the
        water either side of the rhumb line, not just the track itself.
        Latitude is clamped to the poles; longitude is left unclamped so the
        caller can decide how to handle an antimeridian crossing (not a case
        that arises in the FL / Bahamas / Caribbean grounds).
        """
        if margin_deg < 0.0:
            raise ValueError("margin_deg must be >= 0")
        lats = [w.lat for w in self.waypoints]
        lons = [w.lon for w in self.waypoints]
        return (
            max(-90.0, min(lats) - margin_deg),
            min(lons) - margin_deg,
            min(90.0, max(lats) + margin_deg),
            max(lons) + margin_deg,
        )

    def leg_at_distance(self, cumulative_nm: float) -> Leg:
        """The leg containing the given cumulative along-route distance."""
        if cumulative_nm < 0.0 or cumulative_nm > self.total_nm + 1e-6:
            raise ValueError(
                f"distance {cumulative_nm} nm is outside the route (0..{self.total_nm:.1f})"
            )
        for leg in self.legs:
            if cumulative_nm <= leg.cumulative_end_nm + 1e-6:
                return leg
        return self.legs[-1]

    def position_at_distance(self, cumulative_nm: float) -> tuple[float, float]:
        """Interpolated (lat, lon) at a cumulative along-route distance.

        Linear interpolation in lat/lon within the containing leg. Legs are
        short enough (a few tens of nm) that the error against a true
        great-circle interior point is negligible for forecast sampling.
        """
        leg = self.leg_at_distance(cumulative_nm)
        span = leg.cumulative_end_nm - leg.cumulative_start_nm
        if span <= 0.0:
            return leg.start.lat, leg.start.lon
        frac = (cumulative_nm - leg.cumulative_start_nm) / span
        frac = max(0.0, min(1.0, frac))
        lat = leg.start.lat + frac * (leg.end.lat - leg.start.lat)
        lon = leg.start.lon + frac * (leg.end.lon - leg.start.lon)
        return lat, lon

    def sample_points(self, step_nm: float) -> list[tuple[float, float, float]]:
        """Evenly spaced (cumulative_nm, lat, lon) samples, endpoints included."""
        if step_nm <= 0.0:
            raise ValueError("step_nm must be > 0")
        out: list[tuple[float, float, float]] = []
        d = 0.0
        while d < self.total_nm:
            lat, lon = self.position_at_distance(d)
            out.append((d, lat, lon))
            d += step_nm
        lat, lon = self.position_at_distance(self.total_nm)
        out.append((self.total_nm, lat, lon))
        return out


# ---------------------------------------------------------------------------
# GPX parsing
# ---------------------------------------------------------------------------


def _local(tag: str) -> str:
    """Strip an XML namespace: ``{ns}rtept`` -> ``rtept``."""
    return tag.rsplit("}", 1)[-1]


def _find_child_text(elem: ET.Element, name: str) -> str | None:
    for child in elem:
        if _local(child.tag) == name and child.text is not None:
            text = child.text.strip()
            return text or None
    return None


def _parse_points(container: ET.Element, point_tag: str) -> list[Waypoint]:
    points: list[Waypoint] = []
    seq = 0
    for elem in container:
        if _local(elem.tag) != point_tag:
            continue
        seq += 1
        try:
            lat = float(elem.attrib["lat"])
            lon = float(elem.attrib["lon"])
        except (KeyError, ValueError) as exc:
            raise RouteContentError(
                f"{point_tag} #{seq} is missing a numeric lat/lon"
            ) from exc
        # The Navionics Boating app exports <rtept> with no <name> child, so
        # fall back to a plain sequence label the briefing can print.
        name = _find_child_text(elem, "name") or f"WP{seq}"
        desc = _find_child_text(elem, "desc")
        points.append(Waypoint(name=name, lat=lat, lon=lon, description=desc))
    return points


def _build_legs(
    points: list[Waypoint],
) -> tuple[tuple[Waypoint, ...], tuple[Leg, ...]]:
    kept: list[Waypoint] = []
    legs: list[Leg] = []
    cumulative = 0.0
    for wp in points:
        if not kept:
            kept.append(wp)
            continue
        prev = kept[-1]
        dist = great_circle_nm(prev.lat, prev.lon, wp.lat, wp.lon)
        if dist < _MIN_LEG_NM:
            # Duplicate / coincident point — drop it rather than emit a zero leg.
            continue
        bearing = initial_bearing_deg(prev.lat, prev.lon, wp.lat, wp.lon)
        legs.append(
            Leg(
                index=len(legs),
                start=prev,
                end=wp,
                distance_nm=dist,
                bearing_deg=bearing,
                cumulative_start_nm=cumulative,
                cumulative_end_nm=cumulative + dist,
            )
        )
        cumulative += dist
        kept.append(wp)
    return tuple(kept), tuple(legs)


def parse_gpx(path: str | Path) -> Route:
    """Parse a GPX file into a :class:`Route`.

    Prefers a ``<rte>`` element; falls back to the first ``<trk>`` (its points
    are the concatenation of every ``<trkseg>``). Raises
    :class:`RouteFileError` for I/O or XML problems and
    :class:`RouteContentError` when the file holds no usable route.
    """
    p = Path(path)
    try:
        tree = ET.parse(p)
    except FileNotFoundError as exc:
        raise RouteFileError(f"GPX file not found: {p}") from exc
    except OSError as exc:
        raise RouteFileError(f"cannot read {p}: {exc}") from exc
    except ET.ParseError as exc:
        raise RouteFileError(f"{p} is not valid XML: {exc}") from exc

    root = tree.getroot()
    if _local(root.tag) != "gpx":
        raise RouteContentError(f"{p}: root element is <{_local(root.tag)}>, expected <gpx>")

    # <rte> path
    for elem in root:
        if _local(elem.tag) == "rte":
            name = _find_child_text(elem, "name") or p.stem
            points = _parse_points(elem, "rtept")
            if len(points) < 2:
                raise RouteContentError(f"{p}: <rte> {name!r} has fewer than 2 route points")
            waypoints, legs = _build_legs(points)
            if not legs:
                raise RouteContentError(f"{p}: <rte> {name!r} collapses to a single point")
            return Route(name=name, waypoints=waypoints, legs=legs)

    # <trk> fallback
    for elem in root:
        if _local(elem.tag) != "trk":
            continue
        name = _find_child_text(elem, "name") or p.stem
        trk_points: list[Waypoint] = []
        for seg in elem:
            if _local(seg.tag) == "trkseg":
                trk_points.extend(_parse_points(seg, "trkpt"))
        if len(trk_points) < 2:
            raise RouteContentError(f"{p}: <trk> {name!r} has fewer than 2 track points")
        waypoints, legs = _build_legs(trk_points)
        if not legs:
            raise RouteContentError(f"{p}: <trk> {name!r} collapses to a single point")
        return Route(name=name, waypoints=waypoints, legs=legs)

    raise RouteContentError(f"{p}: no <rte> or <trk> element found")


def load_route(path: str | Path) -> Route:
    """Public alias for :func:`parse_gpx`."""
    return parse_gpx(path)
