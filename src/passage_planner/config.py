"""Load and validate the routing framework's configuration.

Purpose: parse and validate `config/vessel.toml` + `config/sources.toml` into
    frozen dataclasses.
Version: v4 (2026-09-24) - env HYLAS_INFLUX_URL overrides [infra.influx] url
    (the Docker stack reaches InfluxDB at a different address).
    v3 (2026-09-22) - `BuddyBoat` gained `call_sign` and `polar_file`
    (a real per-boat polar from a pasted ORC certificate, see boat_lookup.py).
Scope: PRODUCTION - passage-planner's config loader, the entry point every
    CLI/web run starts from.
Architecture: passage-planner subsystem. `load()` is the public entry point;
    `analysis.py`, `service.py` and the CLI all consume the frozen
    `AppConfig`/`VesselConfig` it returns.

Two TOML files are read from a config directory:

* ``vessel.toml``  -> :class:`VesselConfig`  (planning inputs for one boat)
* ``sources.toml`` -> :class:`SourceRegistry` (weather + reference data sources)

The public entry point is :func:`load`. Loading is pure: it touches the
filesystem and the process environment (for secrets named by ``*_env`` keys)
but performs no network I/O and mutates nothing. Every dataclass is frozen so
downstream code can treat a loaded config as an immutable snapshot.

All failures raise a :class:`~passage_planner.errors.ConfigError` subclass with a
message naming the offending file and key.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .errors import (
    ConfigFileError,
    ConfigValidationError,
    ConfigVersionError,
    MissingSecretError,
)

SUPPORTED_CONFIG_VERSION: Final[int] = 1

VESSEL_FILENAME: Final[str] = "vessel.toml"
SOURCES_FILENAME: Final[str] = "sources.toml"

_VALID_TIERS: Final[frozenset[str]] = frozenset(
    {"primary", "professional", "secondary"}
)
_VALID_POLAR_SOURCES: Final[frozenset[str]] = frozenset(
    {"baseline", "derived"}
)


# ---------------------------------------------------------------------------
# Vessel configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DraftConfig:
    static_ft: float
    actual_min_ft: float
    actual_max_ft: float
    planning_ft: float
    ukc_margin_ft: float

    @property
    def required_charted_ft(self) -> float:
        """Charted depth needed before any tide allowance."""
        return self.planning_ft + self.ukc_margin_ft


@dataclass(frozen=True, slots=True)
class AirDraftConfig:
    masthead_measured_ft: float
    antenna_ft: float
    freeboard_ft: float
    planning_ft: float
    clearance_buffer_ft: float

    @property
    def required_clearance_ft(self) -> float:
        """Minimum charted vertical clearance for a fixed overhead structure."""
        return self.planning_ft + self.clearance_buffer_ft


@dataclass(frozen=True, slots=True)
class CruiseConfig:
    speed_kn: float
    burn_gph: float
    motorsail_burn_gph: float


@dataclass(frozen=True, slots=True)
class PropulsionConfig:
    engine: str
    rated_hp: int
    fuel_capacity_gal: float
    fuel_reserve_frac: float
    cruise: CruiseConfig

    @property
    def usable_fuel_gal(self) -> float:
        return self.fuel_capacity_gal * (1.0 - self.fuel_reserve_frac)


@dataclass(frozen=True, slots=True)
class WaterConfig:
    """Fresh-water planning. The tanks cover cooking + hygiene; drinking water
    is handled outside this budget (bottled, plus a jerry can off the watermaker)."""

    tank_gal: float
    per_person_gpd: float             # tank draw per person per day (cooking + hygiene)
    watermaker_installed: bool
    watermaker_commissioned: bool
    watermaker_gph: float             # rated output; 0.0 = not yet known

    def tank_days(self, crew: int) -> float:
        """Days the tanks last for ``crew`` people at the planning draw rate."""
        rate = max(crew, 1) * self.per_person_gpd
        return self.tank_gal / rate if rate > 0 else float("inf")

    @property
    def watermaker_producing(self) -> bool:
        return self.watermaker_installed and self.watermaker_commissioned


@dataclass(frozen=True, slots=True)
class PerformanceConfig:
    polar_source: str
    polar_file: str
    fleet_target_avg_kn: float
    light_air_speed_kn: float
    hull_max_speed_kn: float
    reef_heel_deg: float


@dataclass(frozen=True, slots=True)
class SafetyThresholds:
    wave_hs_max_m: float
    wind_sustained_max_kn: float
    wind_gust_max_kn: float
    beat_twa_min_deg: float


@dataclass(frozen=True, slots=True)
class CurrentThresholds:
    """When the wind-against-current (steep/breaking seas) caution fires. All
    three are seamanship rule-of-thumb defaults, not measured or sourced from a
    specific reference — PROVISIONAL, tune if a real Gulf Stream crossing shows
    they're wrong in either direction (see wiki/Design-Decisions.md)."""

    wind_against_current_tws_min_kn: float
    wind_against_current_current_min_kn: float
    wind_against_current_angle_deg: float


