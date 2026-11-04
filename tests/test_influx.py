"""Tests for passage_planner.influx (fake query runner — no server)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from passage_planner.config import InfluxConfig
from passage_planner.errors import InfluxAuthError, InfluxError, InfluxUnavailableError
from passage_planner.influx import (
    InfluxReader,
    Sample,
    _as_sample,
    _translate,
    flux_latest,
    flux_measurements,
    flux_polar_baseline,
    flux_series,
)

CFG = InfluxConfig(
    url="http://192.168.1.10:8086",
    org="your-org",
    bucket="boat",
    token_env="HYLAS_INFLUX_TOKEN",
    write_timeout_s=5.0,
    local_buffer_path="var/b.jsonl",
    enabled=False,
    read_timeout_s=10.0,
)
T0 = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


# -- Flux builders -----------------------------------------------------


def test_flux_latest_bounds_and_filters() -> None:
    q = flux_latest("boat", "navigation.speedOverGround", "value", timedelta(hours=1), None)
    assert 'from(bucket: "boat")' in q
    assert "range(start: -3600s)" in q
    assert 'r._measurement == "navigation.speedOverGround"' in q
    assert 'r._field == "value"' in q
    assert "|> last()" in q


def test_flux_latest_adds_source_filter() -> None:
    q = flux_latest(
        "boat", "navigation.position.latitude", "value", timedelta(minutes=15), "n2k-can0.2"
    )
    assert 'r["$source"] == "n2k-can0.2"' in q


def test_flux_series_aggregates_when_every_given() -> None:
    q = flux_series(
        "boat", "environment.wind.speedTrue", "value", T0, None,
        timedelta(minutes=30), "mean", None,
    )
    assert "range(start: 2026-10-15T12:00:00Z)" in q
    assert "aggregateWindow(every: 1800s, fn: mean" in q


def test_flux_series_no_aggregate_without_every() -> None:
    q = flux_series("boat", "x", "value", T0, T0 + timedelta(hours=6), None, "mean", None)
    assert "aggregateWindow" not in q
    assert "stop: 2026-10-15T18:00:00Z" in q


def test_flux_measurements_uses_schema_package() -> None:
    q = flux_measurements("boat", timedelta(days=7))
    assert 'import "influxdata/influxdb/schema"' in q
    assert 'schema.measurements(bucket: "boat", start: -604800s)' in q


def test_flux_polar_baseline_filters_measurement_and_field() -> None:
    q = flux_polar_baseline("boat", "polar_baseline", "boat_speed_kn", timedelta(days=3650))
    assert 'from(bucket: "boat")' in q
    assert 'r._measurement == "polar_baseline"' in q
    assert 'r._field == "boat_speed_kn"' in q
    assert '"twa_deg"' in q and '"tws_kn"' in q


# -- Reader with a fake runner --------------------------------------


class _Fake:
    """Return canned rows keyed by a substring of the Flux query."""

    def __init__(self, routes: dict[str, list[dict]] | None = None) -> None:
        self.routes = routes or {}
        self.queries: list[str] = []
        self.raise_with: Exception | None = None

    def __call__(self, flux: str) -> list[dict]:
        self.queries.append(flux)
        if self.raise_with is not None:
            raise self.raise_with
        for key, rows in self.routes.items():
            if key in flux:
                return rows
        return []


def _reader(fake: _Fake) -> InfluxReader:
    return InfluxReader(CFG, query_runner=fake)


def test_latest_returns_newest_sample() -> None:
    fake = _Fake({'"navigation.speedOverGround"': [
        {"_time": T0, "_value": 6.1},
        {"_time": T0 + timedelta(minutes=1), "_value": 6.4},
    ]})
    s = _reader(fake).latest("navigation.speedOverGround")
    assert s == Sample(time=T0 + timedelta(minutes=1), value=6.4)


def test_latest_none_when_empty() -> None:
    assert _reader(_Fake()).latest("nothing.here") is None


def test_series_sorted_and_filtered() -> None:
    fake = _Fake({'"environment.wind.speedTrue"': [
        {"_time": T0 + timedelta(minutes=2), "_value": 12.0},
        {"_time": T0, "_value": 11.0},
        {"_time": T0 + timedelta(minutes=1), "_value": None},  # dropped
    ]})
    out = _reader(fake).series("environment.wind.speedTrue", T0)
    assert [s.value for s in out] == [11.0, 12.0]


def test_series_requires_aware_start() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _reader(_Fake()).series("x", datetime(2026, 10, 15, 12, 0))


def test_latest_fix_joins_lat_and_lon() -> None:
    fake = _Fake({
        '"navigation.position.latitude"': [{"_time": T0, "_value": 29.95}],
        '"navigation.position.longitude"': [{"_time": T0, "_value": -90.07}],
    })
    fix = _reader(fake).latest_fix()
    assert fix is not None
    assert (fix.lat, fix.lon) == (29.95, -90.07)


def test_latest_fix_none_if_one_axis_missing() -> None:
    fake = _Fake({'"navigation.position.latitude"': [{"_time": T0, "_value": 29.95}]})
    assert _reader(fake).latest_fix() is None


def test_measurements_dedupes_and_sorts() -> None:
    fake = _Fake({"schema.measurements": [
        {"_value": "ssm_battery"}, {"_value": "navigation.position.latitude"},
        {"_value": "ssm_battery"},
    ]})
    assert _reader(fake).measurements() == ["navigation.position.latitude", "ssm_battery"]


def test_polar_baseline_builds_grid_from_tagged_rows() -> None:
    fake = _Fake({'"polar_baseline"': [
        {"_time": T0, "_value": 6.9, "twa_deg": "110", "tws_kn": "10"},
        {"_time": T0, "_value": 7.5, "twa_deg": "110", "tws_kn": "12"},
        {"_time": T0, "_value": 6.3, "twa_deg": "60", "tws_kn": "10"},
        {"_time": T0, "_value": 6.8, "twa_deg": "60", "tws_kn": "12"},
    ]})
    polar = _reader(fake).polar_baseline()
    assert polar is not None
    assert polar.twa_axis == (60.0, 110.0)
    assert polar.tws_axis == (10.0, 12.0)
    assert polar.speed(10.0, 60.0) == 6.3
    assert polar.provisional is False
    assert polar.source == "influxdb:boat/polar_baseline"


def test_polar_baseline_keeps_latest_sample_per_cell() -> None:
    fake = _Fake({'"polar_baseline"': [
        {"_time": T0, "_value": 6.0, "twa_deg": "60", "tws_kn": "10"},
        {"_time": T0 + timedelta(days=1), "_value": 6.5, "twa_deg": "60", "tws_kn": "10"},
    ]})
    polar = _reader(fake).polar_baseline()
    assert polar is not None
    assert polar.speed(10.0, 60.0) == 6.5


def test_polar_baseline_none_when_no_rows() -> None:
    assert _reader(_Fake()).polar_baseline() is None


def test_polar_baseline_ignores_rows_missing_tags_or_non_numeric() -> None:
    fake = _Fake({'"polar_baseline"': [
        {"_time": T0, "_value": 6.0, "twa_deg": "60", "tws_kn": "10"},
        {"_time": T0, "_value": 6.5, "twa_deg": None, "tws_kn": "10"},
        {"_time": T0, "_value": "n/a", "twa_deg": "60", "tws_kn": "12"},
        {"_time": "not-a-time", "_value": 6.5, "twa_deg": "60", "tws_kn": "12"},
    ]})
    polar = _reader(fake).polar_baseline()
    assert polar is not None
    assert polar.twa_axis == (60.0,)
    assert polar.tws_axis == (10.0,)


def test_ping_true_on_success_false_on_unavailable() -> None:
    assert _reader(_Fake({"schema.measurements": [{"_value": "x"}]})).ping() is True
    fake = _Fake()
    fake.raise_with = InfluxUnavailableError("down")
    assert _reader(fake).ping() is False


# -- helpers ---------------------------------------------------------


def test_as_sample_rejects_non_numeric_and_bool() -> None:
    assert _as_sample({"_time": T0, "_value": "3D"}) is None
    assert _as_sample({"_time": T0, "_value": True}) is None
    assert _as_sample({"_time": "not-a-time", "_value": 1.0}) is None
    assert _as_sample({"_time": T0, "_value": 4}) == Sample(T0, 4.0)


def test_translate_maps_status_and_transport() -> None:
    class _Api(Exception):
        status = 403

    assert isinstance(_translate(_Api()), InfluxAuthError)
    assert isinstance(_translate(ConnectionError("refused")), InfluxUnavailableError)
    assert isinstance(_translate(TimeoutError()), InfluxUnavailableError)
    assert isinstance(_translate(ValueError("weird")), InfluxError)
