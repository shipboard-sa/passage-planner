"""Tests for passage_planner.weather.openmeteo using a mocked HTTP transport."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from passage_planner.errors import WeatherFetchError, WeatherParseError
from passage_planner.passage import TimedFix
from passage_planner.weather import OpenMeteoClient, OpenMeteoConfig
from passage_planner.weather.openmeteo import _interp

# Anchor the mocked forecast series to the start of tomorrow (UTC) so it is
# always inside Open-Meteo's +16 day horizon regardless of when tests run.
_ANCHOR = (datetime.now(UTC) + timedelta(days=1)).replace(
    hour=0, minute=0, second=0, microsecond=0
)


def _hour_strings(n: int) -> list[str]:
    return [(_ANCHOR + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(n)]


def _forecast_block(nhours: int, wind: float) -> dict[str, object]:
    return {
        "latitude": 30.0,
        "longitude": -89.0,
        "hourly": {
            "time": _hour_strings(nhours),
            "wind_speed_10m": [wind] * nhours,
            "wind_direction_10m": [180.0] * nhours,
            "wind_gusts_10m": [wind + 5.0] * nhours,
            "pressure_msl": [1015.0] * nhours,
        },
    }


def _marine_block(nhours: int, hs: float, *, current: float = 1.4) -> dict[str, object]:
    return {
        "latitude": 30.0,
        "longitude": -89.0,
        "hourly": {
            "time": _hour_strings(nhours),
            "wave_height": [hs] * nhours,
            "wave_direction": [90.0] * nhours,
            "wave_period": [6.0] * nhours,
            "swell_wave_height": [hs * 0.6] * nhours,
            "ocean_current_velocity": [current] * nhours,
            "ocean_current_direction": [40.0] * nhours,
        },
    }


def _fixes(n: int) -> list[TimedFix]:
    return [
        TimedFix(
            cumulative_nm=float(i * 10),
            lat=30.0 + i * 0.1,
            lon=-89.0 - i * 0.1,
            eta=_ANCHOR + timedelta(hours=3 + i, minutes=30),
            leg_index=i,
        )
        for i in range(n)
    ]


def _handler(nhours: int = 24, wind: float = 15.0, hs: float = 1.2):
    def handle(request: httpx.Request) -> httpx.Response:
        n = len(request.url.params["latitude"].split(","))
        if "marine" in request.url.host:
            body = [_marine_block(nhours, hs) for _ in range(n)]
        else:
            body = [_forecast_block(nhours, wind) for _ in range(n)]
        return httpx.Response(200, json=body)

    return handle


def _run(fixes: list[TimedFix], handler, config: OpenMeteoConfig | None = None):
    client = OpenMeteoClient(config=config or OpenMeteoConfig())

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await client.fetch(fixes, "test route", client=http)

    return asyncio.run(go())


def test_fetch_merges_forecast_and_marine() -> None:
    fixes = _fixes(3)
    rf = _run(fixes, _handler(wind=15.0, hs=1.2))

    assert len(rf.points) == 3
    assert rf.route_name == "test route"
    for fix, pt in zip(fixes, rf.points, strict=True):
        assert pt.cumulative_nm == fix.cumulative_nm
        assert pt.leg_index == fix.leg_index
        assert pt.wind_speed_kn == pytest.approx(15.0)
        assert pt.wind_gust_kn == pytest.approx(20.0)
        assert pt.wave_hs_m == pytest.approx(1.2)
        assert pt.swell_hs_m == pytest.approx(0.72)
        assert pt.current_kn == pytest.approx(1.4)
        assert pt.current_to_deg == pytest.approx(40.0)
        assert pt.has_wind and pt.has_sea_state and pt.has_current
        assert pt.source == "open-meteo"


def test_marine_params_send_wind_speed_unit_kn_for_current() -> None:
    # ocean_current_velocity is a speed field; wind_speed_unit=kn (already sent
    # for the forecast call) converts it too - verified live 2026-09-22 against
    # the real API. No separate unit-conversion code should be needed.
    client = OpenMeteoClient()
    params = client._marine_params(_fixes(1))
    assert params["wind_speed_unit"] == "kn"
    assert "ocean_current_velocity" in params["hourly"]
    assert "ocean_current_direction" in params["hourly"]


def test_chunking_covers_all_fixes() -> None:
    fixes = _fixes(10)
    rf = _run(fixes, _handler(), OpenMeteoConfig(max_points_per_request=3))
    assert len(rf.points) == 10
    assert [p.cumulative_nm for p in rf.points] == [float(i * 10) for i in range(10)]


def test_http_error_raises_weather_fetch_error() -> None:
    def bad(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"reason": "invalid coordinate"})

    with pytest.raises(WeatherFetchError, match="invalid coordinate"):
        _run(_fixes(2), bad)


def test_block_count_mismatch_raises_parse_error() -> None:
    def short(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_forecast_block(24, 12.0)])  # only 1 block

    with pytest.raises(WeatherParseError, match="expected 2 location blocks"):
        _run(_fixes(2), short)


def test_horizon_guard() -> None:
    far = [
        TimedFix(
            cumulative_nm=0.0,
            lat=30.0,
            lon=-89.0,
            eta=datetime.now(UTC).replace(microsecond=0) + timedelta(days=40),
            leg_index=0,
        )
    ]
    with pytest.raises(WeatherFetchError, match="16 days"):
        _run(far, _handler())


# -- interpolation unit tests ------------------------------------------


def _t(h: int) -> datetime:
    return datetime(2026, 10, 15, h, 0, tzinfo=UTC)


_HALF = datetime(2026, 10, 15, 0, 30, tzinfo=UTC)


def test_interp_linear_midpoint() -> None:
    assert _interp([_t(0), _t(1)], [10.0, 20.0], _HALF) == pytest.approx(15.0)


def test_interp_clamps_outside_range() -> None:
    assert _interp([_t(2), _t(3)], [5.0, 7.0], _t(0)) == pytest.approx(5.0)
    assert _interp([_t(2), _t(3)], [5.0, 7.0], _t(9)) == pytest.approx(7.0)


def test_interp_none_bracket_returns_none() -> None:
    assert _interp([_t(0), _t(1)], [None, 20.0], _HALF) is None


def test_interp_empty_returns_none() -> None:
    assert _interp([], [], _t(0)) is None