@dataclass(frozen=True, slots=True)
class WatchAlertConfig:
    """'Wake the captain' triggers from the departure SOP."""

    wind_speed_change_kn: float
    wind_dir_shift_deg: float
    pressure_drop_mb_3h: float
    hazard_cpa_nm: float


@dataclass(frozen=True, slots=True)
class SafetyConfig:
    thresholds: SafetyThresholds
    current: CurrentThresholds
    watch_alert: WatchAlertConfig
    tide_gate_enabled: bool


@dataclass(frozen=True, slots=True)
class InfluxConfig:
    url: str
    org: str
    bucket: str
    token_env: str
    write_timeout_s: float
    local_buffer_path: str
    enabled: bool
    org_id: str = ""
    bucket_id: str = ""
    read_timeout_s: float = 10.0

    def token(self) -> str:
        """Resolve the InfluxDB token from the environment.

        Raises :class:`MissingSecretError` when ``enabled`` but the variable is
        unset. When disabled, never raises: returns the variable's value if it
        is set (the ``influx`` diagnostic subcommands use it) else an empty
        string, so a caller running from pasted data never trips the check.
        """
        value = os.environ.get(self.token_env, "")
        if self.enabled and not value:
            raise MissingSecretError(self.token_env, "InfluxDB token")
        return value


@dataclass(frozen=True, slots=True)
class SignalKConfig:
    url: str
    token_env: str
    enabled: bool

    def token(self) -> str:
        if not self.token_env:
            return ""
        value = os.environ.get(self.token_env, "")
        if self.enabled and not value:
            raise MissingSecretError(self.token_env, "Signal K access token")
        return value


@dataclass(frozen=True, slots=True)
class OpsPanelConfig:
    db_path: str
    soon_buffer_days: int
    enabled: bool


@dataclass(frozen=True, slots=True)
class PapersConfig:
    records_dir: str
    extra_dirs: tuple[str, ...]
    acquired_after: str          # ISO date; files older than this outside a "current"
    overrides_path: str          # subdir are treated as legacy candidates
    enabled: bool


@dataclass(frozen=True, slots=True)
class ActiveCaptainConfig:
    token_env: str
    env: str                         # "stage" or "production"
    cache_dir: str
    tile_max_age_days: int
    search_radius_nm: float
    timeout_s: float
    enabled: bool

    def token(self) -> str:
        value = os.environ.get(self.token_env, "")
        if self.enabled and not value:
            raise MissingSecretError(self.token_env, "ActiveCaptain API key")
        return value


@dataclass(frozen=True, slots=True)
class InfraConfig:
    timezone: str
    influx: InfluxConfig
    signalk: SignalKConfig
    ops_panel: OpsPanelConfig | None = None
    papers: PapersConfig | None = None
    activecaptain: ActiveCaptainConfig | None = None


@dataclass(frozen=True, slots=True)
class ShoreContact:
    name: str
    email: str


HULLS: Final = ("monohull", "catamaran", "trimaran")


def boat_id(name: str) -> str:
    """A boat's fleet-list key: lower-case alphanumerics joined by underscores."""
    keep = "".join(c if c.isalnum() or c in " -_" else "" for c in name)
    return "_".join(keep.lower().replace("-", " ").split())


