"""Tests for passage_planner.weather.tropical with a mocked HTTP transport."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from passage_planner import parse_gpx
from passage_planner.errors import WeatherFetchError
from passage_planner.weather.tropical import (
    fetch_tropical_outlook,
    render_tropical_markdown,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

def _two_rss(body: str) -> str:
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel><item>'
        f"<description><![CDATA[{body}]]></description>"
        "</item></channel></rss>"
    )


_TWO_NO_FORMATION = _two_rss(
    "104 <br />TWOAT <br /><br />Tropical Weather Outlook<br />"
    "NWS National Hurricane Center Miami FL<br />For the North Atlantic:<br /><br />"
    "Tropical cyclone formation is not expected during the next 7 days.<br /><br />$$<br />"
)

_TWO_FORMATION_LIKELY = _two_rss(
    "Tropical Weather Outlook<br />NWS National Hurricane Center Miami FL<br /><br />"
    "1. Near the Bahamas: a broad area of low pressure is producing showers.<br />"
    "Formation chance through 48 hours...high...80 percent.<br />$$"
)


def _storms(*storms: dict) -> str:
    return json.dumps({"activeStorms": list(storms)})


def _storm(name: str, lat: float, lon: float, *, mdir: float | None = None,
           mspd: float | None = None, cls: str = "TS", intensity: str = "45") -> dict:
    return {
        "id": f"al99{name}", "name": name, "classification": cls,
        "intensity": intensity, "latitudeNumeric": lat, "longitudeNumeric": lon,
        "movementDir": mdir, "movementSpeed": mspd,
        "lastUpdate": "2026-09-15T15:00:00.000Z",
        "publicAdvisory": {"url": "https://www.nhc.noaa.gov/text/x.shtml"},
        "forecastGraphics": {"url": "https://www.nhc.noaa.gov/graphics_x.shtml"},
    }


def _run(storms_json: str, two_xml: str, **kw):
    def handler(request: httpx.Request) -> httpx.Response:
        if "CurrentStorms" in str(request.url):
            return httpx.Response(200, text=storms_json,
                                  headers={"content-type": "application/json"})
        return httpx.Response(200, text=two_xml)

    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")  # ~30.2 N, 89 W

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await fetch_tropical_outlook(route, client=http, **kw)

    return asyncio.run(go())


def test_no_storms_clear() -> None:
    o = _run(_storms(), _TWO_NO_FORMATION)
    assert o.systems == ()
    assert o.formation_expected is False
    assert o.clear is True
    assert "No tropical threat" in render_tropical_markdown(o)


def test_nearby_storm_outside_corridor_is_listed_but_not_flagged() -> None:
    # ~300 NM SE of the route: inside the relevance window, outside the corridor.
    o = _run(_storms(_storm("Offshore", 28.0, -84.0)), _TWO_NO_FORMATION)
    assert len(o.systems) == 1
    s = o.systems[0]
    assert s.in_corridor is False
    assert 200 < s.closest_nm < 600
    assert o.clear is True


def test_other_ocean_storm_is_dropped() -> None:
    o = _run(_storms(_storm("Faraway", 18.0, -140.0)), _TWO_NO_FORMATION)
    assert o.systems == ()
    assert o.clear is True


def test_close_storm_flags_corridor() -> None:
    # ~1 degree south of the route corridor -> well inside 200 NM.
    o = _run(_storms(_storm("Ontop", 29.2, -88.8, cls="HU", intensity="75")),
             _TWO_NO_FORMATION)
    s = o.systems[0]
    assert s.in_corridor is True
    assert s.closest_at_h == 0
    assert o.any_in_corridor is True
    md = render_tropical_markdown(o)
    assert "TROPICAL SYSTEM NEAR THE ROUTE" in md
    assert "Ontop" in md


def test_dead_reckoned_storm_enters_corridor() -> None:
    # Start ~300 NM SE of the route, tracking NW toward it at 20 kn.
    o = _run(
        _storms(_storm("Inbound", 26.0, -85.5, mdir=315.0, mspd=20.0)),
        _TWO_NO_FORMATION,
        lookahead_h=96,
        dr_step_h=12,
    )
    s = o.systems[0]
    assert s.in_corridor is True
    assert s.closest_at_h > 0  # closest approach is a projected position, not now


def test_formation_language_sets_flag() -> None:
    o = _run(_storms(), _TWO_FORMATION_LIKELY)
    assert o.formation_expected is True
    assert o.clear is False
    assert "80 percent" in o.atlantic_two_text


def test_http_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await fetch_tropical_outlook(route, client=http)

    with pytest.raises(WeatherFetchError):
        asyncio.run(go())
