"""Tests for passage_planner.weather.compare."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from passage_planner.passage import TimedFix
from passage_planner.weather.compare import (
    _circular_delta,
    compare_route_forecasts,
)
from passage_planner.weather.model import ForecastPoint, RouteForecast
from passage_planner.weather.openmeteo import OpenMeteoClient

_T = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def _rf(model: str, winds: list[float], dirs: list[float]) -> RouteForecast:
    pts = tuple(
        ForecastPoint(
            cumulative_nm=float(i * 10), leg_index=i, lat=25.0, lon=-79.0,
            valid_time=_T, source="open-meteo", model=model, provenance_tier="secondary",
            wind_speed_kn=w, wind_from_deg=d,
        )
        for i, (w, d) in enumerate(zip(winds, dirs, strict=True))
    )
    return RouteForecast(route_name="r", issued=_T, points=pts)


def test_circular_delta() -> None:
    assert _circular_delta(10.0, 350.0) == pytest.approx(20.0)
    assert _circular_delta(90.0, 90.0) == 0.0
    assert _circular_delta(0.0, 180.0) == pytest.approx(180.0)


def test_high_confidence_when_models_agree() -> None:
    a = _rf("gfs", [12, 14, 13], [90, 95, 100])
    b = _rf("ecmwf", [13, 13, 14], [92, 98, 103])
    c = compare_route_forecasts(a, b)
    assert c.confidence == "high"
    assert c.agree is True
    assert c.max_wind_delta_kn <= 5.0


def test_low_confidence_on_big_split() -> None:
    a = _rf("gfs", [12, 12, 12], [90, 90, 90])
    b = _rf("ecmwf", [12, 25, 12], [90, 90, 160])  # leg 1 wind, leg 2 dir blow up
    c = compare_route_forecasts(a, b)
    assert c.confidence == "low"
    assert c.worst_leg is not None
    assert c.worst_leg.leg_index in (1, 2)


def test_moderate_confidence_in_between() -> None:
    a = _rf("gfs", [10, 10, 10], [100, 100, 100])
    b = _rf("ecmwf", [10, 17, 10], [100, 130, 100])
    c = compare_route_forecasts(a, b)
    assert c.confidence == "moderate"


def test_fetch_wind_models_parses_suffixed_keys() -> None:
    fixes = [
        TimedFix(cumulative_nm=0.0, lat=25.0, lon=-79.0,
                 eta=(datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0),
                 leg_index=0),
    ]
    hours = [(fixes[0].eta.replace(minute=0) + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M")
             for h in range(24)]

    def handler(request: httpx.Request) -> httpx.Response:
        block = {
            "hourly": {
                "time": hours,
                "wind_speed_10m_gfs_seamless": [12.0] * 24,
                "wind_direction_10m_gfs_seamless": [90.0] * 24,
                "wind_speed_10m_ecmwf_ifs025": [18.0] * 24,
                "wind_direction_10m_ecmwf_ifs025": [110.0] * 24,
            }
        }
        return httpx.Response(200, json=[block])

    client = OpenMeteoClient()

    async def go() -> dict[str, RouteForecast]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await client.fetch_wind_models(
                fixes, "r", ("gfs_seamless", "ecmwf_ifs025"), client=http
            )

    out = asyncio.run(go())
    assert out["gfs_seamless"].points[0].wind_speed_kn == pytest.approx(12.0)
    assert out["ecmwf_ifs025"].points[0].wind_from_deg == pytest.approx(110.0)
    cmp = compare_route_forecasts(out["gfs_seamless"], out["ecmwf_ifs025"])
    assert cmp.max_wind_delta_kn == pytest.approx(6.0)
    assert cmp.confidence == "moderate"
