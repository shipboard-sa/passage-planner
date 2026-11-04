"""Tests for passage_planner.sweep (weather-routing step 5)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from passage_planner import load, load_polar, parse_gpx
from passage_planner.sweep import (
    SweepFailure,
    SweepResult,
    SweepRow,
    candidate_departures,
    render_sweep_markdown,
    sweep_departures,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
_ANCHOR = (datetime.now(UTC) + timedelta(days=1)).replace(
    hour=0, minute=0, second=0, microsecond=0
)


def _cfg():
    return load(ROOT / "config").vessel


def _polar():
    return load_polar(ROOT / "config" / "baseline.pol")


def _route():
    return parse_gpx(FIXTURES / "nola_to_gulfport.gpx")


def _hour_strings(n: int) -> list[str]:
    return [(_ANCHOR + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(n)]


def _forecast_block(nhours: int, wind: float) -> dict[str, object]:
    return {
        "latitude": 30.0, "longitude": -89.0,
        "hourly": {
            "time": _hour_strings(nhours),
            "wind_speed_10m": [wind] * nhours, "wind_direction_10m": [170.0] * nhours,
            "wind_gusts_10m": [wind + 5.0] * nhours, "pressure_msl": [1015.0] * nhours,
        },
    }


def _marine_block(nhours: int, hs: float = 0.8) -> dict[str, object]:
    return {
        "latitude": 30.0, "longitude": -89.0,
        "hourly": {
            "time": _hour_strings(nhours),
            "wave_height": [hs] * nhours, "wave_direction": [170.0] * nhours,
            "wave_period": [6.0] * nhours, "swell_wave_height": [hs * 0.5] * nhours,
            "ocean_current_velocity": [0.0] * nhours, "ocean_current_direction": [0.0] * nhours,
        },
    }


def _handler(*, wind: float = 14.0, nhours: int = 72, fail_marine: bool = False):
    def handle(request: httpx.Request) -> httpx.Response:
        n = len(request.url.params["latitude"].split(","))
        if "marine" in request.url.host:
            if fail_marine:
                return httpx.Response(429, json={"reason": "rate limited"})
            body = [_marine_block(nhours) for _ in range(n)]
        else:
            body = [_forecast_block(nhours, wind) for _ in range(n)]
        return httpx.Response(200, json=body)

    return handle


def _run(coro):
    return asyncio.run(coro)


# -- candidate_departures -------------------------------------------------


def test_candidate_departures_spans_the_window() -> None:
    times = candidate_departures(_ANCHOR, window_days=1.0, step_hours=6.0)
    assert times[0] == _ANCHOR
    assert times[-1] == _ANCHOR + timedelta(days=1)
    assert len(times) == 5


def test_candidate_departures_rejects_bad_params() -> None:
    with pytest.raises(ValueError):
        candidate_departures(_ANCHOR, window_days=0.0)
    with pytest.raises(ValueError):
        candidate_departures(_ANCHOR, step_hours=0.0)


# -- sweep_departures (mocked HTTP) ---------------------------------------


def test_sweep_departures_builds_a_row_per_candidate_and_model() -> None:
    async def go():
        transport = httpx.MockTransport(_handler(wind=14.0))
        async with httpx.AsyncClient(transport=transport) as http:
            return await sweep_departures(
                _cfg(), _polar(), _route(), _ANCHOR,
                window_days=0.5, step_hours=6.0, models=("gfs_seamless",), client=http,
            )

    result = _run(go())
    assert not result.failures
    assert len(result.rows) == 3  # 0h, 6h, 12h
    for row in result.rows:
        assert row.model == "gfs_seamless"
        assert row.eta > row.depart
        assert row.duration_h > 0.0
        assert row.total_nm > 0.0
        assert row.sailing_fraction >= 0.0


def test_sweep_departures_two_models_doubles_the_rows() -> None:
    async def go():
        transport = httpx.MockTransport(_handler(wind=14.0))
        async with httpx.AsyncClient(transport=transport) as http:
            return await sweep_departures(
                _cfg(), _polar(), _route(), _ANCHOR,
                window_days=0.01, step_hours=6.0, models=("gfs_seamless", "ecmwf_ifs025"),
                client=http,
            )

    result = _run(go())
    assert len(result.rows) == 2
    assert {r.model for r in result.rows} == {"gfs_seamless", "ecmwf_ifs025"}


def test_sweep_departures_records_failures_without_aborting() -> None:
    async def go():
        transport = httpx.MockTransport(_handler(wind=14.0, fail_marine=True))
        async with httpx.AsyncClient(transport=transport) as http:
            return await sweep_departures(
                _cfg(), _polar(), _route(), _ANCHOR,
                window_days=0.01, step_hours=6.0, models=("gfs_seamless",), client=http,
            )

    result = _run(go())
    assert not result.rows
    assert len(result.failures) == 1
    assert result.failures[0].model == "gfs_seamless"


def test_sweep_departures_rejects_empty_models() -> None:
    async def go():
        await sweep_departures(
            _cfg(), _polar(), _route(), _ANCHOR, window_days=0.0, models=(),
        )

    with pytest.raises(ValueError):
        _run(go())


# -- SweepResult.best -------------------------------------------------------


def _row(depart, model, *, duration=10.0, fuel=20.0, shortfall=False, peak_wind=15.0):
    return SweepRow(
        depart=depart, model=model, eta=depart + timedelta(hours=duration),
        duration_h=duration, total_nm=100.0, fuel_gal=fuel, fuel_shortfall=shortfall,
        peak_wind_kn=peak_wind, sailing_fraction=0.5, warning_count=0,
    )


def test_best_prefers_lower_duration() -> None:
    slow = _row(_ANCHOR, "gfs_seamless", duration=12.0)
    fast = _row(_ANCHOR + timedelta(hours=6), "gfs_seamless", duration=8.0)
    result = SweepResult(
        route_name="r", window_start=_ANCHOR, window_end=_ANCHOR, rows=(slow, fast),
    )
    assert result.best() is fast


def test_best_excludes_fuel_shortfall_when_an_alternative_exists() -> None:
    shortfall = _row(_ANCHOR, "gfs_seamless", duration=6.0, shortfall=True)
    safe = _row(_ANCHOR + timedelta(hours=6), "gfs_seamless", duration=9.0, shortfall=False)
    result = SweepResult(
        route_name="r", window_start=_ANCHOR, window_end=_ANCHOR, rows=(shortfall, safe),
    )
    assert result.best() is safe


def test_best_falls_back_to_shortfall_rows_if_that_is_all_there_is() -> None:
    only = _row(_ANCHOR, "gfs_seamless", duration=6.0, shortfall=True)
    result = SweepResult(
        route_name="r", window_start=_ANCHOR, window_end=_ANCHOR, rows=(only,),
    )
    assert result.best() is only


def test_best_returns_none_when_there_are_no_rows() -> None:
    result = SweepResult(route_name="r", window_start=_ANCHOR, window_end=_ANCHOR, rows=())
    assert result.best() is None


def test_best_prefer_field_is_configurable() -> None:
    windy = _row(_ANCHOR, "gfs_seamless", duration=8.0, peak_wind=30.0)
    calm = _row(_ANCHOR + timedelta(hours=6), "gfs_seamless", duration=9.0, peak_wind=12.0)
    result = SweepResult(
        route_name="r", window_start=_ANCHOR, window_end=_ANCHOR, rows=(windy, calm),
    )
    assert result.best(prefer="peak_wind_kn") is calm


# -- render_sweep_markdown --------------------------------------------------


def test_render_sweep_markdown_flags_the_best_row() -> None:
    slow = _row(_ANCHOR, "gfs_seamless", duration=12.0)
    fast = _row(_ANCHOR + timedelta(hours=6), "gfs_seamless", duration=8.0)
    result = SweepResult(
        route_name="FLD to T&C", window_start=_ANCHOR, window_end=_ANCHOR, rows=(slow, fast),
    )
    md = render_sweep_markdown(result)
    assert "FLD to T&C" in md
    assert "2 solved, 0 failed" in md
    lines = [ln for ln in md.splitlines() if ln.startswith("| ")]
    assert len(lines) == 3  # header + 2 rows
    fast_line = next(ln for ln in lines if "8.0 h" in ln)
    assert "**best**" in fast_line
    slow_line = next(ln for ln in lines if "12.0 h" in ln)
    assert "**best**" not in slow_line


def test_render_sweep_markdown_lists_failures() -> None:
    result = SweepResult(
        route_name="r", window_start=_ANCHOR, window_end=_ANCHOR, rows=(),
        failures=(SweepFailure(depart=_ANCHOR, model="gfs_seamless", error="HTTP 429"),),
    )
    md = render_sweep_markdown(result)
    assert "1 failed" in md
    assert "HTTP 429" in md
