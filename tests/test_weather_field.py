"""Tests for passage_planner.weather.field (weather-routing step 3)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from passage_planner import parse_gpx
from passage_planner.errors import RoutingError
from passage_planner.weather.field import Field, corridor_grid, fetch_field
from passage_planner.weather.model import ForecastPoint
from passage_planner.weather.openmeteo import OpenMeteoConfig

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# Anchored to "now" so it always stays inside Open-Meteo's +16 day horizon,
# regardless of when the test suite runs.
T0 = (datetime.now(UTC) + timedelta(hours=6)).replace(minute=0, second=0, microsecond=0)


def _route():
    return parse_gpx(FIXTURES / "nola_to_gulfport.gpx")


# -- corridor_grid ------------------------------------------------------


def test_corridor_grid_on_track_matches_sample_points_count() -> None:
    route = _route()
    grid = corridor_grid(route, along_step_nm=20.0, cross_offsets_nm=(0.0,))
    samples = route.sample_points(20.0)
    assert len(grid) == len(samples)


def test_corridor_grid_offsets_add_points() -> None:
    route = _route()
    on_track = corridor_grid(route, along_step_nm=20.0, cross_offsets_nm=(0.0,))
    banded = corridor_grid(route, along_step_nm=20.0, cross_offsets_nm=(-10.0, 0.0, 10.0))
    assert len(banded) > len(on_track)
    assert len(banded) <= len(on_track) * 3


def test_corridor_grid_points_are_deduplicated() -> None:
    route = _route()
    grid = corridor_grid(route, along_step_nm=20.0, cross_offsets_nm=(-10.0, 0.0, 10.0))
    assert len(grid) == len(set(grid))


def test_corridor_grid_rejects_non_positive_step() -> None:
    with pytest.raises(ValueError):
        corridor_grid(_route(), along_step_nm=0.0)


def test_corridor_grid_much_smaller_than_a_bounding_box() -> None:
    # The live spike (2026-09-22) found a bounding-box grid over a 580 NM route
    # balloons to hundreds of points and trips Open-Meteo's rate limit - the
    # whole reason this module grids a corridor instead. Sanity-check the
    # shape stays small for a realistic passage-planning spacing.
    route = _route()
    grid = corridor_grid(route, along_step_nm=15.0, cross_offsets_nm=(-15.0, 0.0, 15.0))
    assert len(grid) < 60


# -- Field.at -------------------------------------------------------------


def _pt(lat: float, lon: float, t: datetime, wind: float = 10.0) -> ForecastPoint:
    return ForecastPoint(
        cumulative_nm=0.0, leg_index=0, lat=lat, lon=lon, valid_time=t,
        source="test", model="synthetic", provenance_tier="secondary", wind_speed_kn=wind,
    )


def test_field_at_picks_nearest_cell_and_time() -> None:
    field = Field(by_point={
        (25.0, -80.0): (
            _pt(25.0, -80.0, T0, wind=10.0),
            _pt(25.0, -80.0, T0 + timedelta(hours=6), wind=14.0),
        ),
        (26.0, -80.0): (_pt(26.0, -80.0, T0, wind=20.0),),
    })
    # Near the first cell, at a time closer to the +6h sample.
    got = field.at(T0 + timedelta(hours=5), 25.01, -80.01)
    assert got is not None
    assert got.wind_speed_kn == pytest.approx(14.0)

    got_far = field.at(T0, 26.0, -80.0)
    assert got_far is not None
    assert got_far.wind_speed_kn == pytest.approx(20.0)


def test_field_at_empty_field_returns_none() -> None:
    assert Field(by_point={}).at(T0, 25.0, -80.0) is None


def test_field_n_points() -> None:
    field = Field(by_point={
        (25.0, -80.0): (_pt(25.0, -80.0, T0),),
        (26.0, -80.0): (_pt(26.0, -80.0, T0),),
    })
    assert field.n_points == 2


# -- fetch_field (mocked HTTP) --------------------------------------------


def _block(nhours: int, wind: float, current: float) -> dict[str, object]:
    times = [(T0 + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(nhours)]
    return {
        "latitude": 25.0, "longitude": -80.0,
        "hourly": {
            "time": times,
            "wind_speed_10m": [wind] * nhours, "wind_direction_10m": [180.0] * nhours,
            "wind_gusts_10m": [wind + 5.0] * nhours, "pressure_msl": [1015.0] * nhours,
            "wave_height": [1.0] * nhours, "wave_direction": [90.0] * nhours,
            "wave_period": [6.0] * nhours, "swell_wave_height": [0.5] * nhours,
            "ocean_current_velocity": [current] * nhours,
            "ocean_current_direction": [40.0] * nhours,
        },
    }


def _counting_handler(calls: list[int]):
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        n = len(request.url.params["latitude"].split(","))
        wind = 12.0 if "marine" not in request.url.host else 0.0
        current = 1.5 if "marine" in request.url.host else 0.0
        body = [_block(24, wind, current) for _ in range(n)]
        return httpx.Response(200, json=body)

    return handle


def _run(coro):
    return asyncio.run(coro)


def test_fetch_field_builds_a_field_from_the_grid(tmp_path: Path) -> None:
    grid = [(25.0, -80.0), (25.5, -80.5)]
    times = [T0 + timedelta(hours=3)]
    calls: list[int] = []

    async def go():
        transport = httpx.MockTransport(_counting_handler(calls))
        async with httpx.AsyncClient(transport=transport) as http:
            return await fetch_field(
                grid, times, "test", config=OpenMeteoConfig(), cache_dir=tmp_path, client=http,
            )

    field = _run(go())
    assert field.n_points == 2
    pt = field.at(times[0], 25.0, -80.0)
    assert pt is not None
    assert pt.wind_speed_kn == pytest.approx(12.0)
    assert pt.current_kn == pytest.approx(1.5)


def test_fetch_field_caches_and_skips_the_second_fetch(tmp_path: Path) -> None:
    grid = [(25.0, -80.0)]
    times = [T0]
    calls: list[int] = []

    async def go(client):
        return await fetch_field(
            grid, times, "test", config=OpenMeteoConfig(), cache_dir=tmp_path, client=client,
        )

    async def first():
        transport = httpx.MockTransport(_counting_handler(calls))
        async with httpx.AsyncClient(transport=transport) as http:
            return await go(http)

    async def second():
        # A transport that errors on any call - proves the cache was used, not the network.
        def boom(request: httpx.Request) -> httpx.Response:
            raise AssertionError("should not hit the network on a cache hit")

        async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as http:
            return await go(http)

    field1 = _run(first())
    assert calls  # the first call really did hit the (mock) network
    field2 = _run(second())
    assert field1.n_points == field2.n_points == 1


def test_fetch_field_rejects_empty_grid_or_times() -> None:
    async def go():
        await fetch_field([], [T0], "test", cache_dir=None)

    with pytest.raises(RoutingError):
        _run(go())

    async def go2():
        await fetch_field([(25.0, -80.0)], [], "test", cache_dir=None)

    with pytest.raises(RoutingError):
        _run(go2())
