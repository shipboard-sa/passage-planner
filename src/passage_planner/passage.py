"""Turn a static route into a time-referenced passage plan.

A :class:`PassagePlan` pairs a :class:`~passage_planner.route.Route` with a
departure instant and a nominal speed, and produces the (position, time)
samples the weather layer needs. The nominal speed is a deliberate
simplification for the *first* forecast pull — the leg analyzer refines each
leg's real speed from the polar and the forecast, then the plan is re-timed
and (if the ETAs moved enough) the forecast re-fetched. See
`docs`/`Polar-Methodology` for why one pass is not enough.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .route import Leg, Route


@dataclass(frozen=True, slots=True)
class TimedFix:
    """A point on the route with an estimated time of arrival."""

    cumulative_nm: float
    lat: float
    lon: float
    eta: datetime  # timezone-aware, UTC
    leg_index: int


@dataclass(frozen=True, slots=True)
class TimedLeg:
    """A route leg with start/end times under the current speed estimate."""

    leg: Leg
    start_eta: datetime
    end_eta: datetime
    speed_kn: float

    @property
    def duration(self) -> timedelta:
        return self.end_eta - self.start_eta


@dataclass(frozen=True, slots=True)
class PassagePlan:
    route: Route
    depart_utc: datetime
    planning_speed_kn: float

    def __post_init__(self) -> None:
        if self.planning_speed_kn <= 0.0:
            raise ValueError("planning_speed_kn must be > 0")
        if self.depart_utc.tzinfo is None:
            raise ValueError("depart_utc must be timezone-aware (UTC)")

    @property
    def depart(self) -> datetime:
        return self.depart_utc.astimezone(UTC)

    def _eta_at(self, cumulative_nm: float, leg_speeds: dict[int, float] | None) -> datetime:
        """ETA at an along-route distance.

        ``leg_speeds`` maps leg index -> speed (kn) for a refined pass; any leg
        not present falls back to ``planning_speed_kn``. With no map, the whole
        route runs at the planning speed.
        """
        elapsed_h = 0.0
        remaining = cumulative_nm
        for leg in self.route.legs:
            speed = self.planning_speed_kn
            if leg_speeds is not None:
                speed = leg_speeds.get(leg.index, self.planning_speed_kn)
            leg_len = leg.distance_nm
            if remaining <= leg_len + 1e-9:
                elapsed_h += remaining / speed
                break
            elapsed_h += leg_len / speed
            remaining -= leg_len
        return self.depart + timedelta(hours=elapsed_h)

    def timed_legs(self, leg_speeds: dict[int, float] | None = None) -> list[TimedLeg]:
        out: list[TimedLeg] = []
        for leg in self.route.legs:
            speed = self.planning_speed_kn
            if leg_speeds is not None:
                speed = leg_speeds.get(leg.index, self.planning_speed_kn)
            start = self._eta_at(leg.cumulative_start_nm, leg_speeds)
            end = self._eta_at(leg.cumulative_end_nm, leg_speeds)
            out.append(TimedLeg(leg=leg, start_eta=start, end_eta=end, speed_kn=speed))
        return out

    def eta(self, leg_speeds: dict[int, float] | None = None) -> datetime:
        """Arrival time at the final waypoint."""
        return self._eta_at(self.route.total_nm, leg_speeds)

    def waypoint_etas(
        self, leg_speeds: dict[int, float] | None = None
    ) -> list[datetime]:
        """ETA at every waypoint, in route order (first == departure)."""
        etas = [self.depart]
        for leg in self.route.legs:
            etas.append(self._eta_at(leg.cumulative_end_nm, leg_speeds))
        return etas

    def sample_fixes(
        self, step_nm: float, leg_speeds: dict[int, float] | None = None
    ) -> list[TimedFix]:
        """Evenly spaced fixes along the route, each with an ETA.

        Endpoints are always included. Used to place forecast queries.
        """
        if step_nm <= 0.0:
            raise ValueError("step_nm must be > 0")
        distances: list[float] = []
        d = 0.0
        while d < self.route.total_nm:
            distances.append(d)
            d += step_nm
        distances.append(self.route.total_nm)

        fixes: list[TimedFix] = []
        for dist in distances:
            lat, lon = self.route.position_at_distance(dist)
            leg = self.route.leg_at_distance(dist)
            fixes.append(
                TimedFix(
                    cumulative_nm=dist,
                    lat=lat,
                    lon=lon,
                    eta=self._eta_at(dist, leg_speeds),
                    leg_index=leg.index,
                )
            )
        return fixes
