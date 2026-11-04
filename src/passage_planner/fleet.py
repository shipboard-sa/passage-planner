"""The fleet list: boats that may travel with the vessel, and which ones a passage uses.

Purpose: Keep one saved list of companion boats (hull type, make/model, draft, air
    draft, ...), validate what the web form saves, and turn a passage's "travelling
    with" choice into the buddy boats the rest of the tool already understands.
Version: v2 (2026-09-22) - boats gained `call_sign` and `polar_file` (a real
    per-boat polar, see boat_lookup.py); `polar_file` is only ever set by the
    server after parsing a lookup paste, never accepted verbatim from the form.
Scope: PRODUCTION - feeds every briefing section that mentions the fleet (2, 5, 9),
    the depth check and the shore-logistics research prompt.
Architecture: passage-planner support module. One JSON file, ``config/fleet.json``,
    written only through the web form's "Travelling with" screen (never hand-edited,
    same idea as ``depth_soundings.json``). ``[[buddy_boat]]`` tables in
    ``vessel.toml`` are only the seed for a fleet that has never been saved. Called
    by service.run_brief() and cli.py (--fleet / --solo) and by backend/main.py
    (/api/fleet and the ``fleet`` form field of the brief, depth-check and research
    endpoints). Pure functions plus that one file; the boats themselves are
    :class:`passage_planner.config.BuddyBoat`.

Decisions worth knowing:

* **Arrival, departure and anchoring use the keel-UP draft.** A lifting-keel boat's
  keel-down draft is for open water only, so it is stored but never governs a stop.
* **Selection is per passage.** ``None`` means "the boats marked as usual
  companions", an empty list means "travelling alone", a list of ids means exactly
  those boats.
* **Once saved, the file is the fleet.** Until then the ``vessel.toml`` boats are it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .config import HULLS, AppConfig, BuddyBoat, boat_id
from .errors import FleetError

LIBRARY_NAME = "fleet.json"

_MAX_DRAFT_FT = 30.0
_MAX_AIR_DRAFT_FT = 200.0
_MAX_LOA_FT = 300.0
_MAX_BEAM_FT = 80.0
_MAX_DISPLACEMENT_LB = 2_000_000.0
_TEXT_LIMITS = {
    "name": 80, "make": 60, "model": 60, "notes": 600, "call_sign": 10, "polar_file": 200,
}
_FIELDS = tuple(asdict(BuddyBoat("x", 1.0, 1.0)))


def library_path(config_dir: Path) -> Path:
    return config_dir / LIBRARY_NAME


def boat_to_dict(boat: BuddyBoat) -> dict[str, Any]:
    """A boat as plain JSON-able data, with its ``id`` for the web form."""
    return {"id": boat.id, **asdict(boat)}


def _text(data: Mapping[str, Any], key: str, *, required: bool = False) -> str:
    raw = data.get(key, "")
    if raw is None:
        raw = ""
    if not isinstance(raw, str):
        raise FleetError(f"{key} must be text")
    value = " ".join(raw.split()) if key != "notes" else raw.strip()
    if required and not value:
        raise FleetError(f"{key} is required")
    if len(value) > _TEXT_LIMITS.get(key, 80):
        raise FleetError(f"{key} is too long (over {_TEXT_LIMITS.get(key, 80)} characters)")
    return value


def _number(
    data: Mapping[str, Any], key: str, *, maximum: float, required: bool = False
) -> float | None:
    raw = data.get(key)
    if raw is None or raw == "":
        if required:
            raise FleetError(f"{key.replace('_', ' ')} is required")
        return None
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise FleetError(f"{key.replace('_', ' ')} must be a number")
    value = float(raw)
    if not 0 < value <= maximum:
        raise FleetError(f"{key.replace('_', ' ')} must be above 0 and at most {maximum:g}")
    return value


def boat_from_dict(data: Mapping[str, Any]) -> BuddyBoat:
    """Validate one boat from the web form or the saved file.

    Raises :class:`FleetError` with a message a person can act on.
    """
    name = _text(data, "name", required=True)
    if not boat_id(name):
        raise FleetError("name needs at least one letter or digit")
    hull = _text(data, "hull") or "monohull"
    if hull not in HULLS:
        raise FleetError(f"hull must be one of {', '.join(HULLS)}")
    draft = _number(data, "draft_ft", maximum=_MAX_DRAFT_FT, required=True)
    air = _number(data, "air_draft_ft", maximum=_MAX_AIR_DRAFT_FT, required=True)
    assert draft is not None and air is not None
    lifting = bool(data.get("lifting_keel", False))
    draft_up = _number(data, "draft_up_ft", maximum=_MAX_DRAFT_FT) if lifting else None
    if lifting and draft_up is None:
        raise FleetError("a lifting keel needs its keel-up draft")
    if draft_up is not None and draft_up >= draft:
        raise FleetError("the keel-up draft must be less than the keel-down draft")
    mmsi = _text(data, "mmsi")
    if mmsi and not (mmsi.isdigit() and len(mmsi) == 9):
        raise FleetError("MMSI must be 9 digits")
    call_sign = _text(data, "call_sign").upper()
    polar_file = _text(data, "polar_file")
    if polar_file and (
        ".." in polar_file
        or polar_file.startswith(("/", "\\"))
        or Path(polar_file).is_absolute()
    ):
        raise FleetError("polar_file must be a plain relative path")
    return BuddyBoat(
        name=name,
        draft_ft=draft,
        air_draft_ft=air,
        lifting_keel=lifting,
        draft_up_ft=draft_up,
        notes=_text(data, "notes"),
        telemetry=bool(data.get("telemetry", False)),
        hull=hull,
        make=_text(data, "make"),
        model=_text(data, "model"),
        loa_ft=_number(data, "loa_ft", maximum=_MAX_LOA_FT),
        beam_ft=_number(data, "beam_ft", maximum=_MAX_BEAM_FT),
        displacement_lb=_number(data, "displacement_lb", maximum=_MAX_DISPLACEMENT_LB),
        mmsi=mmsi,
        call_sign=call_sign,
        usual_companion=bool(data.get("usual_companion", False)),
        polar_file=polar_file,
    )


def load_fleet(path: Path, seed: Sequence[BuddyBoat] = ()) -> list[BuddyBoat]:
    """The saved fleet; a missing file means the fleet has never been saved, so the
    ``seed`` (the boats in ``vessel.toml``) is the fleet."""
    if not path.is_file():
        return list(seed)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw["boats"]
        if not isinstance(items, list):
            raise TypeError("'boats' must be a list")
        boats = [
            boat_from_dict({k: v for k, v in item.items() if k in _FIELDS}) for item in items
        ]
        return list({b.id: b for b in boats}.values())  # a repeated name keeps the last
    except FleetError as exc:
        raise FleetError(f"{path.name}: a saved boat is not valid ({exc})") from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FleetError(f"{path.name}: cannot read the fleet list ({exc})") from exc


def save_fleet(path: Path, boats: Sequence[BuddyBoat]) -> None:
    """Write the fleet atomically (temp file, then replace)."""
    payload = {"boats": [asdict(b) for b in sorted(boats, key=lambda b: b.name.lower())]}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def upsert(
    boats: Sequence[BuddyBoat], boat: BuddyBoat, *, replaces: str | None = None
) -> list[BuddyBoat]:
    """Add ``boat`` or update the one with the same id. ``replaces`` is the id being
    edited, so renaming a boat replaces it instead of adding a second entry. A name
    that collides with a different boat is refused."""
    target = replaces or boat.id
    clash = [b for b in boats if b.id == boat.id and b.id != target]
    if clash:
        raise FleetError(f"there is already a boat called {clash[0].name!r}")
    kept = [b for b in boats if b.id != target]
    return [*kept, boat]


def remove(boats: Sequence[BuddyBoat], boat_id_: str) -> list[BuddyBoat]:
    """The fleet without ``boat_id_``; raises if there is no such boat."""
    kept = [b for b in boats if b.id != boat_id_]
    if len(kept) == len(boats):
        raise FleetError(f"no boat with id {boat_id_!r} in the fleet")
    return kept


def parse_ids(raw: str | None) -> list[str] | None:
    """The web form's / CLI's boat choice: ``None`` (not given) means the usual
    companions; a JSON list or a comma list means exactly those boats; ``[]`` or an
    empty string after being given means travelling alone."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return []
    if text[0] in "[{":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FleetError("fleet must be a JSON list of boat ids") from exc
        if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
            raise FleetError("fleet must be a JSON list of boat ids")
        return [boat_id(x) for x in data if boat_id(x)]
    return [boat_id(x) for x in text.split(",") if boat_id(x)]


def select(boats: Sequence[BuddyBoat], ids: Sequence[str] | None) -> tuple[BuddyBoat, ...]:
    """The boats a passage travels with (see the module notes for ``ids``)."""
    if ids is None:
        return tuple(b for b in boats if b.usual_companion)
    by_id = {b.id: b for b in boats}
    chosen: list[BuddyBoat] = []
    for raw in ids:
        key = boat_id(raw)
        if key not in by_id:
            raise FleetError(f"no boat {raw!r} in the fleet list")
        if by_id[key] not in chosen:
            chosen.append(by_id[key])
    return tuple(chosen)


def apply_fleet(app: AppConfig, ids: Sequence[str] | None = None) -> AppConfig:
    """``app`` with the passage's companion boats in place of whatever
    ``vessel.toml`` lists, so every consumer of ``vessel.buddy_boats`` sees the choice."""
    boats = load_fleet(library_path(app.config_dir), app.vessel.buddy_boats)
    chosen = select(boats, ids)
    return replace(app, vessel=replace(app.vessel, buddy_boats=chosen))
