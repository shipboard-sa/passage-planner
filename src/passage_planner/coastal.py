"""Distance from a position to the nearest coastline.

Used to gate anything that should behave differently inside vs. outside
Starlink Personal Maritime's 12 NM unlimited-data boundary (see
:mod:`passage_planner.comms`) -- e.g. whether it's safe to run a bulk backup
sync without incurring metered Ocean Mode ($/GB) charges.

The bundled coastline (``data/coastline_caribbean.geojson``) is clipped from
Natural Earth's 1:10m coastline dataset (naturalearthdata.com, public domain)
to the boat's planned cruising region: lon -99..-55, lat 4..33 -- Gulf of
Mexico, US Atlantic seaboard, Bahamas, the wider Caribbean including the
Lesser Antilles and ABC islands, with margin toward Central America. A point
outside that box raises :class:`~passage_planner.errors.CoastalDataError`
rather than silently returning a wrong answer; re-clip the dataset (see
``scripts`` note below) before relying on this further afield.

Distance uses a local equirectangular approximation centered on the query
point, not a true geodesic projection -- accurate to well under 1% error at
the few-to-few-hundred-NM scale this module is used for, without pulling in
a full projection library (pyproj/geopandas).

Note: Starlink's actual maritime service-area boundary is an H3 hexagonal
cell grid (~15-mile cells), not a literal coastline buffer -- the 12 NM rule
is Starlink's own documented land/ocean threshold, but because the grid is
discrete hexagons rather than a smooth coastline offset, real edge cases
occur: large inland waters (e.g. the Great Lakes) can be classified "land,"
while enclosed coastal bays/sounds (e.g. Chesapeake Bay) are often
classified "Ocean" even though they're well inside 12 NM of shore. That
matters directly for this boat's shakedown route (Mississippi Sound, Mobile
Bay) -- this module can say "inside 12 NM, should be cheap" in a sound or
bay where Starlink is actually billing Ocean Mode. Treat this module's
output as a conservative *planning* heuristic, not a billing guarantee --
confirm against the terminal's actual mode indicator when it matters.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from shapely.geometry import MultiLineString, Point

from .errors import CoastalDataError

_DEFAULT_DATA_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "coastline_caribbean.geojson"
)
_NM_PER_DEG_LAT = 60.0


@dataclass(frozen=True, slots=True)
class CoastalBounds:
    lon_min: float
    lat_min: float
    lon_max: float
    lat_max: float

    def contains(self, lat: float, lon: float) -> bool:
        return self.lon_min <= lon <= self.lon_max and self.lat_min <= lat <= self.lat_max


BOUNDS = CoastalBounds(lon_min=-99.0, lat_min=4.0, lon_max=-55.0, lat_max=33.0)

Segment = tuple[tuple[float, float], ...]


@lru_cache(maxsize=4)
def _load_segments(path: str) -> tuple[Segment, ...]:
    p = Path(path)
    if not p.is_file():
        raise CoastalDataError(f"{p}: coastline dataset not found")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CoastalDataError(f"{p}: {exc}") from exc

    segments: list[Segment] = []
    for feat in data.get("features", ()):
        geom = feat.get("geometry", {})
        if geom.get("type") != "LineString":
            continue
        coords = geom.get("coordinates", [])
        if len(coords) >= 2:
            segments.append(tuple((float(lon), float(lat)) for lon, lat in coords))
    if not segments:
        raise CoastalDataError(f"{p}: no LineString features found")
    return tuple(segments)


def distance_to_coast_nm(
    lat: float, lon: float, *, data_path: str | Path = _DEFAULT_DATA_PATH
) -> float:
    """Approximate distance (NM) from ``(lat, lon)`` to the nearest coastline
    in the bundled dataset. Raises :class:`CoastalDataError` if the point is
    outside :data:`BOUNDS` or the dataset can't be read."""
    if not BOUNDS.contains(lat, lon):
        raise CoastalDataError(
            f"({lat}, {lon}) is outside the bundled coastline coverage "
            f"(lon {BOUNDS.lon_min}..{BOUNDS.lon_max}, lat {BOUNDS.lat_min}..{BOUNDS.lat_max}) -- "
            "re-clip data/coastline_caribbean.geojson for this area before relying on it here"
        )
    segments = _load_segments(str(data_path))

    cos_lat0 = math.cos(math.radians(lat))
    lon_scale = _NM_PER_DEG_LAT * cos_lat0

    lines = [
        [((s_lon - lon) * lon_scale, (s_lat - lat) * _NM_PER_DEG_LAT) for s_lon, s_lat in seg]
        for seg in segments
    ]
    mls = MultiLineString(lines)
    return float(Point(0.0, 0.0).distance(mls))


def is_in_territorial_waters(
    lat: float,
    lon: float,
    *,
    limit_nm: float = 12.0,
    data_path: str | Path = _DEFAULT_DATA_PATH,
) -> bool:
    """True if within ``limit_nm`` of the nearest coastline -- the boundary
    Starlink Personal Maritime uses for unlimited vs. metered Ocean Mode data
    (see :mod:`passage_planner.comms`, ``_starlink_reminder``). A conservative
    planning heuristic, not a guarantee of Starlink's actual billing state."""
    return distance_to_coast_nm(lat, lon, data_path=data_path) <= limit_nm
