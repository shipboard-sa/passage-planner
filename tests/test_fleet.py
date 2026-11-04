"""Tests for passage_planner.fleet (the saved list of boats that may travel with us)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from passage_planner import fleet
from passage_planner.config import AppConfig, BuddyBoat, boat_id, load
from passage_planner.errors import FleetError

ROOT = Path(__file__).resolve().parents[1]


def _cat() -> dict[str, Any]:
    return {
        "name": "SV Salt Cat", "hull": "catamaran", "make": "Lagoon", "model": "45",
        "draft_ft": 4.0, "air_draft_ft": 62.0, "loa_ft": 45, "beam_ft": 24,
        "usual_companion": True,
    }


def _mono() -> dict[str, Any]:
    return {
        "name": "SV Blue Mono", "make": "Beneteau", "model": "Oceanis 46.1",
        "draft_ft": 6.4, "air_draft_ft": 66.0,
    }


def _app(tmp_path: Path) -> AppConfig:
    app = load(ROOT / "config")
    return replace(app, config_dir=tmp_path)


# --- ids and descriptions ---------------------------------------------------


def test_boat_id_is_a_slug() -> None:
    assert boat_id("SV Example Buddy") == "sv_example_buddy"
    assert boat_id("  S/V  Blue-Mono!! ") == "sv_blue_mono"
    assert boat_id("!!!") == ""


def test_description_shows_make_model_and_hull() -> None:
    b = fleet.boat_from_dict(_mono())
    assert b.description == "Beneteau Oceanis 46.1, monohull"
    assert fleet.boat_from_dict({"name": "X", "draft_ft": 5, "air_draft_ft": 50}).description == (
        "monohull"
    )
    assert not b.is_multihull and fleet.boat_from_dict(_cat()).is_multihull


# --- validation ----------------------------------------------------------------


def test_minimal_boat_needs_name_and_the_two_drafts() -> None:
    b = fleet.boat_from_dict({"name": "Tiny", "draft_ft": 5, "air_draft_ft": 50})
    assert b.hull == "monohull" and not b.lifting_keel and b.draft_up_ft is None
    for missing in ("name", "draft_ft", "air_draft_ft"):
        data = {"name": "Tiny", "draft_ft": 5, "air_draft_ft": 50}
        del data[missing]
        with pytest.raises(FleetError):
            fleet.boat_from_dict(data)


@pytest.mark.parametrize(
    "patch, message",
    [
        ({"hull": "raft"}, "hull must be one of"),
        ({"draft_ft": 0}, "draft ft must be above 0"),
        ({"draft_ft": "deep"}, "must be a number"),
        ({"air_draft_ft": 500}, "at most"),
        ({"mmsi": "12345"}, "9 digits"),
        ({"name": "!!!"}, "at least one letter"),
        ({"polar_file": "../../secrets.pol"}, "plain relative path"),
        ({"polar_file": "/etc/passwd"}, "plain relative path"),
        ({"lifting_keel": True}, "needs its keel-up draft"),
        ({"lifting_keel": True, "draft_up_ft": 20, "draft_ft": 14}, "less than the keel-down"),
    ],
)
def test_invalid_boats_are_refused_with_a_useful_message(
    patch: dict[str, Any], message: str
) -> None:
    data = {"name": "SV Deep", "draft_ft": 14.0, "air_draft_ft": 90.0, **patch}
    with pytest.raises(FleetError, match=message):
        fleet.boat_from_dict(data)


def test_call_sign_is_uppercased_and_polar_file_round_trips() -> None:
    b = fleet.boat_from_dict({
        "name": "SV Example Buddy", "draft_ft": 14, "air_draft_ft": 90,
        "call_sign": "xyz0000", "polar_file": "polars/sv_example_buddy.pol",
    })
    assert b.call_sign == "XYZ0000"
    assert b.polar_file == "polars/sv_example_buddy.pol"
    assert fleet.boat_to_dict(b)["call_sign"] == "XYZ0000"


def test_lifting_keel_keeps_both_drafts_and_a_non_lifting_boat_drops_the_up_draft() -> None:
    lifting = fleet.boat_from_dict({
        "name": "SV Example Buddy", "draft_ft": 14, "draft_up_ft": 8, "lifting_keel": True,
        "air_draft_ft": 90,
    })
    assert lifting.keel_up_known and lifting.effective_shallow_draft_ft == 8
    fixed = fleet.boat_from_dict({
        "name": "SV Fixed", "draft_ft": 6, "draft_up_ft": 3, "air_draft_ft": 60,
    })
    assert fixed.draft_up_ft is None and fixed.effective_shallow_draft_ft == 6


# --- the saved file ------------------------------------------------------------------


def test_missing_file_means_the_vessel_toml_boats_are_the_fleet(tmp_path: Path) -> None:
    app = _app(tmp_path)
    seeded = fleet.load_fleet(fleet.library_path(tmp_path), app.vessel.buddy_boats)
    assert [b.name for b in seeded] == ["SV Example Buddy"]
    assert seeded[0].usual_companion  # a vessel.toml boat has always travelled with us


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    path = fleet.library_path(tmp_path)
    boats = [fleet.boat_from_dict(_mono()), fleet.boat_from_dict(_cat())]
    fleet.save_fleet(path, boats)
    assert not path.with_suffix(".tmp").exists()
    loaded = fleet.load_fleet(path)
    assert {b.id for b in loaded} == {"sv_blue_mono", "sv_salt_cat"}
    cat = next(b for b in loaded if b.id == "sv_salt_cat")
    assert cat.hull == "catamaran" and cat.beam_ft == 24 and cat.usual_companion


def test_once_saved_the_file_replaces_the_seed(tmp_path: Path) -> None:
    app = _app(tmp_path)
    fleet.save_fleet(fleet.library_path(tmp_path), [fleet.boat_from_dict(_mono())])
    boats = fleet.load_fleet(fleet.library_path(tmp_path), app.vessel.buddy_boats)
    assert [b.name for b in boats] == ["SV Blue Mono"]


def test_corrupt_file_raises_a_fleet_error(tmp_path: Path) -> None:
    path = fleet.library_path(tmp_path)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(FleetError, match="cannot read the fleet list"):
        fleet.load_fleet(path)
    path.write_text(json.dumps({"boats": [{"name": "X"}]}), encoding="utf-8")
    with pytest.raises(FleetError):
        fleet.load_fleet(path)


def test_unknown_keys_in_a_saved_boat_are_ignored(tmp_path: Path) -> None:
    path = fleet.library_path(tmp_path)
    boat = {**_mono(), "future_field": "x", "id": "sv_blue_mono"}
    path.write_text(json.dumps({"boats": [boat]}), encoding="utf-8")
    assert fleet.load_fleet(path)[0].name == "SV Blue Mono"


# --- editing ---------------------------------------------------------------------------


def test_upsert_adds_updates_and_renames() -> None:
    mono, cat = fleet.boat_from_dict(_mono()), fleet.boat_from_dict(_cat())
    boats = fleet.upsert([], mono)
    boats = fleet.upsert(boats, cat)
    assert len(boats) == 2
    taller = replace(mono, air_draft_ft=70.0)
    boats = fleet.upsert(boats, taller)
    assert len(boats) == 2 and next(b for b in boats if b.id == mono.id).air_draft_ft == 70
    renamed = replace(mono, name="SV Blue Mono II")
    boats = fleet.upsert(boats, renamed, replaces=mono.id)
    assert {b.id for b in boats} == {"sv_blue_mono_ii", "sv_salt_cat"}


def test_upsert_refuses_a_name_that_collides_with_another_boat() -> None:
    mono, cat = fleet.boat_from_dict(_mono()), fleet.boat_from_dict(_cat())
    with pytest.raises(FleetError, match="already a boat called"):
        fleet.upsert([mono, cat], replace(cat, name="SV Blue Mono"), replaces=cat.id)


def test_remove() -> None:
    mono, cat = fleet.boat_from_dict(_mono()), fleet.boat_from_dict(_cat())
    assert fleet.remove([mono, cat], mono.id) == [cat]
    with pytest.raises(FleetError, match="no boat with id"):
        fleet.remove([mono], "nobody")


# --- choosing boats for a passage --------------------------------------------------------


def test_parse_ids_distinguishes_default_solo_and_a_choice() -> None:
    assert fleet.parse_ids(None) is None
    assert fleet.parse_ids("") == [] and fleet.parse_ids("  ") == [] and fleet.parse_ids("[]") == []
    assert fleet.parse_ids('["SV Blue Mono", "sv_salt_cat"]') == ["sv_blue_mono", "sv_salt_cat"]
    assert fleet.parse_ids("SV Blue Mono, SV Salt Cat") == ["sv_blue_mono", "sv_salt_cat"]
    for bad in ('["a", 3]', "[not json", '{"a": 1}'):
        with pytest.raises(FleetError):
            fleet.parse_ids(bad)


def test_select_default_solo_and_named() -> None:
    mono, cat = fleet.boat_from_dict(_mono()), fleet.boat_from_dict(_cat())  # cat is "usual"
    boats = [mono, cat]
    assert fleet.select(boats, None) == (cat,)
    assert fleet.select(boats, []) == ()
    assert fleet.select(boats, ["sv_blue_mono", "SV Blue Mono"]) == (mono,)  # repeats dropped
    with pytest.raises(FleetError, match="no boat"):
        fleet.select(boats, ["ghost"])


def test_apply_fleet_replaces_the_vessel_toml_buddy_boats(tmp_path: Path) -> None:
    app = _app(tmp_path)
    assert [b.name for b in fleet.apply_fleet(app).vessel.buddy_boats] == ["SV Example Buddy"]
    assert fleet.apply_fleet(app, []).vessel.buddy_boats == ()  # solo, even with a seed
    fleet.save_fleet(fleet.library_path(tmp_path), [
        fleet.boat_from_dict(_cat()), fleet.boat_from_dict(_mono()),
    ])
    chosen = fleet.apply_fleet(app, ["sv_blue_mono"]).vessel.buddy_boats
    assert [b.name for b in chosen] == ["SV Blue Mono"]
    # the original app is untouched
    assert [b.name for b in app.vessel.buddy_boats] == ["SV Example Buddy"]


def test_shipped_config_seed_has_example_buddy_as_a_usual_monohull() -> None:
    sb = load(ROOT / "config").vessel.buddy_boats[0]
    assert isinstance(sb, BuddyBoat) and sb.id == "sv_example_buddy"
    assert sb.hull == "monohull" and sb.usual_companion
