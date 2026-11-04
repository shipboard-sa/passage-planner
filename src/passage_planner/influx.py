"""Read-only client for the vessel telemetry bucket on ``your-pi-hostname``.

Purpose: bounded, read-only Flux queries against the builder's `boat` InfluxDB
    bucket - telemetry, GPS fix, and (since v2) his derived polar.
Version: v2 (2026-09-18) - added `polar_baseline()` / `flux_polar_baseline()`.
Scope: PRODUCTION - passage-planner's only InfluxDB access point.
Architecture: passage-planner subsystem, data layer. Called by `service.py`
    (polar) and CLI `influx` subcommands; never writes - see
    `wiki/Data-Flows-and-Interfaces.md`.

Wraps ``influxdb-client`` with the query patterns this framework needs — latest
value, a bounded time series, the current GPS fix — and the schema quirks from
the builder's ``influxdb_readonly_access.md``:

* SignalK paths: ``_measurement`` **is** the path, ``_field`` is always
  ``"value"``. ``navigation.position.latitude`` is a measurement, not a field
  of ``navigation.position``. The wrong form returns zero rows, no error.
* ``cerbo.*`` measurements carry both numeric ``value`` and string
  ``text_value`` — every query filters ``_field == "value"``.
* The Pi is shared; every query is bounded in ``range()`` and filtered to one
  measurement.

The reader is optional. When ``infra.influx.enabled`` is false, or the boat
LAN is unreachable (planning from home), callers get ``None`` / an
:class:`~passage_planner.errors.InfluxUnavailableError` they can treat as
"no data" — the framework runs from pasted data regardless.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import AppConfig, InfluxConfig
from .errors import InfluxAuthError, InfluxError, InfluxUnavailableError
from .polar import Polar, polar_from_grid

# A query runner takes a Flux string and returns one dict per record (the
# record's column→value mapping). Injected in tests; the default hits InfluxDB.
QueryRunner = Callable[[str], list[Mapping[str, Any]]]

_DEFAULT_LOOKBACK = timedelta(days=30)
# polar_baseline is a static snapshot (the builder's derivation pipeline overwrites it
# in place, all rows sharing one timestamp), not an accumulating time series —
# a normal telemetry lookback would miss it once it ages past 30 days.
_POLAR_LOOKBACK = timedelta(days=3650)


@dataclass(frozen=True, slots=True)
class Sample:
    time: datetime
    value: float


@dataclass(frozen=True, slots=True)
class GpsFix:
    time: datetime
    lat: float
    lon: float

    @property
    def age(self) -> timedelta:
        return datetime.now(UTC) - self.time


# ---------------------------------------------------------------------------
# Flux builders (pure — unit-tested without a server)
# ---------------------------------------------------------------------------


def _q(s: str) -> str:
    """Quote a string for embedding in Flux (double quotes, backslash-escaped)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _rel_seconds(td: timedelta) -> str:
    return f"-{int(td.total_seconds())}s"


def flux_measurements(bucket: str, lookback: timedelta) -> str:
    return (
        'import "influxdata/influxdb/schema"\n'
        f"schema.measurements(bucket: {_q(bucket)}, "
        f"start: {_rel_seconds(lookback)})"
    )


def flux_latest(
    bucket: str, measurement: str, field: str, within: timedelta, source: str | None
) -> str:
    src = f' and r["$source"] == {_q(source)}' if source else ""
    return (
        f"from(bucket: {_q(bucket)})\n"
        f"  |> range(start: {_rel_seconds(within)})\n"
        f"  |> filter(fn: (r) => r._measurement == {_q(measurement)} "
        f"and r._field == {_q(field)}{src})\n"
        f"  |> last()"
    )


def flux_series(
    bucket: str,
    measurement: str,
    field: str,
    start: datetime,
    stop: datetime | None,
    every: timedelta | None,
    agg: str,
    source: str | None,
) -> str:
    src = f' and r["$source"] == {_q(source)}' if source else ""
    stop_line = f"start: {_iso(start)}, stop: {_iso(stop)}" if stop else f"start: {_iso(start)}"
    lines = [
        f"from(bucket: {_q(bucket)})",
        f"  |> range({stop_line})",
        f"  |> filter(fn: (r) => r._measurement == {_q(measurement)} "
        f"and r._field == {_q(field)}{src})",
    ]
    if every is not None:
        lines.append(
            f"  |> aggregateWindow(every: {int(every.total_seconds())}s, "
            f"fn: {agg}, createEmpty: false)"
        )
    lines.append("  |> keep(columns: [\"_time\", \"_value\"])")
    return "\n".join(lines)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def flux_polar_baseline(bucket: str, measurement: str, field: str, lookback: timedelta) -> str:
    return (
        f"from(bucket: {_q(bucket)})\n"
        f"  |> range(start: {_rel_seconds(lookback)})\n"
        f"  |> filter(fn: (r) => r._measurement == {_q(measurement)} "
        f"and r._field == {_q(field)})\n"
        '  |> keep(columns: ["_time", "_value", "twa_deg", "tws_kn"])'
    )


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