@dataclass(frozen=True, slots=True)
class BuddyBoat:
    """A boat travelling with the fleet. Used for joint-planning constraints —
    shared anchorages, channels, and bridges are governed by the fleet's
    worst-case draft and air draft, not this vessel's alone.

    ``draft_ft`` is the deep (keel-down) figure. ``draft_up_ft``, when set, is
    the retracted draft for a lifting-keel boat that can raise it off the wind
    to get into thin water.
    """

    name: str
    draft_ft: float
    air_draft_ft: float
    lifting_keel: bool = False
    draft_up_ft: float | None = None
    notes: str = ""
    telemetry: bool = False  # do we receive live data from this boat?
    # Fleet-list fields (fleet.py). Make and model are the starting point for a
    # production boat's characteristics (and later a generic polar); hull type changes
    # upwind performance dramatically, so it is recorded and flagged.
    hull: str = "monohull"  # one of HULLS
    make: str = ""
    model: str = ""
    loa_ft: float | None = None
    beam_ft: float | None = None  # matters most for a catamaran: slips and travelifts
    displacement_lb: float | None = None
    mmsi: str = ""
    call_sign: str = ""
    usual_companion: bool = False  # pre-ticked in the "Travelling with" list
    # A real per-boat polar (from a pasted ORC certificate's VPP table, see
    # boat_lookup.py), relative to config/. Empty means no real polar on file -
    # analysis.py falls back to the generic multihull-upwind heuristic instead.
    polar_file: str = ""

    @property
    def id(self) -> str:
        """Stable key for the fleet list and the web form: the name, slugged."""
        return boat_id(self.name)

    @property
    def is_multihull(self) -> bool:
        return self.hull != "monohull"

    @property
    def description(self) -> str:
        """``Beneteau Oceanis 45, monohull`` (make/model only when known)."""
        parts = [" ".join(p for p in (self.make, self.model) if p), self.hull]
        return ", ".join(p for p in parts if p)

    @property
    def keel_up_known(self) -> bool:
        return (
            self.lifting_keel
            and self.draft_up_ft is not None
            and self.draft_up_ft < self.draft_ft
        )

    @property
    def effective_shallow_draft_ft(self) -> float:
        """Retracted draft when known, else the keel-down figure."""
        if self.keel_up_known and self.draft_up_ft is not None:
            return self.draft_up_ft
        return self.draft_ft


@dataclass(frozen=True, slots=True)
class VesselConfig:
    name: str
    model: str
    designer: str
    rig: str
    year: int
    loa_ft: float
    beam_ft: float
    displacement_lb: float
    draft: DraftConfig
    air_draft: AirDraftConfig
    propulsion: PropulsionConfig
    performance: PerformanceConfig
    safety: SafetyConfig
    infra: InfraConfig
    water: WaterConfig | None = None
    shore_contact: ShoreContact | None = None
    buddy_boats: tuple[BuddyBoat, ...] = ()
    mmsi: str = ""

    @property
    def fleet_draft_ft(self) -> float:
        """Deepest draft in the fleet with every lifting keel DOWN (open-water case)."""
        return max([self.draft.planning_ft, *(b.draft_ft for b in self.buddy_boats)])

    @property
    def fleet_draft_min_ft(self) -> float:
        """Deepest draft in the fleet with every lifting keel UP where it can be
        (the best case for a shared thin-water stop)."""
        return max([
            self.draft.planning_ft,
            *(b.effective_shallow_draft_ft for b in self.buddy_boats),
        ])

    @property
    def fleet_has_lifting_keel(self) -> bool:
        return any(b.lifting_keel for b in self.buddy_boats)

    @property
    def fleet_air_draft_ft(self) -> float:
        """Tallest air draft in the fleet."""
        return max([self.air_draft.planning_ft, *(b.air_draft_ft for b in self.buddy_boats)])


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WeatherSource:
    source_id: str
    kind: str
    tier: str
    enabled: bool
    provider: str
    endpoint: str | None
    options: dict[str, Any]

    def secret_env_vars(self) -> tuple[str, ...]:
        """Environment variable names this source needs, in declaration order."""
        return tuple(
            str(v)
            for k, v in self.options.items()
            if k.endswith("_env")
        )

    def resolve_secret(self, option_key: str) -> str:
        """Read a secret named by ``options[option_key]`` from the environment.

        Raises :class:`ConfigValidationError` if the option is absent and
        :class:`MissingSecretError` if the named variable is unset while the
        source is enabled.
        """
        if option_key not in self.options:
            raise ConfigValidationError(
                f"{SOURCES_FILENAME}: source {self.source_id!r} has no "
                f"{option_key!r} option"
            )
        env_var = str(self.options[option_key])
        value = os.environ.get(env_var, "")
        if self.enabled and not value:
            raise MissingSecretError(
                env_var, f"weather source {self.source_id!r}"
            )
        return value


@dataclass(frozen=True, slots=True)
class DataSource:
    source_id: str
    kind: str
    tier: str
    enabled: bool
    provider: str
    endpoint: str | None
    options: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SourceRegistry:
    weather: tuple[WeatherSource, ...]
    data: tuple[DataSource, ...]

    def weather_by_id(self, source_id: str) -> WeatherSource:
        for src in self.weather:
            if src.source_id == source_id:
                return src
        raise KeyError(source_id)

    def enabled_weather(self, *, tier: str | None = None) -> tuple[WeatherSource, ...]:
        return tuple(
            s
            for s in self.weather
            if s.enabled and (tier is None or s.tier == tier)
        )

    def enabled_data(self, *, kind: str | None = None) -> tuple[DataSource, ...]:
        return tuple(
            s
            for s in self.data
            if s.enabled and (kind is None or s.kind == kind)
        )


