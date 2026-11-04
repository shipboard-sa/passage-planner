"""Forecast acquisition for route analysis."""

from __future__ import annotations

from .compare import (
    ForecastComparison,
    compare_route_forecasts,
    fetch_forecast_comparison,
    render_confidence_markdown,
)
from .field import Field, corridor_grid, fetch_field
from .model import ForecastPoint, RouteForecast
from .openmeteo import OpenMeteoClient, OpenMeteoConfig, fetch_route_forecast
from .tropical import (
    TropicalOutlook,
    TropicalSystem,
    fetch_tropical_outlook,
    fetch_tropical_outlook_sync,
    render_tropical_markdown,
)

__all__ = [
    "Field",
    "ForecastComparison",
    "ForecastPoint",
    "OpenMeteoClient",
    "OpenMeteoConfig",
    "RouteForecast",
    "TropicalOutlook",
    "TropicalSystem",
    "compare_route_forecasts",
    "corridor_grid",
    "fetch_field",
    "fetch_forecast_comparison",
    "fetch_route_forecast",
    "fetch_tropical_outlook",
    "fetch_tropical_outlook_sync",
    "render_confidence_markdown",
    "render_tropical_markdown",
]
