"""Tests for passage_planner.notices with a mocked NGA transport."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from passage_planner import parse_gpx
from passage_planner.errors import NoticeFetchError, NoticeParseError
from passage_planner.notices import (
    fetch_nav_warnings,
    parse_issue_date,
    parse_positions,
    render_notices_markdown,
    screen_warnings,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# nola_to_gulfport.gpx runs ~30.2 N, 89.0 W.


def _warn(num: int, text: str, *, area: str = "4", sub: str = "28",
          issued: str = "011200Z JAN 2026") -> dict:
    return {
        "msgYear": 2026, "msgNumber": num, "navArea": area, "subregion": sub,
        "text": text, "status": "A", "issueDate": issued,
        "authority": "NGA", "cancelDate": None,
        "number": num, "year": 2026, "area": area,
    }


def _payload(*warns: dict) -> str:
    return json.dumps({"broadcast-warn": list(warns)})


# -- text parsing -------------------------------------------------


def test_parse_positions_deg_min() -> None:
    pos = parse_positions("KLEIN CURACAO LIGHT 11-59.4N 068-38.6W UNLIT.")
    assert len(pos) == 1
    assert pos[0].lat == round(11 + 59.4 / 60, 4)
    assert pos[0].lon == round(-(68 + 38.6 / 60), 4)


def test_parse_positions_multiple_and_hemispheres() -> None:
    text = "SHOAL 10-38.04N 071-36.00W AND WRECK 30-12.0N 089-00.0W."
    pos = parse_positions(text)
    assert len(pos) == 2
    assert pos[1].lat == 30.2 and pos[1].lon == -89.0


def test_parse_positions_none() -> None:
    assert parse_positions("CANCEL NAVAREA IV 489/24.") == ()


def test_parse_issue_date() -> None:
    dt = parse_issue_date("081653Z MAY 2024")
    assert dt is not None
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2024, 5, 8, 16, 53)
    assert parse_issue_date("garbage") is None
    assert parse_issue_date(None) is None


# -- corridor screening -----------------------------------------


def test_screen_keeps_in_corridor_drops_far_and_positionless() -> None:
    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")
    raws = [
        _warn(1, "GULF OF MEXICO.\nWRECK REPORTED IN 30-12.0N 089-00.0W."),   # on route
        _warn(2, "CARIBBEAN SEA.\nLIGHT 18-00.0N 076-00.0W UNLIT."),          # ~1000 NM
        _warn(3, "ADMIN. CANCEL NAVAREA IV 400/25."),                         # no position
    ]
    kept = screen_warnings(raws, route, corridor_nm=75.0)
    assert [w.number for w in kept] == [1]
    assert kept[0].closest_nm < 20
    assert kept[0].ident == "NAVAREA IV 1/26"


# -- fetch + render -------------------------------------------


def _run(payload: str, **kw):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload,
                              headers={"content-type": "application/json"})

    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await fetch_nav_warnings(route, client=http, **kw)

    return asyncio.run(go())


def test_fetch_filters_area_and_corridor() -> None:
    report = _run(_payload(
        _warn(10, "WRECK IN 30-12.0N 089-00.0W.", area="4"),
        _warn(11, "SHOAL IN 30-12.0N 089-00.0W.", area="P"),   # wrong area -> dropped
        _warn(12, "LIGHT 25-00.0N 080-00.0W UNLIT.", area="4"),  # far -> dropped
    ))
    assert report.total_in_force == 2          # the two navArea 4 ones
    assert [w.number for w in report.warnings] == [10]
    md = render_notices_markdown(report)
    assert "1 navigational warning" in md
    assert "NAVAREA IV 10/26" in md


def test_sar_broadcasts_split_from_hazards() -> None:
    report = _run(_payload(
        _warn(30, "GULF OF MEXICO.\nWRECK REPORTED IN 30-12.0N 089-00.0W.", area="4"),
        _warn(31, "GULF OF MEXICO.\nDISTRESS SIGNAL RECEIVED IN 30-12.0N 089-00.0W. "
                  "VESSELS KEEP A SHARP LOOKOUT.", area="4"),
    ))
    assert [w.number for w in report.hazards] == [30]
    assert [w.number for w in report.sar] == [31]
    md = render_notices_markdown(report)
    assert "1 navigational warning within" in md
    assert "search-and-rescue broadcast" in md
    assert "NAVAREA IV 31/26" in md


def test_fetch_clear_route() -> None:
    report = _run(_payload(_warn(20, "LIGHT 25-00.0N 080-00.0W UNLIT.", area="4")))
    assert report.warnings == ()
    md = render_notices_markdown(report)
    assert "No navigational warnings on the route" in md
    assert "USCG Local Notice to Mariners" in md  # pointer always rendered
    assert "navcen.uscg.gov" in md


def test_http_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await fetch_nav_warnings(route, client=http)

    try:
        asyncio.run(go())
    except NoticeFetchError:
        return
    raise AssertionError("expected NoticeFetchError")


def test_bad_json_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="{not json")

    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await fetch_nav_warnings(route, client=http)

    try:
        asyncio.run(go())
    except NoticeParseError:
        return
    raise AssertionError("expected NoticeParseError")


def test_render_unreachable() -> None:
    from datetime import UTC, datetime

    from passage_planner.notices import NoticeReport

    report = NoticeReport(
        route_name="x", generated=datetime.now(UTC), corridor_nm=75.0,
        areas_checked=("NAVAREA IV",), warnings=(), total_in_force=0, unreachable=True,
    )
    assert "PENDING" in render_notices_markdown(report)