@dataclass(frozen=True, slots=True)
class AppConfig:
    config_dir: Path
    vessel: VesselConfig
    sources: SourceRegistry


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigFileError(f"configuration file not found: {path}") from exc
    except OSError as exc:
        raise ConfigFileError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigFileError(f"{path} is not valid TOML: {exc}") from exc


def _check_version(raw: dict[str, Any], filename: str) -> None:
    version = raw.get("config_version")
    if version is None:
        raise ConfigVersionError(f"{filename}: missing 'config_version'")
    if not isinstance(version, int):
        raise ConfigVersionError(
            f"{filename}: 'config_version' must be an integer, got {version!r}"
        )
    if version != SUPPORTED_CONFIG_VERSION:
        raise ConfigVersionError(
            f"{filename}: config_version {version} is not supported "
            f"(this build handles {SUPPORTED_CONFIG_VERSION})"
        )


def _require(mapping: dict[str, Any], key: str, fn: str, ctx: str) -> Any:
    if key not in mapping:
        raise ConfigValidationError(f"{fn}: [{ctx}] is missing '{key}'")
    return mapping[key]


def _table(mapping: dict[str, Any], key: str, fn: str, ctx: str) -> dict[str, Any]:
    value = _require(mapping, key, fn, ctx)
    if not isinstance(value, dict):
        raise ConfigValidationError(f"{fn}: [{ctx}.{key}] must be a table")
    return value


def _str(
    mapping: dict[str, Any], key: str, fn: str, ctx: str, *, default: str | None = None
) -> str:
    if key not in mapping:
        if default is not None:
            return default
        raise ConfigValidationError(f"{fn}: [{ctx}] is missing '{key}'")
    return str(mapping[key])


def _int(mapping: dict[str, Any], key: str, fn: str, ctx: str) -> int:
    value = _require(mapping, key, fn, ctx)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError(f"{fn}: [{ctx}] '{key}' must be an integer, got {value!r}")
    return int(value)


def _num(
    mapping: dict[str, Any],
    key: str,
    fn: str,
    ctx: str,
    *,
    default: float | None = None,
    positive: bool = False,
    fraction: bool = False,
) -> float:
    """Read ``key`` as a float, with optional range checks."""
    if key not in mapping:
        if default is not None:
            return default
        raise ConfigValidationError(f"{fn}: [{ctx}] is missing '{key}'")
    raw = mapping[key]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ConfigValidationError(f"{fn}: [{ctx}] '{key}' must be a number, got {raw!r}")
    value = float(raw)
    if positive and value <= 0.0:
        raise ConfigValidationError(f"{fn}: [{ctx}] '{key}' must be > 0, got {value}")
    if fraction and not 0.0 <= value < 1.0:
        raise ConfigValidationError(f"{fn}: [{ctx}] '{key}' must be in [0.0, 1.0), got {value}")
    return value


# ---------------------------------------------------------------------------
# Vessel parsing
# ---------------------------------------------------------------------------


def _parse_draft(raw: dict[str, Any], fn: str) -> DraftConfig:
    ctx = "vessel.draft"
    static_ft = _num(raw, "static_ft", fn, ctx, positive=True)
    actual_min = _num(raw, "actual_min_ft", fn, ctx, positive=True)
    actual_max = _num(raw, "actual_max_ft", fn, ctx, positive=True)
    planning = _num(raw, "planning_ft", fn, ctx, positive=True)
    ukc = _num(raw, "ukc_margin_ft", fn, ctx, positive=True)
    if actual_max < actual_min:
        raise ConfigValidationError(
            f"{fn}: [{ctx}] actual_max_ft ({actual_max}) < actual_min_ft ({actual_min})"
        )
    if planning < actual_max:
        raise ConfigValidationError(
            f"{fn}: [{ctx}] planning_ft ({planning}) should be >= actual_max_ft ({actual_max})"
        )
    return DraftConfig(static_ft, actual_min, actual_max, planning, ukc)


