from __future__ import annotations

import json
from pathlib import Path

import pytest

from passage_planner import coastal
from passage_planner.errors import CoastalDataError


@pytest.fixture()
def straight_coast(tmp_path: Path) -> Path:
    """A synthetic coastline: one straight north-south line at lon -80,
    running lat 10 to lat 20. Lets the math be checked against a known
    answer instead of real-world geography."""
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-80.0, 10.0], [-80.0, 20.0]],
                },
            }
        ],
    }
    p = tmp_path / "synthetic_coast.geojson"
    p.write_text(json.dumps(geojson), encoding="utf-8")
    return p


def test_distance_on_the_coastline_is_zero(straight_coast: Path) -> None:
    assert coastal.distance_to_coast_nm(15.0, -80.0, data_path=straight_coast) == pytest.approx(
        0.0, abs=1e-6
    )


def test_distance_one_degree_of_longitude_east(straight_coast: Path) -> None:
    # 1 deg longitude at lat 15 = 60 * cos(15deg) nm ~= 57.96 nm
    dist = coastal.distance_to_coast_nm(15.0, -79.0, data_path=straight_coast)
    assert dist == pytest.approx(60.0 * 0.9659258, rel=1e-3)


def test_is_in_territorial_waters_true_close_in(straight_coast: Path) -> None:
    # ~0.1 deg east ~= 5.8 nm at this latitude -- inside the 12 NM line.
    assert coastal.is_in_territorial_waters(15.0, -79.9, data_path=straight_coast)


def test_is_in_territorial_waters_false_offshore(straight_coast: Path) -> None:
    assert not coastal.is_in_territorial_waters(15.0, -79.0, data_path=straight_coast)


def test_out_of_bounds_point_raises() -> None:
    with pytest.raises(CoastalDataError):
        coastal.distance_to_coast_nm(50.0, 10.0)  # mid-Europe, well outside BOUNDS


def test_missing_dataset_raises(tmp_path: Path) -> None:
    coastal._load_segments.cache_clear()
    with pytest.raises(CoastalDataError):
        coastal.distance_to_coast_nm(15.0, -80.0, data_path=tmp_path / "nope.geojson")


def test_real_bundled_dataset_mid_gulf_is_offshore() -> None:
    # Roughly the middle of the Gulf of Mexico -- clearly more than 12 NM
    # from any coastline in the bundled dataset.
    assert not coastal.is_in_territorial_waters(25.0, -90.0)
    assert coastal.distance_to_coast_nm(25.0, -90.0) > 50.0
