"""Model-divergence check for the briefing's forecast-confidence section.

Purpose: Compare GFS vs ECMWF per leg and render a confidence verdict for
    briefing section 3c.
Version: v2 (2026-09-22) - trimmed verdict wording to terse facts, no
    commentary.
Scope: PRODUCTION - feeds briefing.py section 3c of passage-planner.
Architecture: passage-planner weather submodule. render_confidence_markdown()
    is called by briefing.py's _weather(); ForecastComparison is built by
    service.py when --compare is passed, pulling both models from Open-Meteo.

Two global models (GFS and ECMWF by default) are pulled for the same route
fixes and compared leg by leg. Where they agree, the forecast is trustworthy;
where they diverge, that leg's timing is soft and the go/no-go leans on the
NWS human-forecaster text instead.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

from ..passage import TimedFix
from .model import RouteForecast
from .openmeteo import OpenMeteoClient, OpenMeteoConfig

DEFAULT_MODELS: tuple[str, str] = ("gfs_seamless", "ecmwf_ifs025")

# Confidence thresholds on the worst leg.
_HIGH_WIND_KN, _HIGH_DIR_DEG = 5.0, 25.0
_MOD_WIND_KN, _MOD_DIR_DEG = 10.0, 45.0


@dataclass(frozen=True, slots=True)
class LegDivergence:
    leg_index: int
    wind_speed_delta_kn: float
    wind_dir_delta_deg: float
    a_speed_kn: float | None
    b_speed_kn: float | None
    a_dir_deg: float | None
    b_dir_deg: float | None


@dataclass(frozen=True, slots=True)
class ForecastComparison:
    model_a: str
    model_b: str
    issued: datetime
    mean_wind_delta_kn: float
    max_wind_delta_kn: float
    max_dir_delta_deg: float
    worst_leg: LegDivergence | None
    confidence: str  # "high" | "moderate" | "low"

    @property
    def agree(self) -> bool:
        return self.confidence == "high"


def _circular_delta(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def compare_route_forecasts(a: RouteForecast, b: RouteForecast) -> ForecastComparison:
    """Compare two forecasts for the same route fixes (order-aligned)."""
    pairs = list(zip(a.points, b.points, strict=False))
    if not pairs:
        raise ValueError("nothing to compare")

    speed_deltas: list[float] = []
    per_leg: dict[int, LegDivergence] = {}

    for pa, pb in pairs:
        s_delta = (
            abs(pa.wind_speed_kn - pb.wind_speed_kn)
            if pa.wind_speed_kn is not None and pb.wind_speed_kn is not None
            else 0.0
        )
        d_delta = (
            _circular_delta(pa.wind_from_deg, pb.wind_from_deg)
            if pa.wind_from_deg is not None and pb.wind_from_deg is not None
            else 0.0
        )
        speed_deltas.append(s_delta)
        cur = per_leg.get(pa.leg_index)
        if cur is None or (s_delta + d_delta / 10.0) > (
            cur.wind_speed_delta_kn + cur.wind_dir_delta_deg / 10.0
        ):
            per_leg[pa.leg_index] = LegDivergence(
                leg_index=pa.leg_index,
                wind_speed_delta_kn=round(s_delta, 1),
                wind_dir_delta_deg=round(d_delta, 0),
                a_speed_kn=pa.wind_speed_kn,
                b_speed_kn=pb.wind_speed_kn,
                a_dir_deg=pa.wind_from_deg,
                b_dir_deg=pb.wind_from_deg,
            )

    max_wind = max(speed_deltas)
    mean_wind = sum(speed_deltas) / len(speed_deltas)
    max_dir = max((leg.wind_dir_delta_deg for leg in per_leg.values()), default=0.0)
    worst = max(
        per_leg.values(),
        key=lambda leg: leg.wind_speed_delta_kn + leg.wind_dir_delta_deg / 10.0,
        default=None,
    )

    if max_wind <= _HIGH_WIND_KN and max_dir <= _HIGH_DIR_DEG:
        confidence = "high"
    elif max_wind <= _MOD_WIND_KN and max_dir <= _MOD_DIR_DEG:
        confidence = "moderate"
    else:
        confidence = "low"

    return ForecastComparison(
        model_a=a.points[0].model,
        model_b=b.points[0].model,
        issued=a.issued,
        mean_wind_delta_kn=round(mean_wind, 1),
        max_wind_delta_kn=round(max_wind, 1),
        max_dir_delta_deg=round(max_dir, 0),
        worst_leg=worst,
        confidence=confidence,
    )


@dataclass
class _CompareClient:
    config: OpenMeteoConfig = field(default_factory=OpenMeteoConfig)

    async def run(
        self, fixes: list[TimedFix], route_name: str, models: tuple[str, ...]
    ) -> ForecastComparison:
        client = OpenMeteoClient(config=self.config)
        forecasts = await client.fetch_wind_models(fixes, route_name, models)
        a, b = forecasts[models[0]], forecasts[models[1]]
        return compare_route_forecasts(a, b)


def fetch_forecast_comparison(
    fixes: list[TimedFix],
    route_name: str,
    *,
    models: tuple[str, str] = DEFAULT_MODELS,
    config: OpenMeteoConfig | None = None,
) -> ForecastComparison:
    """Blocking: pull two models in one request and compare them."""
    return asyncio.run(
        _CompareClient(config=config or OpenMeteoConfig()).run(fixes, route_name, models)
    )


def render_confidence_markdown(cmp: ForecastComparison) -> str:
    verdict = {
        "high": "**Models agree.** Leg timings below are firm.",
        "moderate": "**Some model spread.** Leg timings ±a few hours — confirm against "
                    "the NWS text forecast.",
        "low": "⚠️ **Models diverge.** Do not commit on the model picture alone — the "
               "NWS Offshore Waters text forecast governs.",
    }[cmp.confidence]
    lines = [
        verdict,
        "",
        f"- {cmp.model_a} vs {cmp.model_b}: mean wind Δ **{cmp.mean_wind_delta_kn:.1f} kn**, "
        f"worst **{cmp.max_wind_delta_kn:.1f} kn** / **{cmp.max_dir_delta_deg:.0f}°**.",
    ]
    w = cmp.worst_leg
    if w is not None and (w.wind_speed_delta_kn >= 3.0 or w.wind_dir_delta_deg >= 20.0):
        a_s = f"{w.a_speed_kn:.0f}" if w.a_speed_kn is not None else "?"
        b_s = f"{w.b_speed_kn:.0f}" if w.b_speed_kn is not None else "?"
        a_d = f"{w.a_dir_deg:.0f}" if w.a_dir_deg is not None else "?"
        b_d = f"{w.b_dir_deg:.0f}" if w.b_dir_deg is not None else "?"
        lines.append(
            f"- Biggest split at **leg {w.leg_index}**: "
            f"{cmp.model_a} {a_s} kn @ {a_d}°, {cmp.model_b} {b_s} kn @ {b_d}°."
        )
    return "\n".join(lines)