def _parse_air_draft(raw: dict[str, Any], fn: str) -> AirDraftConfig:
    ctx = "vessel.air_draft"
    masthead = _num(raw, "masthead_measured_ft", fn, ctx, positive=True)
    antenna = _num(raw, "antenna_ft", fn, ctx)
    freeboard = _num(raw, "freeboard_ft", fn, ctx)
    planning = _num(raw, "planning_ft", fn, ctx, positive=True)
    buffer_ft = _num(raw, "clearance_buffer_ft", fn, ctx, positive=True)
    if antenna < 0.0 or freeboard < 0.0:
        raise ConfigValidationError(f"{fn}: [{ctx}] antenna_ft/freeboard_ft must be >= 0")
    components = masthead + antenna + freeboard
    if planning < components - 1.0:
        raise ConfigValidationError(
            f"{fn}: [{ctx}] planning_ft ({planning}) is below the sum of its "
            f"components ({components:.1f})"
        )
    return AirDraftConfig(masthead, antenna, freeboard, planning, buffer_ft)


def _parse_water(raw: dict[str, Any], fn: str) -> WaterConfig:
    ctx = "vessel.water"
    return WaterConfig(
        tank_gal=_num(raw, "tank_gal", fn, ctx, positive=True),
        per_person_gpd=_num(raw, "per_person_gpd", fn, ctx, default=3.0, positive=True),
        watermaker_installed=bool(raw.get("watermaker_installed", False)),
        watermaker_commissioned=bool(raw.get("watermaker_commissioned", False)),
        watermaker_gph=_num(raw, "watermaker_gph", fn, ctx, default=0.0),
    )


def _parse_propulsion(raw: dict[str, Any], fn: str) -> PropulsionConfig:
    ctx = "propulsion"
    engine = _str(raw, "engine", fn, ctx)
    rated_hp = _int(raw, "rated_hp", fn, ctx)
    capacity = _num(raw, "fuel_capacity_gal", fn, ctx, positive=True)
    reserve = _num(raw, "fuel_reserve_frac", fn, ctx, fraction=True)

    cctx = "propulsion.cruise"
    cruise_raw = _table(raw, "cruise", fn, ctx)
    cruise = CruiseConfig(
        speed_kn=_num(cruise_raw, "speed_kn", fn, cctx, positive=True),
        burn_gph=_num(cruise_raw, "burn_gph", fn, cctx, positive=True),
        motorsail_burn_gph=_num(cruise_raw, "motorsail_burn_gph", fn, cctx, positive=True),
    )
    return PropulsionConfig(engine, rated_hp, capacity, reserve, cruise)


def _parse_performance(raw: dict[str, Any], fn: str) -> PerformanceConfig:
    ctx = "performance"
    polar_source = _str(raw, "polar_source", fn, ctx)
    if polar_source not in _VALID_POLAR_SOURCES:
        raise ConfigValidationError(
            f"{fn}: [{ctx}] 'polar_source' must be one of "
            f"{sorted(_VALID_POLAR_SOURCES)}, got {polar_source!r}"
        )
    polar_file = _str(raw, "polar_file", fn, ctx)
    target = _num(raw, "fleet_target_avg_kn", fn, ctx, positive=True)
    light_air = _num(raw, "light_air_speed_kn", fn, ctx, positive=True)
    hull_max = _num(raw, "hull_max_speed_kn", fn, ctx, positive=True)
    reef_heel = _num(raw, "reef_heel_deg", fn, ctx, positive=True)
    if light_air >= target:
        raise ConfigValidationError(
            f"{fn}: [{ctx}] light_air_speed_kn ({light_air}) must be below "
            f"fleet_target_avg_kn ({target})"
        )
    if hull_max < target:
        raise ConfigValidationError(
            f"{fn}: [{ctx}] hull_max_speed_kn ({hull_max}) must be >= "
            f"fleet_target_avg_kn ({target})"
        )
    return PerformanceConfig(polar_source, polar_file, target, light_air, hull_max, reef_heel)


