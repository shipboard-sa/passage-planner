"""Exception hierarchy for the Hylas routing framework.

Every module raises a subclass of :class:`RoutingError` so a caller can catch
the whole framework with one handler while still discriminating specific
failure modes. Nothing here inherits from anything but :class:`Exception`.
"""

from __future__ import annotations


class RoutingError(Exception):
    """Base class for every error raised by this package."""


class ConfigError(RoutingError):
    """Configuration could not be loaded, parsed, or validated."""


class ConfigFileError(ConfigError):
    """The configuration file is missing or is not readable/parseable TOML."""


class ConfigVersionError(ConfigError):
    """The file's ``config_version`` is absent or unsupported by this build."""


class ConfigValidationError(ConfigError):
    """The file parsed but a value is missing, out of range, or inconsistent."""


class RouteError(RoutingError):
    """A route file is missing, malformed, or geometrically invalid."""


class PolarError(RoutingError):
    """A polar file is missing or malformed."""


class InfluxError(RoutingError):
    """Base for InfluxDB read failures."""


class InfluxUnavailableError(InfluxError):
    """InfluxDB could not be reached (off the boat LAN, host down, timeout)."""


class InfluxAuthError(InfluxError):
    """InfluxDB rejected the token (401) or the request was out of scope (403)."""


class OpsPanelError(RoutingError):
    """The vessel-ops-panel maintenance DB is missing or unreadable."""


class PapersError(RoutingError):
    """The papers-of-record directory or overrides file could not be read."""


class FleetError(RoutingError):
    """The fleet list (boats travelling with the vessel) is invalid or unreadable."""


class HydroError(RoutingError):
    """Base for depth / tide data failures."""


class HydroFetchError(HydroError):
    """A NOAA CO-OPS or ENC endpoint was unreachable or returned an error."""


class WeatherError(RoutingError):
    """Base for forecast-acquisition failures."""


class WeatherFetchError(WeatherError):
    """A forecast provider was unreachable, timed out, or returned an error status."""


class WeatherParseError(WeatherError):
    """A forecast response was received but could not be interpreted."""


class FloatPlanError(RoutingError):
    """A float-plan config/crew file, or the PDF template, could not be used."""


class CommsError(RoutingError):
    """The comms.toml config could not be read or failed validation."""


class NoticeError(RoutingError):
    """Base for navigational-warning (NTM) acquisition failures."""


class NoticeFetchError(NoticeError):
    """The NGA Maritime Safety Information service was unreachable or errored."""


class NoticeParseError(NoticeError):
    """An NGA response was received but could not be interpreted."""


class RouteFileError(RouteError):
    """The GPX file is missing or is not readable/parseable XML."""


class RouteContentError(RouteError):
    """The GPX parsed but holds no usable route (no points, or a degenerate leg)."""


class CoastalError(RoutingError):
    """Base for coastline-distance lookup failures."""


class CoastalDataError(CoastalError):
    """The bundled coastline dataset is missing/unreadable, or the query point
    falls outside its coverage."""


class ResearchError(RoutingError):
    """Base for the automated shore-logistics research feature."""


class ResearchUnavailableError(ResearchError):
    """The research feature cannot run here: the Claude Code CLI is missing or
    not configured. Raised before any work starts, never mid-run."""


class ResearchOutputError(ResearchError):
    """The model's reply is not a usable port section (no heading, no field
    bullets). The raw reply is available as ``raw``."""

    def __init__(self, message: str, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


class MissingSecretError(ConfigError):
    """A required secret's environment variable is unset or empty.

    The offending environment variable name is available as ``env_var`` so a
    launcher can print an actionable message.
    """

    def __init__(self, env_var: str, purpose: str) -> None:
        self.env_var = env_var
        self.purpose = purpose
        super().__init__(
            f"environment variable {env_var!r} is not set "
            f"(needed for: {purpose})"
        )


class ArrivalPlaceError(ConfigError):
    """The arrival-places pick list (``data/arrival_places.toml``) is malformed."""


class ConsularError(RoutingError):
    """Base for the State Department advisory/embassy lookup."""


class ConsularFetchError(ConsularError):
    """The State Department feeds were unreachable or returned an error."""


class ConsularParseError(ConsularError):
    """A State Department feed was not the JSON shape we expect."""


class ActiveCaptainError(RoutingError):
    """Base for Garmin ActiveCaptain Community data failures."""


class ActiveCaptainFetchError(ActiveCaptainError):
    """The ActiveCaptain API was unreachable, timed out, or returned an error."""


class ActiveCaptainDataError(ActiveCaptainError):
    """A downloaded tile database is missing, corrupt, or has an unexpected schema."""
