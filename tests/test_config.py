"""Validation tests for passage_planner.config.

Run: python -m pytest  (from the routing/ directory)
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from passage_planner import config
from passage_planner.errors import (
    ConfigFileError,
    ConfigValidationError,
    ConfigVersionError,
    MissingSecretError,
)

REPO_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


# ---------------------------------------------------------------------------
# The real shipped config must load and be internally consistent
# ---------------------------------------------------------------------------


def test_shipped_config_loads() -> None:
    app = config.load(REPO_CONFIG_DIR)

    assert app.vessel.model == "your vessel"
    assert app.vessel.draft.planning_ft == 8.0
    assert app.vessel.draft.required_charted_ft == 10.0
    assert app.vessel.air_draft.required_clearance_ft == 75.0
    assert app.vessel.propulsion.usable_fuel_gal == pytest.approx(170.0)
    assert app.vessel.performance.hull_max_speed_kn == 9.0
    assert app.vessel.performance.reef_heel_deg == 15.0
    assert app.vessel.safety.watch_alert.pressure_drop_mb_3h == 2.0
    assert app.vessel.safety.watch_alert.hazard_cpa_nm == 2.0

    primary = app.sources.enabled_weather(tier="primary")
    assert {"gfs", "gfs_wave", "nhc_tropical"} <= {s.source_id for s in primary}

    mwxc = app.sources.weather_by_id("mwxc")
    assert mwxc.enabled is False
    assert mwxc.tier == "professional"
    assert set(mwxc.secret_env_vars()) == {
        "HYLAS_MWXC_IMAP_HOST",
        "HYLAS_MWXC_IMAP_USER",
        "HYLAS_MWXC_IMAP_PASS",
    }


def test_disabled_influx_token_is_empty_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A locally-built disabled config, not the shipped one - InfluxDB went
    # live 2026-09-18 (enabled=true in config/vessel.toml), so this test's
    # actual invariant (disabled + var unset => empty token, never raises)
    # needs its own disabled instance rather than depending on that config
    # value. The env var is cleared explicitly: once HYLAS_INFLUX_TOKEN is a
    # persistent user env var, a fresh process sees it and token() returns it.
    monkeypatch.delenv("HYLAS_INFLUX_TOKEN", raising=False)
    cfg = config.InfluxConfig(
        url="http://192.168.1.10:8086", org="your-org", bucket="boat",
        token_env="HYLAS_INFLUX_TOKEN", write_timeout_s=5.0,
        local_buffer_path="var/influx_buffer.jsonl", enabled=False,
    )
    assert cfg.token() == ""


def test_shipped_config_has_buddy_and_shore_contact() -> None:
    v = config.load(REPO_CONFIG_DIR).vessel
    assert v.shore_contact is not None
    assert "@" in v.shore_contact.email
    assert len(v.buddy_boats) == 1
    sb = v.buddy_boats[0]
    assert sb.name == "SV Example Buddy"
    assert sb.telemetry is False
    # Fleet worst case is driven by the deeper/taller buddy.
    assert v.fleet_draft_ft == 14.0
    assert v.fleet_air_draft_ft == 90.0


def test_shipped_config_has_water_and_boat_zone() -> None:
    v = config.load(REPO_CONFIG_DIR).vessel
    assert v.infra.timezone == "America/Chicago"
    assert v.water is not None
    assert v.water.tank_gal == 150.0
    assert v.water.watermaker_installed is True
    assert v.water.watermaker_commissioned is False
    assert v.water.watermaker_producing is False
    # 150 gal / (2 crew * 3 gpd) = 25 days
    assert v.water.tank_days(2) == pytest.approx(25.0)


def test_minimal_vessel_has_no_water(tmp_path: Path) -> None:
    vc = config.load_vessel_config(_write(tmp_path, "vessel.toml", _MINIMAL_VESSEL))
    assert vc.water is None


# ---------------------------------------------------------------------------
# Vessel file validation
# ---------------------------------------------------------------------------

_MINIMAL_VESSEL = """
config_version = 1
[vessel]
name = "Test"
model = "your vessel"
rig = "cutter"
year = 1990
loa_ft = 51.0
beam_ft = 14.75
[vessel.draft]
static_ft = 6.5
actual_min_ft = 7.0
actual_max_ft = 8.0
planning_ft = 8.0
ukc_margin_ft = 2.0
[vessel.air_draft]
masthead_measured_ft = 61.0
antenna_ft = 3.0
freeboard_ft = 5.0
planning_ft = 70.0
clearance_buffer_ft = 5.0
[propulsion]
engine = "Yanmar 4JH80"
rated_hp = 80
fuel_capacity_gal = 200.0
fuel_reserve_frac = 0.15
[propulsion.cruise]
speed_kn = 7.0
burn_gph = 2.25
motorsail_burn_gph = 1.6
[performance]
polar_source = "baseline"
polar_file = "config/baseline.pol"
fleet_target_avg_kn = 7.0
light_air_speed_kn = 5.0
hull_max_speed_kn = 9.0
reef_heel_deg = 15.0
[safety.thresholds]
wave_hs_max_m = 3.0
wind_sustained_max_kn = 25.0
wind_gust_max_kn = 35.0
beat_twa_min_deg = 45.0
[safety.current]
wind_against_current_tws_min_kn = 15.0
wind_against_current_current_min_kn = 1.5
wind_against_current_angle_deg = 120.0
[safety.watch_alert]
wind_speed_change_kn = 10.0
wind_dir_shift_deg = 30.0
pressure_drop_mb_3h = 2.0
hazard_cpa_nm = 2.0
[safety.depth]
tide_gate_enabled = true
[infra]
timezone = "UTC"
[infra.influx]
url = "http://x:8086"
org = "hylas"
bucket = "routing"
token_env = "HYLAS_INFLUX_TOKEN"
local_buffer_path = "var/b.jsonl"
enabled = false
[infra.signalk]
url = "http://x:3000"
token_env = "HYLAS_SIGNALK_TOKEN"
enabled = false
"""


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return path


def test_minimal_vessel_ok(tmp_path: Path) -> None:
    vc = config.load_vessel_config(_write(tmp_path, "vessel.toml", _MINIMAL_VESSEL))
    assert vc.name == "Test"
    assert vc.propulsion.rated_hp == 80


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigFileError):
        config.load_vessel_config(tmp_path / "nope.toml")


def test_bad_version(tmp_path: Path) -> None:
    body = _MINIMAL_VESSEL.replace("config_version = 1", "config_version = 99")
    with pytest.raises(ConfigVersionError):
        config.load_vessel_config(_write(tmp_path, "vessel.toml", body))


def test_planning_draft_below_actual_max_rejected(tmp_path: Path) -> None:
    body = _MINIMAL_VESSEL.replace("planning_ft = 8.0", "planning_ft = 7.5")
    with pytest.raises(ConfigValidationError, match="planning_ft"):
        config.load_vessel_config(_write(tmp_path, "vessel.toml", body))


def test_light_air_above_target_rejected(tmp_path: Path) -> None:
    body = _MINIMAL_VESSEL.replace("light_air_speed_kn = 5.0", "light_air_speed_kn = 8.0")
    with pytest.raises(ConfigValidationError, match="light_air_speed_kn"):
        config.load_vessel_config(_write(tmp_path, "vessel.toml", body))


def test_gust_below_sustained_rejected(tmp_path: Path) -> None:
    body = _MINIMAL_VESSEL.replace("wind_gust_max_kn = 35.0", "wind_gust_max_kn = 20.0")
    with pytest.raises(ConfigValidationError, match="wind_gust_max_kn"):
        config.load_vessel_config(_write(tmp_path, "vessel.toml", body))


def test_negative_draft_rejected(tmp_path: Path) -> None:
    body = _MINIMAL_VESSEL.replace("planning_ft = 8.0", "planning_ft = -1.0")
    with pytest.raises(ConfigValidationError):
        config.load_vessel_config(_write(tmp_path, "vessel.toml", body))


def test_shipped_config_has_current_thresholds() -> None:
    v = config.load(REPO_CONFIG_DIR).vessel
    cur = v.safety.current
    assert cur.wind_against_current_tws_min_kn > 0.0
    assert cur.wind_against_current_current_min_kn > 0.0
    assert 0.0 < cur.wind_against_current_angle_deg <= 180.0


def test_current_section_missing_is_rejected(tmp_path: Path) -> None:
    body = _MINIMAL_VESSEL.replace(
        "[safety.current]\n"
        "wind_against_current_tws_min_kn = 15.0\n"
        "wind_against_current_current_min_kn = 1.5\n"
        "wind_against_current_angle_deg = 120.0\n",
        "",
    )
    with pytest.raises(ConfigValidationError, match="current"):
        config.load_vessel_config(_write(tmp_path, "vessel.toml", body))


def test_current_angle_out_of_range_rejected(tmp_path: Path) -> None:
    body = _MINIMAL_VESSEL.replace(
        "wind_against_current_angle_deg = 120.0", "wind_against_current_angle_deg = 200.0"
    )
    with pytest.raises(ConfigValidationError, match="wind_against_current_angle_deg"):
        config.load_vessel_config(_write(tmp_path, "vessel.toml", body))


def test_current_angle_zero_rejected(tmp_path: Path) -> None:
    body = _MINIMAL_VESSEL.replace(
        "wind_against_current_angle_deg = 120.0", "wind_against_current_angle_deg = 0.0"
    )
    with pytest.raises(ConfigValidationError, match="wind_against_current_angle_deg"):
        config.load_vessel_config(_write(tmp_path, "vessel.toml", body))


def test_enabled_influx_without_token_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HYLAS_INFLUX_TOKEN", raising=False)
    body = _MINIMAL_VESSEL.replace(
        'token_env = "HYLAS_INFLUX_TOKEN"\nlocal_buffer_path = "var/b.jsonl"\nenabled = false',
        'token_env = "HYLAS_INFLUX_TOKEN"\nlocal_buffer_path = "var/b.jsonl"\nenabled = true',
    )
    vc = config.load_vessel_config(_write(tmp_path, "vessel.toml", body))
    with pytest.raises(MissingSecretError):
        vc.infra.influx.token()


# ---------------------------------------------------------------------------
# Source registry validation
# ---------------------------------------------------------------------------

_SOURCES_OK = """
config_version = 1
[[weather_source]]
id = "gfs"
kind = "grib"
tier = "primary"
enabled = true
provider = "NOAA NCEP"
endpoint = "https://example"
[[weather_source]]
id = "om"
kind = "json"
tier = "secondary"
enabled = true
provider = "Open-Meteo"
endpoint = "https://example"
"""


def test_sources_ok(tmp_path: Path) -> None:
    reg = config.load_source_registry(_write(tmp_path, "sources.toml", _SOURCES_OK))
    assert len(reg.weather) == 2
    assert len(reg.enabled_weather(tier="primary")) == 1


def test_sources_require_a_primary(tmp_path: Path) -> None:
    body = _SOURCES_OK.replace(
        'id = "gfs"\nkind = "grib"\ntier = "primary"\nenabled = true',
        'id = "gfs"\nkind = "grib"\ntier = "primary"\nenabled = false',
    )
    with pytest.raises(ConfigValidationError, match="primary-tier"):
        config.load_source_registry(_write(tmp_path, "sources.toml", body))


def test_sources_reject_bad_tier(tmp_path: Path) -> None:
    body = _SOURCES_OK.replace('tier = "secondary"', 'tier = "blog"')
    with pytest.raises(ConfigValidationError, match="tier"):
        config.load_source_registry(_write(tmp_path, "sources.toml", body))


def test_sources_reject_duplicate_id(tmp_path: Path) -> None:
    body = _SOURCES_OK.replace('id = "om"', 'id = "gfs"')
    with pytest.raises(ConfigValidationError, match="duplicate"):
        config.load_source_registry(_write(tmp_path, "sources.toml", body))