def _parse_safety(raw: dict[str, Any], fn: str) -> SafetyConfig:
    ctx = "safety"
    tctx = "safety.thresholds"
    thr_raw = _table(raw, "thresholds", fn, ctx)
    thresholds = SafetyThresholds(
        wave_hs_max_m=_num(thr_raw, "wave_hs_max_m", fn, tctx, positive=True),
        wind_sustained_max_kn=_num(thr_raw, "wind_sustained_max_kn", fn, tctx, positive=True),
        wind_gust_max_kn=_num(thr_raw, "wind_gust_max_kn", fn, tctx, positive=True),
        beat_twa_min_deg=_num(thr_raw, "beat_twa_min_deg", fn, tctx, positive=True),
    )
    if thresholds.beat_twa_min_deg >= 90.0:
        raise ConfigValidationError(
            f"{fn}: [{tctx}] beat_twa_min_deg ({thresholds.beat_twa_min_deg}) must be < 90"
        )
    if thresholds.wind_gust_max_kn < thresholds.wind_sustained_max_kn:
        raise ConfigValidationError(
            f"{fn}: [{tctx}] wind_gust_max_kn must be >= wind_sustained_max_kn"
        )

    cctx = "safety.current"
    cur_raw = _table(raw, "current", fn, ctx)
    current = CurrentThresholds(
        wind_against_current_tws_min_kn=_num(
            cur_raw, "wind_against_current_tws_min_kn", fn, cctx, positive=True
        ),
        wind_against_current_current_min_kn=_num(
            cur_raw, "wind_against_current_current_min_kn", fn, cctx, positive=True
        ),
        wind_against_current_angle_deg=_num(
            cur_raw, "wind_against_current_angle_deg", fn, cctx, positive=True
        ),
    )
    if not (0.0 < current.wind_against_current_angle_deg <= 180.0):
        raise ConfigValidationError(
            f"{fn}: [{cctx}] wind_against_current_angle_deg "
            f"({current.wind_against_current_angle_deg}) must be in (0, 180]"
        )

    wctx = "safety.watch_alert"
    wa_raw = _table(raw, "watch_alert", fn, ctx)
    watch_alert = WatchAlertConfig(
        wind_speed_change_kn=_num(wa_raw, "wind_speed_change_kn", fn, wctx, positive=True),
        wind_dir_shift_deg=_num(wa_raw, "wind_dir_shift_deg", fn, wctx, positive=True),
        pressure_drop_mb_3h=_num(wa_raw, "pressure_drop_mb_3h", fn, wctx, positive=True),
        hazard_cpa_nm=_num(wa_raw, "hazard_cpa_nm", fn, wctx, positive=True),
    )

    depth_raw = raw.get("depth", {})
    if not isinstance(depth_raw, dict):
        raise ConfigValidationError(f"{fn}: [{ctx}.depth] must be a table")
    tide_gate = bool(depth_raw.get("tide_gate_enabled", True))
    return SafetyConfig(thresholds, current, watch_alert, tide_gate)


def _parse_infra(raw: dict[str, Any], fn: str) -> InfraConfig:
    ctx = "infra"
    timezone = _str(raw, "timezone", fn, ctx, default="UTC")

    ictx = "infra.influx"
    influx_raw = _table(raw, "influx", fn, ctx)
    influx = InfluxConfig(
        # HYLAS_INFLUX_URL overrides the file: inside the Docker stack InfluxDB is
        # 172.18.0.40:8086 on openhab-net, not the LAN address in vessel.toml.
        url=os.environ.get("HYLAS_INFLUX_URL", "").strip() or _str(influx_raw, "url", fn, ictx),
        org=_str(influx_raw, "org", fn, ictx),
        bucket=_str(influx_raw, "bucket", fn, ictx),
        token_env=_str(influx_raw, "token_env", fn, ictx),
        write_timeout_s=_num(influx_raw, "write_timeout_s", fn, ictx, default=5.0, positive=True),
        local_buffer_path=_str(influx_raw, "local_buffer_path", fn, ictx),
        enabled=bool(influx_raw.get("enabled", False)),
        org_id=_str(influx_raw, "org_id", fn, ictx, default=""),
        bucket_id=_str(influx_raw, "bucket_id", fn, ictx, default=""),
        read_timeout_s=_num(influx_raw, "read_timeout_s", fn, ictx, default=10.0, positive=True),
    )

    sctx = "infra.signalk"
    sk_raw = _table(raw, "signalk", fn, ctx)
    signalk = SignalKConfig(
        url=_str(sk_raw, "url", fn, sctx),
        token_env=_str(sk_raw, "token_env", fn, sctx, default=""),
        enabled=bool(sk_raw.get("enabled", False)),
    )

    ops_panel: OpsPanelConfig | None = None
    if "ops_panel" in raw:
        octx = "infra.ops_panel"
        op_raw = _table(raw, "ops_panel", fn, ctx)
        ops_panel = OpsPanelConfig(
            db_path=_str(op_raw, "db_path", fn, octx),
            soon_buffer_days=int(_num(op_raw, "soon_buffer_days", fn, octx, default=10.0)),
            enabled=bool(op_raw.get("enabled", True)),
        )

    papers: PapersConfig | None = None
    if "papers" in raw:
        pctx = "infra.papers"
        pp_raw = _table(raw, "papers", fn, ctx)
        extra = pp_raw.get("extra_dirs", [])
        if not isinstance(extra, list):
            raise ConfigValidationError(f"{fn}: [{pctx}] 'extra_dirs' must be an array")
        papers = PapersConfig(
            records_dir=_str(pp_raw, "records_dir", fn, pctx),
            extra_dirs=tuple(str(d) for d in extra),
            acquired_after=_str(pp_raw, "acquired_after", fn, pctx, default="2025-01-01"),
            overrides_path=_str(pp_raw, "overrides_path", fn, pctx, default="config/papers.toml"),
            enabled=bool(pp_raw.get("enabled", True)),
        )

    activecaptain: ActiveCaptainConfig | None = None
    if "activecaptain" in raw:
        actx = "infra.activecaptain"
        ac_raw = _table(raw, "activecaptain", fn, ctx)
        activecaptain = ActiveCaptainConfig(
            token_env=_str(ac_raw, "token_env", fn, actx),
            env=_str(ac_raw, "env", fn, actx, default="production"),
            cache_dir=_str(ac_raw, "cache_dir", fn, actx, default="var/cache"),
            tile_max_age_days=int(_num(ac_raw, "tile_max_age_days", fn, actx, default=7.0)),
            search_radius_nm=_num(
                ac_raw, "search_radius_nm", fn, actx, default=10.0, positive=True,
            ),
            timeout_s=_num(ac_raw, "timeout_s", fn, actx, default=60.0, positive=True),
            enabled=bool(ac_raw.get("enabled", True)),
        )

    return InfraConfig(timezone, influx, signalk, ops_panel, papers, activecaptain)


