"""Tests for passage_planner.consular (State Department advisories + embassy blocks).

The payloads are small copies of the shapes the real feeds use (checked against
cadataapi.state.gov on 2026-09-21); no test touches the network."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from passage_planner.consular import (
    ADVISORIES_URL,
    ADVISORY_ATTEMPTS,
    COUNTRY_INFO_URL,
    INFO_ATTEMPTS,
    advisory_warnings,
    build_consular_report,
    country_key,
    lookup,
    parse_advisories,
    parse_country_info,
    render_consular_markdown,
)
from passage_planner.errors import ConsularParseError

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)

ADVISORIES: list[dict[str, Any]] = [
    {"Title": "The Bahamas - Level 2: Exercise Increased Caution",
     "Link": "https://travel.state.gov/x/bhs.html", "Published": "2026-07-27T20:00:00-04:00",
     "Summary": "<p>Exercise increased caution<p>in <b>The Bahamas</b> due to <b>crime</b>.</p>"
                "<h4>Crime</h4><p>More.</p>"},
    # the feed lists some countries twice; the newer one must win
    {"Title": "The Bahamas - Level 2: Exercise Increased Caution",
     "Link": "https://travel.state.gov/x/bhs-old.html", "Published": "2026-01-01T20:00:00-04:00",
     "Summary": "Old text."},
    {"Title": "Colombia - Level 3: Reconsider Travel",
     "Link": "https://travel.state.gov/x/col.html", "Published": "2026-03-30T20:00:00-04:00",
     "Summary": "Reconsider travel<p>to <b>Colombia</b> due to crime, terrorism. Some areas "
                "have an increased risk. Read the entire Travel Advisory.</p><p>Details.</p>"},
    {"Title": "Mexico Travel Advisory - Level 2: Exercise Increased Caution",
     "Link": "https://travel.state.gov/x/mex.html", "Published": "2026-05-28T20:00:00-04:00",
     "Summary": "Exercise increased caution in Mexico due to crime."},
    # some records open with an update note; the reason is the level-phrase line
    {"Title": "Jamaica - Level 2: Exercise Increased Caution",
     "Link": "https://travel.state.gov/x/jam.html", "Published": "2026-06-22T20:00:00-04:00",
     "Summary": "<p><i><b>There was no change to the advisory level.&nbsp; The &#8220;natural "
                "disaster&#8221; indicator was removed.</b></i></p>\n<p><b>Exercise Increased "
                "Caution </b>due to&nbsp;<b>crime and health </b>risks. Some areas have increased "
                "risk. Read the entire Travel Advisory.</p>\n<p><b>Reconsider travel</b> to:</p>"},
    {"Title": "Saint Lucia - Level 2: Exercise Increased Caution",
     "Link": "https://travel.state.gov/x/lca.html", "Published": "2026-07-09T20:00:00-04:00",
     "Summary": "Exercise increased caution in Saint Lucia due to crime."},
    {"Title": "Martinique - Level 1: Exercise Normal Precautions",
     "Link": "https://travel.state.gov/x/mtq.html", "Published": "2026-08-19T20:00:00-04:00",
     "Summary": "Exercise normal precautions in Martinique."},
    {"Title": "A record with no level in its title", "Link": "x", "Summary": "ignored"},
]


def _embassy_html(name: str, street: str, phone: str, web: str) -> str:
    return (
        f"<div>Embassy Name: {name} <br/>\r\n Street Address: {street}<br/>\r\n"
        f"Nassau, Somewhere <br/>\r\n Phone: {phone} <br/>\r\n Emergency Phone: {phone} <br/>\r\n"
        f" Fax: <br/>\r\n Email: acs@state.gov <br/>\r\n Web: {web} </div>"
    )


INFO: list[dict[str, Any]] = [
    {"geopoliticalarea": "Bahamas", "last_update_date": "Last Updated: September 17, 2026",
     "travel_embassyAndConsulate": _embassy_html(
         "U.S. Embassy Nassau", "235 Shirley Street", "+1-242-461-5025", "http://bs.usembassy.gov/")},
    {"geopoliticalarea": "Colombia", "last_update_date": "Last Updated: August 11, 2025",
     "travel_embassyAndConsulate": _embassy_html(
         "U.S. Embassy Bogota", "Calle 24 Bis", "+57 601 508-8165", "https://co.usembassy.gov/")},
    {"geopoliticalarea": "SaintLucia", "last_update_date": "Last Updated: July 1, 2026",
     "travel_embassyAndConsulate": _embassy_html(
         "U.S. Embassy Bridgetown", "Wildey", "+1-246-227-4000", "https://bb.usembassy.gov/")},
    {"geopoliticalarea": "Mexico", "last_update_date": "Last Updated: August 11, 2025",
     "travel_embassyAndConsulate": ""},
]


def _nosleep(seconds: float) -> None:
    pass


def _by_key() -> tuple[Any, Any]:
    return parse_advisories(ADVISORIES), parse_country_info(INFO)


# --- names ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ours", "theirs"),
    [
        ("Bahamas", "The Bahamas"),
        ("St Lucia", "SaintLucia"),
        ("Turks and Caicos", "Turks and Caicos Islands"),
        ("Curacao", "Curaçao"),
        ("Antigua & Barbuda", "AntiguaandBarbuda"),
        ("St Kitts and Nevis", "Saint Kitts and Nevis"),
    ],
)
def test_country_keys_meet_in_the_middle(ours: str, theirs: str) -> None:
    assert country_key(ours) == country_key(theirs)


# --- parsing -------------------------------------------------------------------


def test_advisories_dedupe_keep_newest_and_read_level_date_link_reason() -> None:
    adv = parse_advisories(ADVISORIES)
    b = adv[country_key("Bahamas")]
    assert (b.level, b.published) == (2, "2026-07-27")
    assert b.link == "https://travel.state.gov/x/bhs.html"
    assert b.level_text == "Exercise Increased Caution"
    # tags become spaces, so run-together words from the HTML are not fused
    assert b.headline == "Exercise increased caution in The Bahamas due to crime."
    col = adv[country_key("Colombia")]
    assert col.level == 3
    assert col.headline == (
        "Reconsider travel to Colombia due to crime, terrorism. Some areas have an increased risk."
    )
    jam = adv[country_key("Jamaica")]
    assert jam.headline == (
        "Exercise Increased Caution due to crime and health risks. Some areas have increased risk."
    )
    assert country_key("Mexico") in adv  # "Mexico Travel Advisory" title quirk
    assert len(adv) == 6  # the record with no level is skipped


def test_country_info_parses_the_embassy_block_and_updated_date() -> None:
    info = parse_country_info(INFO)
    b = info[country_key("Bahamas")]
    assert b["updated"] == "September 17, 2026"
    e = b["embassy"]
    assert e["name"] == "U.S. Embassy Nassau"
    assert e["address"] == "235 Shirley Street, Nassau, Somewhere"
    assert e["phone"] == "+1-242-461-5025" and e["email"] == "acs@state.gov"
    assert info[country_key("Mexico")]["embassy"] is None  # empty block


def test_bad_payload_shapes_are_reported() -> None:
    with pytest.raises(ConsularParseError):
        parse_advisories({"not": "a list"})
    with pytest.raises(ConsularParseError):
        parse_country_info("nope")


# --- lookup and rendering -------------------------------------------------------


def test_lookup_statuses_us_soil_and_unknown() -> None:
    adv, info = _by_key()
    res = {
        c.requested: c
        for c in lookup(
            ["Bahamas", "St Lucia", "Puerto Rico", "US Virgin Islands", "Martinique", "Atlantis"],
            adv, info,
        )
    }
    assert res["Bahamas"].status == "found" and res["Bahamas"].embassy is not None
    assert res["St Lucia"].advisory is not None and res["St Lucia"].embassy is not None
    assert res["Puerto Rico"].status == "us_soil" and res["US Virgin Islands"].status == "us_soil"
    assert res["Martinique"].advisory is not None and res["Martinique"].embassy is None
    assert res["Atlantis"].status == "not_found"


def test_render_flags_level_3_and_is_explicit_about_gaps() -> None:
    adv, info = _by_key()
    from passage_planner.consular import ConsularReport

    report = ConsularReport(
        countries=lookup(
            ["Colombia", "Bahamas", "Puerto Rico", "Martinique", "Atlantis"], adv, info
        ),
        fetched=NOW,
    )
    md = render_consular_markdown(report)
    assert "### U.S. Department of State: travel advisory and embassy" in md
    assert "\u26a0\ufe0f Colombia: Level 3 \u2014 Reconsider Travel** (issued 2026-03-30)" in md
    assert "**The Bahamas: Level 2 \u2014 Exercise Increased Caution** (issued 2026-07-27)" in md
    assert "\u26a0\ufe0f The Bahamas" not in md
    assert "U.S. embassy: U.S. Embassy Bogota; Calle 24 Bis" in md
    assert "Puerto Rico:** U.S. territory" in md and "Customs and Border Protection" in md
    assert "none listed" in md  # Martinique
    assert "no State Department record for this name" in md  # Atlantis
    assert "STEP" in md and "re-check before departure" in md
    assert advisory_warnings(report) == [
        "U.S. State Department advisory for Colombia: Level 3 (Reconsider Travel), "
        "issued 2026-03-30"
    ]


# --- fetching, cache, degradation ----------------------------------------------


def _client(calls: list[str], *, fail: bool = False) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if fail:
            raise httpx.ConnectError("offline")
        body = ADVISORIES if str(req.url) == ADVISORIES_URL else INFO
        assert str(req.url) in (ADVISORIES_URL, COUNTRY_INFO_URL)
        return httpx.Response(200, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_caches_and_a_fresh_cache_skips_the_network(tmp_path: Path) -> None:
    calls: list[str] = []
    r1 = build_consular_report(["Bahamas"], cache_dir=tmp_path, now=lambda: NOW,
                               sleep=_nosleep, client=_client(calls))
    assert sorted(calls) == sorted(
        [ADVISORIES_URL] * ADVISORY_ATTEMPTS + [COUNTRY_INFO_URL] * INFO_ATTEMPTS
    )
    assert r1.countries[0].advisory is not None and not r1.stale and not r1.unreachable
    cache = json.loads((tmp_path / "state_dept.json").read_text(encoding="utf-8"))
    assert set(cache) == {"fetched", "advisories", "info"}  # reduced, not the 6 MB feed

    calls.clear()
    r2 = build_consular_report(["Colombia"], cache_dir=tmp_path,
                               now=lambda: NOW + timedelta(hours=3), client=_client(calls))
    got = r2.countries[0].advisory
    assert calls == [] and got is not None and got.level == 3


def test_an_old_cache_is_refreshed_and_kept_as_stale_when_the_network_is_down(
    tmp_path: Path,
) -> None:
    build_consular_report(
        ["Bahamas"], cache_dir=tmp_path, now=lambda: NOW, sleep=_nosleep, client=_client([])
    )
    later = NOW + timedelta(days=2)
    down: list[str] = []
    report = build_consular_report(["Bahamas"], cache_dir=tmp_path, now=lambda: later,
                                   sleep=_nosleep, client=_client(down, fail=True))
    assert down  # it did try
    assert report.stale and not report.unreachable
    assert report.countries[0].advisory is not None
    assert "older copy" in render_consular_markdown(report)


def test_no_network_and_no_cache_gives_a_pending_block_not_an_error(tmp_path: Path) -> None:
    report = build_consular_report(["Bahamas", "Colombia"], cache_dir=tmp_path,
                                   now=lambda: NOW, sleep=_nosleep, client=_client([], fail=True))
    assert report.unreachable
    md = render_consular_markdown(report)
    assert "**PENDING**" in md and "travel.state.gov" in md
    assert advisory_warnings(report) == []


def test_http_errors_count_as_unreachable(tmp_path: Path) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    report = build_consular_report(
        ["Bahamas"], cache_dir=tmp_path, sleep=_nosleep, client=client
    )
    assert report.unreachable


def test_a_stale_edition_from_one_server_does_not_beat_the_current_one(tmp_path: Path) -> None:
    # 2026-09-21: the endpoint sometimes served an older edition (Colombia dated
    # 2025 with an old-style link). The newest record per country must win, in
    # whatever order the servers answer.
    stale_adv = [{**ADVISORIES[2], "Published": "2025-04-16T20:00:00-04:00",
                  "Link": "https://travel.state.gov/x/colombia-travel-advisory.html"}]
    stale_info = [{**INFO[1], "last_update_date": "Last Updated: April 1, 2025"}]
    n = {"adv": 0, "info": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        is_adv = str(req.url) == ADVISORIES_URL
        key = "adv" if is_adv else "info"
        n[key] += 1
        stale = n[key] == 1  # the FIRST answer is the old edition
        current = ADVISORIES if is_adv else INFO
        old = stale_adv if is_adv else stale_info
        return httpx.Response(200, json=old if stale else current)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    report = build_consular_report(["Colombia"], cache_dir=tmp_path, now=lambda: NOW,
                                   sleep=_nosleep, client=client)
    c = report.countries[0]
    assert c.advisory is not None and c.advisory.published == "2026-03-30"
    assert c.advisory.link.endswith("col.html")
    assert c.info_updated == "August 11, 2025"  # the newer of the two info records


def test_one_failed_attempt_is_tolerated_but_all_failing_is_unreachable(tmp_path: Path) -> None:
    calls = {"n": 0}

    def flaky(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] % 2 == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=ADVISORIES if str(req.url) == ADVISORIES_URL else INFO)

    client = httpx.Client(transport=httpx.MockTransport(flaky))
    report = build_consular_report(["Bahamas"], cache_dir=tmp_path, now=lambda: NOW,
                                   sleep=_nosleep, client=client)
    assert not report.unreachable and report.countries[0].advisory is not None


def test_a_429_burst_limit_is_retried_with_backoff(tmp_path: Path) -> None:
    # Cloudflare answers 429 to a burst (seen 2026-09-21); the third try succeeds.
    seen: dict[str, int] = {}
    slept: list[float] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        seen[url] = seen.get(url, 0) + 1
        if seen[url] <= 2:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, json=ADVISORIES if url == ADVISORIES_URL else INFO)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    report = build_consular_report(["Bahamas"], cache_dir=tmp_path, now=lambda: NOW,
                                   client=client, sleep=slept.append)
    assert not report.unreachable and report.countries[0].advisory is not None
    assert any(s >= 2.0 for s in slept)  # it backed off rather than hammering


def test_persistent_429_is_unreachable_not_a_crash(tmp_path: Path) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    report = build_consular_report(["Bahamas"], cache_dir=tmp_path, client=client,
                                   sleep=_nosleep)
    assert report.unreachable
