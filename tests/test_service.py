"""Tests for passage_planner.service.resolve_polar (the derived/baseline switch),
resolve_buddy_polars (a fleet boat's own real polar, when one is on file), and
export_polar (the export-polar CLI command's fetch, no soft fallback)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from passage_planner import fleet, service
from passage_planner.config import load
from passage_planner.errors import InfluxError, InfluxUnavailableError, MissingSecretError
from passage_planner.polar import Polar, write_pol

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def _app(*, polar_source: str, influx_enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        vessel=SimpleNamespace(
            performance=SimpleNamespace(
                polar_source=polar_source, polar_file="config/baseline.pol"
            ),
            infra=SimpleNamespace(influx=SimpleNamespace(enabled=influx_enabled)),
        )
    )


_DERIVED = Polar(
    twa_axis=(60.0,), tws_axis=(10.0,), grid=((6.3,),),
    source="influxdb:boat/polar_baseline", provisional=False,
)


class _FakeReader:
    """Stand-in for InfluxReader — context-manager, canned polar_baseline()."""

    def __init__(self, result: Polar | None = None, raises: Exception | None = None) -> None:
        self._result = result
        self._raises = raises

    @classmethod
    def from_app_config(cls, app, *, query_runner=None):
        return cls._instance

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def polar_baseline(self) -> Polar | None:
        if self._raises is not None:
            raise self._raises
        return self._result


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeReader) -> None:
    _FakeReader._instance = fake  # type: ignore[attr-defined]
    monkeypatch.setattr(service, "InfluxReader", _FakeReader)


def test_baseline_source_never_touches_influx(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _FakeReader(raises=RuntimeError("should not be called")))
    app = _app(polar_source="baseline", influx_enabled=True)
    warnings: list[str] = []
    pol = service.resolve_polar(app, CONFIG_DIR, warnings.append)
    assert pol.provisional is True  # the shipped config/baseline.pol
    assert warnings == []


def test_derived_but_influx_disabled_falls_back_quietly(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _FakeReader(raises=RuntimeError("should not be called")))
    app = _app(polar_source="derived", influx_enabled=False)
    warnings: list[str] = []
    pol = service.resolve_polar(app, CONFIG_DIR, warnings.append)
    assert pol.provisional is True
    assert warnings == []  # disabled is expected steady-state, not a failure


def test_derived_and_reachable_uses_influx_polar(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _FakeReader(result=_DERIVED))
    app = _app(polar_source="derived", influx_enabled=True)
    warnings: list[str] = []
    pol = service.resolve_polar(app, CONFIG_DIR, warnings.append)
    assert pol is _DERIVED
    assert warnings == []


def test_derived_but_no_data_yet_falls_back_with_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _FakeReader(result=None))
    app = _app(polar_source="derived", influx_enabled=True)
    warnings: list[str] = []
    pol = service.resolve_polar(app, CONFIG_DIR, warnings.append)
    assert pol.provisional is True
    assert len(warnings) == 1 and "no polar_baseline data" in warnings[0]


def test_derived_but_unreachable_falls_back_with_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _FakeReader(raises=InfluxUnavailableError("off the boat LAN")))
    app = _app(polar_source="derived", influx_enabled=True)
    warnings: list[str] = []
    pol = service.resolve_polar(app, CONFIG_DIR, warnings.append)
    assert pol.provisional is True
    assert len(warnings) == 1 and "could not read polar_baseline" in warnings[0]


def test_derived_but_token_env_var_unset_falls_back_with_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: enabled=true with no HYLAS_INFLUX_TOKEN set raises
    MissingSecretError (a ConfigError, not an InfluxError) from InfluxConfig.token()
    - caught live 2026-09-18 when enabled was first flipped on with the token not
    yet visible to the running process. Must fall back, not 400 the whole briefing."""
    _install(monkeypatch, _FakeReader(raises=MissingSecretError("HYLAS_INFLUX_TOKEN", "x")))
    app = _app(polar_source="derived", influx_enabled=True)
    warnings: list[str] = []
    pol = service.resolve_polar(app, CONFIG_DIR, warnings.append)
    assert pol.provisional is True
    assert len(warnings) == 1 and "could not read polar_baseline" in warnings[0]


# -- export_polar (no soft fallback — used by the export-polar CLI command) --


def test_export_polar_returns_the_derived_polar(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _FakeReader(result=_DERIVED))
    app = _app(polar_source="derived", influx_enabled=True)
    assert service.export_polar(app) is _DERIVED


def test_export_polar_raises_when_influx_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _FakeReader(raises=RuntimeError("should not be called")))
    app = _app(polar_source="derived", influx_enabled=False)
    with pytest.raises(InfluxError, match="enabled is false"):
        service.export_polar(app)


def test_export_polar_raises_when_no_data_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _FakeReader(result=None))
    app = _app(polar_source="derived", influx_enabled=True)
    with pytest.raises(InfluxError, match="no rows yet"):
        service.export_polar(app)


def test_export_polar_propagates_influx_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _FakeReader(raises=InfluxUnavailableError("off the boat LAN")))
    app = _app(polar_source="derived", influx_enabled=True)
    with pytest.raises(InfluxUnavailableError):
        service.export_polar(app)


# -- resolve_buddy_polars (a fleet boat's own real polar, when one is on file) --


def _boat(**over: object) -> object:
    return fleet.boat_from_dict({
        "name": "SV Example Buddy", "draft_ft": 14.0, "air_draft_ft": 90.0, **over,
    })


def test_no_polar_file_means_no_entry() -> None:
    vessel = replace(load(CONFIG_DIR).vessel, buddy_boats=(_boat(),))
    warnings: list[str] = []
    assert service.resolve_buddy_polars(vessel, CONFIG_DIR, warnings.append) == {}
    assert warnings == []


def test_polar_file_on_disk_is_loaded_and_keyed_by_boat_id(tmp_path: Path) -> None:
    pol_path = tmp_path / "example_buddy.pol"
    real = Polar(
        twa_axis=(60.0, 120.0), tws_axis=(10.0, 20.0),
        grid=((7.0, 9.0), (8.0, 10.0)), source="orc", provisional=False,
    )
    write_pol(real, pol_path)
    boat = _boat(polar_file="example_buddy.pol")
    vessel = replace(load(CONFIG_DIR).vessel, buddy_boats=(boat,))
    warnings: list[str] = []
    loaded = service.resolve_buddy_polars(vessel, tmp_path, warnings.append)
    assert warnings == []
    assert set(loaded) == {boat.id}
    assert loaded[boat.id].speed(10.0, 60.0) == 7.0


def test_missing_polar_file_warns_but_does_not_raise(tmp_path: Path) -> None:
    boat = _boat(polar_file="does_not_exist.pol")
    vessel = replace(load(CONFIG_DIR).vessel, buddy_boats=(boat,))
    warnings: list[str] = []
    loaded = service.resolve_buddy_polars(vessel, tmp_path, warnings.append)
    assert loaded == {}
    assert len(warnings) == 1
    assert "SV Example Buddy" in warnings[0] and "multihull heuristic" in warnings[0]