def _parse_vessel(raw: dict[str, Any]) -> VesselConfig:
    fn = VESSEL_FILENAME
    _check_version(raw, fn)

    v = _table(raw, "vessel", fn, "<root>")

    shore_contact: ShoreContact | None = None
    if "shore_contact" in raw:
        sc = _table(raw, "shore_contact", fn, "<root>")
        shore_contact = ShoreContact(
            name=_str(sc, "name", fn, "shore_contact"),
            email=_str(sc, "email", fn, "shore_contact"),
        )

    buddy_raw = raw.get("buddy_boat", [])
    if not isinstance(buddy_raw, list):
        raise ConfigValidationError(f"{fn}: 'buddy_boat' must be an array of tables")
    buddy_boats: list[BuddyBoat] = []
    for i, entry in enumerate(buddy_raw):
        bctx = f"buddy_boat[{i}]"
        if not isinstance(entry, dict):
            raise ConfigValidationError(f"{fn}: {bctx} must be a table")
        draft_up = (
            _num(entry, "draft_up_ft", fn, bctx, positive=True)
            if "draft_up_ft" in entry
            else None
        )
        hull = _str(entry, "hull", fn, bctx, default="monohull")
        if hull not in HULLS:
            raise ConfigValidationError(f"{fn}: {bctx}.hull must be one of {list(HULLS)}")

        def opt(key: str, _e: dict[str, Any] = entry, _c: str = bctx) -> float | None:
            return _num(_e, key, fn, _c, positive=True) if key in _e else None

        buddy_boats.append(BuddyBoat(
            name=_str(entry, "name", fn, bctx),
            draft_ft=_num(entry, "draft_ft", fn, bctx, positive=True),
            air_draft_ft=_num(entry, "air_draft_ft", fn, bctx, positive=True),
            lifting_keel=bool(entry.get("lifting_keel", draft_up is not None)),
            draft_up_ft=draft_up,
            notes=_str(entry, "notes", fn, bctx, default=""),
            telemetry=bool(entry.get("telemetry", False)),
            hull=hull,
            make=_str(entry, "make", fn, bctx, default=""),
            model=_str(entry, "model", fn, bctx, default=""),
            loa_ft=opt("loa_ft"),
            beam_ft=opt("beam_ft"),
            displacement_lb=opt("displacement_lb"),
            mmsi=_str(entry, "mmsi", fn, bctx, default=""),
            call_sign=_str(entry, "call_sign", fn, bctx, default=""),
            # a boat listed in vessel.toml has always travelled with the vessel
            usual_companion=bool(entry.get("usual_companion", True)),
            polar_file=_str(entry, "polar_file", fn, bctx, default=""),
        ))

    return VesselConfig(
        name=_str(v, "name", fn, "vessel"),
        model=_str(v, "model", fn, "vessel"),
        designer=_str(v, "designer", fn, "vessel", default=""),
        rig=_str(v, "rig", fn, "vessel"),
        year=_int(v, "year", fn, "vessel"),
        loa_ft=_num(v, "loa_ft", fn, "vessel", positive=True),
        beam_ft=_num(v, "beam_ft", fn, "vessel", positive=True),
        displacement_lb=_num(v, "displacement_lb", fn, "vessel", default=1.0, positive=True),
        draft=_parse_draft(_table(v, "draft", fn, "vessel"), fn),
        air_draft=_parse_air_draft(_table(v, "air_draft", fn, "vessel"), fn),
        propulsion=_parse_propulsion(_table(raw, "propulsion", fn, "<root>"), fn),
        performance=_parse_performance(_table(raw, "performance", fn, "<root>"), fn),
        safety=_parse_safety(_table(raw, "safety", fn, "<root>"), fn),
        infra=_parse_infra(_table(raw, "infra", fn, "<root>"), fn),
        water=_parse_water(_table(v, "water", fn, "vessel"), fn) if "water" in v else None,
        shore_contact=shore_contact,
        buddy_boats=tuple(buddy_boats),
        mmsi=_str(v, "mmsi", fn, "vessel", default=""),
    )


