"""Tests for the CLI wiring (forecast fetches are stubbed)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from passage_planner import cli, service
from passage_planner.errors import InfluxError
from passage_planner.polar import Polar, load_polar
from passage_planner.weather.model import ForecastPoint, RouteForecast

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "nola_to_gulfport.gpx"
ROOT = Path(__file__).resolve().parents[1]


def _fake_forecast(fixes, route_name: str) -> RouteForecast:
    pts = tuple(
        ForecastPoint(
            cumulative_nm=f.cumulative_nm, leg_index=f.leg_index, lat=f.lat, lon=f.lon,
            valid_time=f.eta, source="test", model="stub", provenance_tier="secondary",
            wind_speed_kn=12.0, wind_from_deg=150.0, wind_gust_kn=16.0, pressure_hpa=1015.0,
            wave_hs_m=0.7, wave_from_deg=150.0, wave_period_s=6.0, swell_hs_m=0.3,
        )
        for f in fixes
    )
    return RouteForecast(route_name=route_name, issued=datetime.now(UTC), points=pts)


def test_brief_writes_three_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "fetch_route_forecast", _fake_forecast)
    monkeypatch.chdir(ROOT)  # so config/ and config/baseline.pol resolve

    depart = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT12:00Z")
    rc = cli.main([
        "brief", str(FIXTURE), "--depart", depart, "--to", "bahamas",
        "--crew", "skipper,mate", "--no-tropical", "--no-tides", "--no-maintenance",
        "--no-papers", "--no-notices", "--out-dir", str(tmp_path), "--stem", "t",
    ])
    assert rc == 0
    for ext in ("md", "html", "docx"):
        assert (tmp_path / f"t.{ext}").exists()
    md = (tmp_path / "t.md").read_text(encoding="utf-8")
    assert "# Passage Briefing" in md
    assert "Click2Clear" in md  # Bahamas CIQ
    assert "SV Example Buddy" in md  # buddy boat from shipped config
    assert "CDT" in md  # times are boat-local, not UTC


def test_brief_wires_nga_notices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from passage_planner.notices import NavWarning, NoticePosition, NoticeReport

    monkeypatch.setattr(service, "fetch_route_forecast", _fake_forecast)
    monkeypatch.chdir(ROOT)

    def _fake_notices(route, **kw):
        w = NavWarning(
            area="NAVAREA IV", number=7, year=2026, subregion="28",
            issued=datetime(2026, 9, 1, tzinfo=UTC), authority="NGA",
            text="GULF OF MEXICO.\nLIGHT 30-12.0N 089-00.0W UNLIT.",
            positions=(NoticePosition(30.2, -89.0),), closest_nm=4.0,
        )
        return NoticeReport(
            route_name=route.name, generated=datetime.now(UTC), corridor_nm=75.0,
            areas_checked=("NAVAREA IV", "HYDROLANT"), warnings=(w,), total_in_force=5,
        )

    monkeypatch.setattr(service, "fetch_nav_warnings_sync", _fake_notices)
    depart = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT12:00Z")
    rc = cli.main([
        "brief", str(FIXTURE), "--depart", depart, "--no-tropical", "--no-tides",
        "--no-maintenance", "--no-papers", "--out-dir", str(tmp_path), "--stem", "n",
    ])
    assert rc == 0
    md = (tmp_path / "n.md").read_text(encoding="utf-8")
    assert "5d. Navigational warnings" in md
    assert "NAVAREA IV 7/26" in md


def test_brief_wires_parker_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "fetch_route_forecast", _fake_forecast)
    monkeypatch.chdir(ROOT)

    parker_file = tmp_path / "parker.txt"
    parker_file.write_text(
        "Wx Update, E Caribbean, Mon15, 8am\n\n"
        "WIND:\nTrinidad-StVincent: E-SE@12-20g24k/3-5' today.\n",
        encoding="utf-8",
    )
    depart = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT12:00Z")
    rc = cli.main([
        "brief", str(FIXTURE), "--depart", depart, "--no-tropical", "--no-tides",
        "--no-maintenance", "--no-papers", "--out-dir", str(tmp_path), "--stem", "p",
        "--parker-file", str(parker_file),
    ])
    assert rc == 0
    md = (tmp_path / "p.md").read_text(encoding="utf-8")
    assert "3d. Professional forecast" in md
    assert "[Professional Forecast: Marine Weather Center]" in md


def test_brief_omits_parker_section_without_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "fetch_route_forecast", _fake_forecast)
    monkeypatch.chdir(ROOT)
    depart = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT12:00Z")
    rc = cli.main([
        "brief", str(FIXTURE), "--depart", depart, "--no-tropical", "--no-tides",
        "--no-maintenance", "--no-papers", "--out-dir", str(tmp_path), "--stem", "q",
    ])
    assert rc == 0
    md = (tmp_path / "q.md").read_text(encoding="utf-8")
    assert "3d. Professional forecast" not in md


def test_bad_depart_is_rejected() -> None:
    with pytest.raises(SystemExit):
        cli.main(["brief", str(FIXTURE), "--depart", "not-a-date"])


def test_missing_route_returns_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)
    depart = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT12:00Z")
    rc = cli.main(["brief", str(tmp_path / "nope.gpx"), "--depart", depart, "--no-tropical"])
    assert rc == 1


# -- export-polar --------------------------------------------------------


_DERIVED = Polar(
    twa_axis=(60.0, 110.0), tws_axis=(10.0, 12.0),
    grid=((6.3, 6.8), (6.9, 7.5)),
    source="influxdb:boat/polar_baseline", provisional=False,
)


def test_export_polar_writes_a_loadable_pol_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(cli, "export_polar", lambda app: _DERIVED)
    out = tmp_path / "exported.pol"
    rc = cli.main(["export-polar", "--out", str(out)])
    assert rc == 0
    reloaded = load_polar(out)
    assert reloaded.twa_axis == _DERIVED.twa_axis
    assert reloaded.speed(12.0, 110.0) == pytest.approx(7.5)


def test_export_polar_creates_missing_output_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(cli, "export_polar", lambda app: _DERIVED)
    out = tmp_path / "nested" / "polar_baseline.pol"
    rc = cli.main(["export-polar", "--out", str(out)])
    assert rc == 0
    assert out.exists()


def test_export_polar_reports_influx_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)

    def _raise(app):
        raise InfluxError("polar_baseline has no rows yet in InfluxDB")

    monkeypatch.setattr(cli, "export_polar", _raise)
    rc = cli.main(["export-polar", "--out", str(tmp_path / "x.pol")])
    assert rc == 1
    assert not (tmp_path / "x.pol").exists()


# -- solve (weather-routing step 4, EXPERIMENTAL) ----------------------------


async def _fake_fetch_field(grid, times, route_name, *, config=None, cache_dir=None, client=None):
    from passage_planner.weather.field import Field
    from passage_planner.weather.model import ForecastPoint

    points = {
        cell: (ForecastPoint(
            cumulative_nm=0.0, leg_index=0, lat=cell[0], lon=cell[1], valid_time=times[0],
            source="test", model="stub", provenance_tier="secondary",
            wind_speed_kn=12.0, wind_from_deg=150.0,
        ),)
        for cell in grid
    }
    return Field(by_point=points)


def test_solve_writes_gpx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(cli, "fetch_field", _fake_fetch_field)
    depart = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT12:00Z")
    out = tmp_path / "solved.gpx"
    rc = cli.main([
        "solve", str(FIXTURE), "--depart", depart, "--out", str(out),
        "--along-step-nm", "1000", "--cross-offsets-nm", "0",
    ])
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    assert text.startswith("<?xml")
    assert "<rte>" in text


def test_solve_reports_field_fetch_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from passage_planner.errors import WeatherFetchError

    monkeypatch.chdir(ROOT)

    async def _raise(*a, **k):
        raise WeatherFetchError("rate limited")

    monkeypatch.setattr(cli, "fetch_field", _raise)
    depart = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT12:00Z")
    rc = cli.main(["solve", str(FIXTURE), "--depart", depart])
    assert rc == 1


# -- sweep (weather-routing step 5, EXPERIMENTAL) -----------------------------


def test_sweep_writes_markdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from passage_planner.sweep import SweepResult, SweepRow

    async def _fake_sweep(vessel, polar, route, start, *, window_days, step_hours, models, **k):
        row = SweepRow(
            depart=start, model=models[0], eta=start + timedelta(hours=10),
            duration_h=10.0, total_nm=100.0, fuel_gal=20.0, fuel_shortfall=False,
            peak_wind_kn=15.0, sailing_fraction=0.5, warning_count=0,
        )
        return SweepResult(
            route_name=route.name, window_start=start, window_end=start, rows=(row,),
        )

    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(cli, "sweep_departures", _fake_sweep)
    depart = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT12:00Z")
    out = tmp_path / "sweep.md"
    rc = cli.main(["sweep", str(FIXTURE), "--depart", depart, "--out", str(out)])
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    assert "Departure-window sweep" in text


def test_sweep_rejects_empty_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)
    depart = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT12:00Z")
    with pytest.raises(SystemExit):
        cli.main(["sweep", str(FIXTURE), "--depart", depart, "--models", " , "])
