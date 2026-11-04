"""Tests for passage_planner.arrival (the pick-list schema and loader), split_ports,
and the /api/arrival-places endpoints."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from passage_planner import arrival
from passage_planner.errors import ArrivalPlaceError
from passage_planner.logistics import split_ports

GOOD = """
[[place]]
id = "gd-tyrell-bay"
name = "Tyrell Bay"
island = "Carriacou"
country = "Grenada"
region = "Windward Islands"
lat = 12.4667
lon = -61.4833
kind = "port_of_entry"
clearance = "Customs and immigration on the dinghy dock."
also_known_as = ["Tyrrel Bay"]
sources = ["https://example.org/a"]
verified = "2026-09-21"

[[place]]
id = "bs-nassau"
name = "Nassau"
island = "New Providence"
country = "Bahamas"
lat = 25.0833
lon = -77.35
kind = "port_of_entry"
clearance = "Clear in at the port."
sources = ["https://example.org/b"]
verified = "2026-09-21"
"""


def _bad(**over: object) -> str:
    entry = {
        "id": '"gd-x"', "name": '"X"', "country": '"Grenada"', "lat": "12.4", "lon": "-61.4",
        "kind": '"harbour"', "clearance": '"c"', "sources": '["https://e.org"]',
        "verified": '"2026-09-21"',
    }
    entry.update({k: str(v) for k, v in over.items()})
    return "[[place]]\n" + "\n".join(f"{k} = {v}" for k, v in entry.items() if v != "None") + "\n"


def test_good_file_loads_with_labels_and_sorting() -> None:
    places = arrival.parse_places(GOOD)
    assert {p.id for p in places} == {"gd-tyrell-bay", "bs-nassau"}
    by_id = {p.id: p for p in places}
    assert by_id["gd-tyrell-bay"].label == "Tyrell Bay, Carriacou (Grenada)"
    assert by_id["bs-nassau"].label == "Nassau, New Providence (Bahamas)"
    assert by_id["gd-tyrell-bay"].also_known_as == ("Tyrrel Bay",)


def test_label_omits_the_island_when_it_is_the_name_or_blank() -> None:
    p = arrival.parse_places(_bad(name='"Nassau"', island='"Nassau"', id='"gd-nassau"'))[0]
    assert p.label == "Nassau (Grenada)"
    q = arrival.parse_places(_bad())[0]
    assert q.label == "X (Grenada)"
    # an island that just repeats the country adds nothing either
    r = arrival.parse_places(
        _bad(name='"Prickly Bay"', island='"Grenada"', id='"gd-prickly-bay"')
    )[0]
    assert r.label == "Prickly Bay (Grenada)"


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        ({"id": '"Grenada-x"'}, "id must look like"),
        ({"id": '"zz-x"'}, "not a known country code"),
        ({"country": '"Bahamas"'}, "country must be 'Grenada'"),
        ({"kind": '"marina"'}, "kind must be one of"),
        ({"lat": "95"}, "out of range"),
        ({"lat": "45"}, "outside the Caribbean"),
        ({"lon": "61.4"}, "outside the Caribbean"),
        ({"lat": "13.9"}, "outside the box for Grenada"),
        ({"lon": "-62.5"}, "outside the box for Grenada"),
        ({"sources": "[]"}, "missing 'sources'"),
        ({"sources": '["not a url"]'}, r"http\(s\) URLs"),
        ({"verified": '"yesterday"'}, "ISO date"),
        ({"clearance": '""'}, "missing 'clearance'"),
    ],
)
def test_validation_names_the_problem(over: dict[str, str], needle: str) -> None:
    with pytest.raises(ArrivalPlaceError, match=needle):
        arrival.parse_places(_bad(**over))


def test_duplicate_ids_and_labels_are_rejected() -> None:
    with pytest.raises(ArrivalPlaceError, match="duplicate id"):
        arrival.parse_places(_bad() + _bad(name='"Y"'))
    with pytest.raises(ArrivalPlaceError, match="duplicate label"):
        arrival.parse_places(_bad() + _bad(id='"gd-x2"'))


def test_bad_toml_and_missing_file() -> None:
    with pytest.raises(ArrivalPlaceError, match="not valid TOML"):
        arrival.parse_places("[[place]\nx=")
    assert arrival.load_places(Path("no-such-file.toml")) == []


def test_nearest_ranks_by_distance_and_can_cap_it() -> None:
    places = arrival.parse_places(GOOD)
    near = arrival.nearest(places, 12.5, -61.5, n=5)
    assert near[0][0].id == "gd-tyrell-bay" and near[0][1] < 5
    assert len(near) == 2
    assert [p.id for p, _ in arrival.nearest(places, 12.5, -61.5, max_nm=100)] == ["gd-tyrell-bay"]


def test_dump_round_trips_and_sorts() -> None:
    places = arrival.parse_places(GOOD)
    text = arrival.dump_places(places, header="Purpose: x\nsecond line")
    assert text.startswith("# Purpose: x\n# second line\n\n[[place]]")
    again = arrival.parse_places(text)
    assert sorted(p.id for p in again) == sorted(p.id for p in places)
    assert {p.id: p for p in again} == {p.id: p for p in places}
    assert [p.country for p in again] == sorted(p.country for p in again)


def test_dump_keeps_accents_and_quotes() -> None:
    # TOML source with an escaped accent and escaped quotes in the name
    toml_name = r"Bogot\u00e1 \"Bay\""
    weird = GOOD.replace("Tyrell Bay", toml_name).replace("gd-tyrell-bay", "gd-x")
    p = arrival.parse_places(weird)
    assert p[0].name == 'Bogot\u00e1 "Bay"'
    back = {x.id: x for x in arrival.parse_places(arrival.dump_places(p))}
    assert back["gd-x"].name == 'Bogot\u00e1 "Bay"'


def test_close_pairs_finds_a_place_entered_twice() -> None:
    dup = GOOD + GOOD.split("[[place]]")[1].replace("gd-tyrell-bay", "gd-tyrell-bay-2").join(
        ["[[place]]", ""]
    ).replace("Tyrell Bay", "Tyrrel Bay")
    places = arrival.parse_places(dup)
    pairs = arrival.close_pairs(places)
    assert len(pairs) == 1 and pairs[0][2] < 0.01


def test_merge_command_writes_one_sorted_file_and_rejects_cross_file_duplicates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a, b = tmp_path / "a.toml", tmp_path / "b.toml"
    first, second = GOOD.split("[[place]]")[1:3]
    a.write_text("[[place]]" + first, encoding="utf-8")
    b.write_text("[[place]]" + second, encoding="utf-8")
    out = tmp_path / "merged.toml"
    assert arrival._main(["merge", str(out), str(a), str(b)]) == 0
    assert [p.id for p in arrival.load_places(out)] == ["bs-nassau", "gd-tyrell-bay"]
    assert out.read_text(encoding="utf-8").startswith("# Purpose:")
    # the same place in two files is an error, not a silent overwrite
    c = tmp_path / "c.toml"
    c.write_text("[[place]]" + first, encoding="utf-8")
    assert arrival._main(["merge", str(tmp_path / "x.toml"), str(a), str(c)]) == 1
    assert "duplicate id" in capsys.readouterr().out
    assert not (tmp_path / "x.toml").exists()


def test_the_shipped_data_file_is_valid_and_sorted() -> None:
    places = arrival.load_places()  # data/arrival_places.toml, empty until researched
    keys = [(p.country, p.island, p.name) for p in places]
    assert keys == sorted(keys)


def test_split_ports_keeps_commas_inside_picked_names() -> None:
    assert split_ports("West End, George Town") == ["West End", "George Town"]
    assert split_ports("Tyrell Bay, Carriacou (Grenada)\nNassau, New Providence (Bahamas)") == [
        "Tyrell Bay, Carriacou (Grenada)", "Nassau, New Providence (Bahamas)",
    ]
    assert split_ports("A; B ;A\n") == ["A", "B"]
    assert split_ports(" , ") == []


# --- the HTTP endpoints ---------------------------------------------------------

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import backend.main as backend  # noqa: E402

GPX = (ROOT / "tests" / "fixtures" / "nola_to_gulfport.gpx").read_bytes()


def test_endpoint_serves_the_places(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend, "load_places", lambda: arrival.parse_places(GOOD))
    body = TestClient(backend.app).get("/api/arrival-places").json()
    assert [p["label"] for p in body["places"]] == [
        "Tyrell Bay, Carriacou (Grenada)", "Nassau, New Providence (Bahamas)",
    ]
    assert body["places"][0]["country"] == "Grenada"


def test_endpoint_reports_a_broken_data_file(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> list[arrival.ArrivalPlace]:
        raise ArrivalPlaceError("arrival_places.toml: duplicate id 'x'")

    monkeypatch.setattr(backend, "load_places", boom)
    resp = TestClient(backend.app).get("/api/arrival-places")
    assert resp.status_code == 500 and "duplicate id" in resp.json()["detail"]


def test_suggest_returns_nearest_to_route_end(monkeypatch: pytest.MonkeyPatch) -> None:
    # nola_to_gulfport ends near 30.4 N 89.1 W; put a place there and one far away.
    near = (
        GOOD.replace("12.4667", "30.35").replace("-61.4833", "-89.09")
        .replace("gd-tyrell-bay", "us-gulfport").replace('"Grenada"', '"United States"')
    )
    monkeypatch.setattr(backend, "load_places", lambda: arrival.parse_places(near))
    resp = TestClient(backend.app).post(
        "/api/arrival-places/suggest", files={"route": ("r.gpx", GPX)}
    )
    assert resp.status_code == 200
    ends = resp.json()["end"]
    assert ends and ends[0]["id"] == "us-gulfport" and ends[0]["distance_nm"] < 60
    assert all(e["id"] != "bs-nassau" for e in ends)  # beyond the 60 NM cap


def test_every_country_has_a_box_and_boxes_are_sane() -> None:
    assert set(arrival.COUNTRY_BOXES) == set(arrival.COUNTRIES)
    for code, (lat0, lat1, lon0, lon1) in arrival.COUNTRY_BOXES.items():
        assert lat0 < lat1 and lon0 < lon1, code
        assert lat0 >= 5 and lat1 <= 35 and lon0 >= -100 and lon1 <= -55, code