class InfluxReader:
    """Bounded read access to the ``boat`` bucket."""

    def __init__(self, config: InfluxConfig, *, query_runner: QueryRunner | None = None) -> None:
        self._cfg = config
        self._runner = query_runner or self._default_runner
        self._client: Any = None  # lazily created influxdb_client.InfluxDBClient

    @classmethod
    def from_app_config(
        cls, app: AppConfig, *, query_runner: QueryRunner | None = None
    ) -> InfluxReader:
        return cls(app.vessel.infra.influx, query_runner=query_runner)

    # -- lifecycle ------------------------------------------------------

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                from influxdb_client import InfluxDBClient  # type: ignore[attr-defined]
            except ImportError as exc:  # pragma: no cover - dependency present in this project
                raise InfluxError("influxdb-client is not installed") from exc
            self._client = InfluxDBClient(
                url=self._cfg.url,
                token=self._cfg.token(),
                org=self._cfg.org,
                timeout=int(self._cfg.read_timeout_s * 1000),
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> InfluxReader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _default_runner(self, flux: str) -> list[Mapping[str, Any]]:
        client = self._ensure_client()
        try:
            tables = client.query_api().query(flux)
        except Exception as exc:
            raise _translate(exc) from exc
        out: list[Mapping[str, Any]] = []
        for table in tables:
            for record in table.records:
                out.append(dict(record.values))
        return out

    # -- queries ------------------------------------------------------

    def ping(self) -> bool:
        """True if the server answers and the token is accepted."""
        try:
            self._runner(flux_measurements(self._cfg.bucket, timedelta(minutes=5)))
        except InfluxUnavailableError:
            return False
        return True

    def measurements(self, *, lookback: timedelta = _DEFAULT_LOOKBACK) -> list[str]:
        rows = self._runner(flux_measurements(self._cfg.bucket, lookback))
        names = sorted({str(r.get("_value")) for r in rows if r.get("_value") is not None})
        return names

    def latest(
        self,
        measurement: str,
        *,
        field: str = "value",
        within: timedelta = timedelta(hours=1),
        source: str | None = None,
    ) -> Sample | None:
        rows = self._runner(
            flux_latest(self._cfg.bucket, measurement, field, within, source)
        )
        recs = [s for r in rows if (s := _as_sample(r)) is not None]
        if not recs:
            return None
        return max(recs, key=lambda s: s.time)

    def series(
        self,
        measurement: str,
        start: datetime,
        *,
        field: str = "value",
        stop: datetime | None = None,
        every: timedelta | None = None,
        agg: str = "mean",
        source: str | None = None,
    ) -> list[Sample]:
        if start.tzinfo is None:
            raise ValueError("start must be timezone-aware")
        rows = self._runner(
            flux_series(self._cfg.bucket, measurement, field, start, stop, every, agg, source)
        )
        samples = [s for r in rows if (s := _as_sample(r)) is not None]
        samples.sort(key=lambda s: s.time)
        return samples

    def latest_fix(
        self, *, within: timedelta = timedelta(minutes=15), source: str | None = None
    ) -> GpsFix | None:
        """Current GPS position from ``navigation.position.{latitude,longitude}``."""
        lat = self.latest("navigation.position.latitude", within=within, source=source)
        lon = self.latest("navigation.position.longitude", within=within, source=source)
        if lat is None or lon is None:
            return None
        return GpsFix(time=max(lat.time, lon.time), lat=lat.value, lon=lon.value)

    def polar_baseline(
        self,
        *,
        measurement: str = "polar_baseline",
        field: str = "boat_speed_kn",
        lookback: timedelta = _POLAR_LOOKBACK,
    ) -> Polar | None:
        """the builder's empirically-derived polar, published as a live measurement in
        this same ``boat`` bucket (schema confirmed live 2026-09-17: tags
        ``twa_deg``/``tws_kn``, field ``boat_speed_kn``, one row per grid cell).
        Pure read — this project never derives or refines a polar of its own,
        it only ever consumes the builder's.

        Returns ``None`` if the measurement has no rows yet (soft, same pattern
        as every other read here — a missing/incomplete grid is "no data", not
        an error). Raises :class:`InfluxError`/:class:`InfluxUnavailableError`/
        :class:`InfluxAuthError` on network/auth failure, exactly like the other
        methods.
        """
        rows = self._runner(flux_polar_baseline(self._cfg.bucket, measurement, field, lookback))
        latest_by_cell: dict[tuple[float, float], tuple[datetime, float]] = {}
        for r in rows:
            t = r.get("_time")
            v = r.get("_value")
            twa_raw, tws_raw = r.get("twa_deg"), r.get("tws_kn")
            if (
                not isinstance(t, datetime)
                or not isinstance(v, (int, float))
                or isinstance(v, bool)
            ):
                continue
            if twa_raw is None or tws_raw is None:
                continue
            try:
                key = (float(twa_raw), float(tws_raw))
            except (TypeError, ValueError):
                continue
            prev = latest_by_cell.get(key)
            if prev is None or t > prev[0]:
                latest_by_cell[key] = (t, float(v))
        if not latest_by_cell:
            return None
        cells = {key: value for key, (_time, value) in latest_by_cell.items()}
        return polar_from_grid(
            cells, source=f"influxdb:{self._cfg.bucket}/{measurement}", provisional=False
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _as_sample(row: Mapping[str, Any]) -> Sample | None:
    t = row.get("_time")
    v = row.get("_value")
    if not isinstance(t, datetime) or not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    return Sample(time=t if t.tzinfo else t.replace(tzinfo=UTC), value=float(v))


def _translate(exc: Exception) -> InfluxError:
    """Map an influxdb-client / transport exception to our hierarchy."""
    status = getattr(exc, "status", None)
    if status in (401, 403):
        return InfluxAuthError(f"InfluxDB rejected the request (HTTP {status})")
    if status is not None:
        return InfluxError(f"InfluxDB error (HTTP {status})")
    name = type(exc).__name__
    looks_offline = (
        isinstance(exc, (ConnectionError, TimeoutError, OSError))
        or "Timeout" in name
        or "Connection" in name
    )
    if looks_offline:
        return InfluxUnavailableError(
            "InfluxDB unreachable — off the boat LAN, host down, or timed out"
        )
    return InfluxError(f"InfluxDB query failed: {exc}")