# ---------------------------------------------------------------------------
# Source registry parsing
# ---------------------------------------------------------------------------

_SOURCE_CORE_KEYS: Final[frozenset[str]] = frozenset(
    {"id", "kind", "tier", "enabled", "provider", "endpoint"}
)


def _parse_source_common(
    entry: dict[str, Any], fn: str, table: str, index: int
) -> tuple[str, str, str, bool, str, str | None, dict[str, Any]]:
    ctx = f"{table}[{index}]"
    if not isinstance(entry, dict):
        raise ConfigValidationError(f"{fn}: {ctx} must be a table")
    source_id = str(_require(entry, "id", fn, ctx))
    kind = str(_require(entry, "kind", fn, ctx))
    tier = str(_require(entry, "tier", fn, ctx))
    if tier not in _VALID_TIERS:
        raise ConfigValidationError(
            f"{fn}: {ctx} tier must be one of {sorted(_VALID_TIERS)}, got {tier!r}"
        )
    provider = str(_require(entry, "provider", fn, ctx))
    enabled = bool(entry.get("enabled", False))
    endpoint_raw = entry.get("endpoint")
    endpoint = None if endpoint_raw is None else str(endpoint_raw)
    options = {k: val for k, val in entry.items() if k not in _SOURCE_CORE_KEYS}
    return source_id, kind, tier, enabled, provider, endpoint, options


def _parse_sources(raw: dict[str, Any]) -> SourceRegistry:
    fn = SOURCES_FILENAME
    _check_version(raw, fn)

    weather_entries = raw.get("weather_source", [])
    data_entries = raw.get("data_source", [])
    if not isinstance(weather_entries, list) or not isinstance(data_entries, list):
        raise ConfigValidationError(
            f"{fn}: 'weather_source' and 'data_source' must be arrays of tables"
        )

    seen: set[str] = set()
    weather: list[WeatherSource] = []
    for i, entry in enumerate(weather_entries):
        sid, kind, tier, enabled, provider, endpoint, options = _parse_source_common(
            entry, fn, "weather_source", i
        )
        if sid in seen:
            raise ConfigValidationError(f"{fn}: duplicate source id {sid!r}")
        seen.add(sid)
        weather.append(
            WeatherSource(sid, kind, tier, enabled, provider, endpoint, options)
        )

    data: list[DataSource] = []
    for i, entry in enumerate(data_entries):
        sid, kind, tier, enabled, provider, endpoint, options = _parse_source_common(
            entry, fn, "data_source", i
        )
        if sid in seen:
            raise ConfigValidationError(f"{fn}: duplicate source id {sid!r}")
        seen.add(sid)
        data.append(
            DataSource(sid, kind, tier, enabled, provider, endpoint, options)
        )

    if not any(s.enabled and s.tier == "primary" for s in weather):
        raise ConfigValidationError(
            f"{fn}: at least one enabled primary-tier weather source is required"
        )
    return SourceRegistry(tuple(weather), tuple(data))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_vessel_config(path: Path) -> VesselConfig:
    """Load and validate a single ``vessel.toml`` file."""
    return _parse_vessel(_read_toml(path))


def load_source_registry(path: Path) -> SourceRegistry:
    """Load and validate a single ``sources.toml`` file."""
    return _parse_sources(_read_toml(path))


def load(config_dir: str | os.PathLike[str]) -> AppConfig:
    """Load the full application configuration from a directory.

    ``config_dir`` must contain ``vessel.toml`` and ``sources.toml``.
    """
    directory = Path(config_dir).expanduser().resolve()
    if not directory.is_dir():
        raise ConfigFileError(f"config directory not found: {directory}")
    vessel = load_vessel_config(directory / VESSEL_FILENAME)
    sources = load_source_registry(directory / SOURCES_FILENAME)
    return AppConfig(config_dir=directory, vessel=vessel, sources=sources)
