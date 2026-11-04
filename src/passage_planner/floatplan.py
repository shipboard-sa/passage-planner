"""Auto-populate the USPS Float Plan (v10.2) fillable PDF from a passage plan.

The form has 346 AcroForm fields. Most of it is either computed from the
passage (the whole itinerary block) or static about the boat and its regular
crew (``config/float_plan.toml`` / ``config/crew.toml``) — filled once and
reused every passage. The only things that change trip to trip beyond the
route itself are the crew complement and days of food aboard, both CLI flags
on the ``float-plan`` command.

Itinerary rows are filled ARRIVE-only for every waypoint after the first
(the form's own DEPART/ARRIVE pair per stop assumes a known layover, which a
continuous :class:`~passage_planner.passage.PassagePlan` does not model) — the
first row's DEPART fields cover getting underway. This matches how the
vessel's own 2022 float plans were filled for offshore legs; hand-edit the
result if a specific stop is an overnight layover.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError

from .analysis import PassageAnalysis, SailMode
from .clock import Clock
from .config import VesselConfig
from .errors import FloatPlanError
from .passage import PassagePlan
from .route import Waypoint

# The form's Yes/No boxes this vessel can actually answer; keys are the exact
# PDF field names so a typo in the TOML is caught rather than silently ignored.
EQUIPMENT_FIELDS: tuple[str, ...] = (
    "NAV-Radar", "NAV-Compass", "NAV-Maps", "NAV-DepthSounder", "NAV-GPS", "NAV-Charts",
    "VDS-EDL", "VDS-Flag", "VDS-FlareAerial", "VDS-FlareHandheld", "VDS-SignalMirror", "VDS-Smoke",
    "ADS-Horn", "ADS-Whistle", "ADS-Bell",
    "ADD-Anchor", "ADD-FireExtinguisher", "ADD-Flashlight", "ADD-Raft", "ADD-FoodAvail",
    "ADD-Dewatering", "ADD-Water", "ADD-ExposureSuit",
)

_MODE_LABEL = {SailMode.SAIL: "Sail", SailMode.MOTOR: "Motor", SailMode.MOTORSAIL: "Motorsail"}
_MAX_ITINERARY_ROWS = 21  # the form has rows 01 (depart) through 21 (arrive)
_WP_PLACEHOLDER_RE = re.compile(r"WP\d+", re.IGNORECASE)


def _age(dob: date, today: date) -> str:
    years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return str(years)


@dataclass(frozen=True, slots=True)
class CrewMember:
    name: str
    phone: str = ""
    gender: str = ""            # "M" | "F" | "" (matches the form's choice field)
    dob: date | None = None
    plb_uin: str = ""
    pfd: bool = True
    note: str = ""

    def age(self, today: date) -> str:
        return _age(self.dob, today) if self.dob is not None else ""


@dataclass(frozen=True, slots=True)
class FloatPlanConfig:
    """Static, rarely-changing facts the float plan needs beyond ``vessel.toml``."""

    hailing_port: str
    official_number: str
    hin: str
    vessel_type: str          # must match the form's ID-Type option, e.g. "Cutter"
    hull_material: str        # must match ID-HullMat, e.g. "Fiberglass"
    hull_trim_colors: str
    prominent_features: str
    call_sign: str
    radio1_type: str
    radio1_freq_mon: str
    radio2_type: str
    radio2_freq_mon: str
    cell_sat_phone: str
    operator_email: str
    epirb_uin: str
    anchor_line_length_ft: float
    equipment_aboard: frozenset[str]
    operator_name: str
    operator_address: str
    operator_city: str
    operator_state: str
    operator_zip: str
    operator_phone: str
    operator_gender: str
    operator_dob: date | None
    operator_plb_uin: str
    tracking_note: str
    contact1_name: str
    contact1_phone: str
    contact2_name: str
    contact2_phone: str
    rescue_authority: str
    rescue_authority_phone: str


def _as_date(value: object, ctx: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise FloatPlanError(f"{ctx}: not an ISO date: {value!r}") from exc


def load_float_plan_config(path: str | Path) -> FloatPlanConfig:
    p = Path(path)
    if not p.is_file():
        raise FloatPlanError(f"{p}: not found")
    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise FloatPlanError(f"{p}: {exc}") from exc

    v = raw.get("vessel", {})
    c = raw.get("comms", {})
    e = raw.get("equipment", {})
    o = raw.get("operator", {})
    k = raw.get("contacts", {})

    aboard = e.get("aboard", [])
    if not isinstance(aboard, list) or not all(isinstance(x, str) for x in aboard):
        raise FloatPlanError(f"{p}: [equipment].aboard must be a list of strings")
    unknown = set(aboard) - set(EQUIPMENT_FIELDS)
    if unknown:
        raise FloatPlanError(f"{p}: [equipment].aboard has unknown field(s): {sorted(unknown)}")

    return FloatPlanConfig(
        hailing_port=str(v.get("hailing_port", "")),
        official_number=str(v.get("official_number", "")),
        hin=str(v.get("hin", "")),
        vessel_type=str(v.get("vessel_type", "")),
        hull_material=str(v.get("hull_material", "")),
        hull_trim_colors=str(v.get("hull_trim_colors", "")),
        prominent_features=str(v.get("prominent_features", "")),
        anchor_line_length_ft=float(v.get("anchor_line_length_ft", 0.0)),
        call_sign=str(c.get("call_sign", "")),
        radio1_type=str(c.get("radio1_type", "")),
        radio1_freq_mon=str(c.get("radio1_freq_mon", "")),
        radio2_type=str(c.get("radio2_type", "")),
        radio2_freq_mon=str(c.get("radio2_freq_mon", "")),
        cell_sat_phone=str(c.get("cell_sat_phone", "")),
        operator_email=str(c.get("operator_email", "")),
        epirb_uin=str(c.get("epirb_uin", "")),
        equipment_aboard=frozenset(aboard),
        operator_name=str(o.get("name", "")),
        operator_address=str(o.get("address", "")),
        operator_city=str(o.get("city", "")),
        operator_state=str(o.get("state", "")),
        operator_zip=str(o.get("zip", "")),
        operator_phone=str(o.get("phone", "")),
        operator_gender=str(o.get("gender", "")),
        operator_dob=_as_date(o.get("dob"), f"{p}:operator.dob"),
        operator_plb_uin=str(o.get("plb_uin", "")),
        tracking_note=str(c.get("tracking_note", "")),
        contact1_name=str(k.get("contact1_name", "")),
        contact1_phone=str(k.get("contact1_phone", "")),
        contact2_name=str(k.get("contact2_name", "")),
        contact2_phone=str(k.get("contact2_phone", "")),
        rescue_authority=str(k.get("rescue_authority", "")),
        rescue_authority_phone=str(k.get("rescue_authority_phone", "")),
    )


def load_crew_roster(path: str | Path) -> dict[str, CrewMember]:
    """``config/crew.toml``'s ``[[crew]]`` entries, keyed by lowercase ``key``."""
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise FloatPlanError(f"{p}: {exc}") from exc
    entries = raw.get("crew", [])
    if not isinstance(entries, list):
        raise FloatPlanError(f"{p}: [[crew]] must be an array of tables")
    out: dict[str, CrewMember] = {}
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise FloatPlanError(f"{p}: crew[{i}] must be a table")
        key = str(entry.get("key", "")).strip().lower()
        if not key:
            raise FloatPlanError(f"{p}: crew[{i}] is missing 'key'")
        out[key] = CrewMember(
            name=str(entry.get("name", "")),
            phone=str(entry.get("phone", "")),
            gender=str(entry.get("gender", "")),
            dob=_as_date(entry.get("dob"), f"{p}:crew[{i}].dob"),
            plb_uin=str(entry.get("plb_uin", "")),
            pfd=bool(entry.get("pfd", True)),
            note=str(entry.get("note", "")),
        )
    return out


def resolve_crew(names: list[str], roster: dict[str, CrewMember]) -> list[CrewMember]:
    """Turn ``--crew`` entries into :class:`CrewMember`, modifying the complement
    per passage: a bare name looks up the roster; ``Name:phone:gender:YYYY-MM-DD``
    (trailing parts optional) adds someone not in it, e.g. a guest.
    """
    out: list[CrewMember] = []
    for raw in names:
        raw = raw.strip()
        if not raw:
            continue
        if ":" not in raw:
            member = roster.get(raw.lower())
            out.append(member if member is not None else CrewMember(name=raw))
            continue
        parts = [p.strip() for p in raw.split(":")]
        parts += [""] * (4 - len(parts))
        name, phone, gender, dob_raw = parts[:4]
        out.append(CrewMember(
            name=name, phone=phone, gender=gender.upper(),
            dob=_as_date(dob_raw, f"--crew entry {raw!r}"),
        ))
    return out


def _dm(lat: float, lon: float) -> str:
    ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
    la, lo = abs(lat), abs(lon)
    lad, lam = int(la), (la - int(la)) * 60
    lod, lom = int(lo), (lo - int(lo)) * 60
    return f"{lad} {lam:.3f}{ns}, {lod} {lom:.3f}{ew}"


def _location(wp: Waypoint) -> str:
    name = wp.name.strip() if wp.name else ""
    if name and not _WP_PLACEHOLDER_RE.fullmatch(name):
        return name
    return _dm(wp.lat, wp.lon)


def _time12(local: datetime) -> str:
    hour12 = local.hour % 12 or 12
    return f"{hour12}:{local:%M} {'am' if local.hour < 12 else 'pm'}"


def _itinerary_fields(plan: PassagePlan, analysis: PassageAnalysis, clock: Clock) -> dict[str, str]:
    wps = plan.route.waypoints
    etas = plan.waypoint_etas(analysis.leg_speeds)
    n = min(len(wps), _MAX_ITINERARY_ROWS)
    out: dict[str, str] = {}
    for i in range(n):
        wp = wps[i]
        local = etas[i].astimezone(ZoneInfo(clock.zone_at(wp.lat, wp.lon)))
        date_s, time_s, loc_s = f"{local:%m-%d-%y}", _time12(local), _location(wp)
        if i == 0:
            mode = _MODE_LABEL.get(analysis.legs[0].mode, "") if analysis.legs else ""
            out.update({
                "01DepartDate": date_s, "01DepartTime": time_s,
                "01DepartLocation": loc_s, "01DepartMode": mode,
            })
        else:
            row = f"{i + 1:02d}"
            out.update({
                f"{row}ArriveDate": date_s, f"{row}ArriveTime": time_s,
                f"{row}ArriveLocation": loc_s,
            })
    return out


def build_field_values(
    *,
    vessel: VesselConfig,
    plan: PassagePlan,
    analysis: PassageAnalysis,
    clock: Clock,
    static: FloatPlanConfig,
    crew: list[CrewMember],
    food_days: float | None = None,
    today: date | None = None,
) -> dict[str, str]:
    """Every PDF field this framework can fill, given the passage + config."""
    today = today or date.today()
    values: dict[str, str] = {
        "ID-VesselName": (
            f"{vessel.name} / {static.hailing_port}" if static.hailing_port else vessel.name
        ),
        "ID-DocRegNum": static.official_number,
        "ID-HIN": static.hin,
        "ID-YearMakeModel": f"{vessel.year} {vessel.model}",
        "ID-Length": f"{vessel.loa_ft:.0f}",
        "ID-Draft": f"{vessel.draft.actual_max_ft:.1f}",
        "ID-Type": static.vessel_type,
        "ID-HullMat": static.hull_material,
        "ID-HullTrimColors": static.hull_trim_colors,
        "ID-ProminentFeatures": static.prominent_features,
        "PRO-PrimEngType": "Diesel IB",
        "PRO-PrimNumEngines": "1",
        "PRO-PrimFuelCapacity": f"{vessel.propulsion.fuel_capacity_gal:.0f}",
        "COM-DSCNo": vessel.mmsi,
        "COM-RadioCallSign": static.call_sign,
        "COM-Radio1Type": static.radio1_type,
        "COM-Radio1FreqMon": static.radio1_freq_mon,
        "COM-Radio2Type": static.radio2_type,
        "COM-Radio2FreqMon": static.radio2_freq_mon,
        "COM-CellSatPhone": static.cell_sat_phone,
        "COM-Email": static.operator_email,
        "EPIRB-UIN": static.epirb_uin,
        "ADD-AnchorLineLength": (
            f"{static.anchor_line_length_ft:.0f}" if static.anchor_line_length_ft else ""
        ),
        "OPR-Name": static.operator_name,
        "OPR-Address": static.operator_address,
        "OPR-City": static.operator_city,
        "OPR-State": static.operator_state,
        "OPR-ZipCode": static.operator_zip,
        "OPR-Home Phone": static.operator_phone,
        "OPR-Gender": static.operator_gender,
        "OPR-PLBUIN": static.operator_plb_uin,
        "OPR-VehicleYearMakeModel": "n/a",
        "OPR-Float Plan Note": static.tracking_note,
        "OPR-VesselExperience": "/Yes",
        "OPR-PFD": "/Yes",
        "Contact1": static.contact1_name,
        "Contact1-Phone": static.contact1_phone,
        "Contact2": static.contact2_name,
        "Contact2-Phone": static.contact2_phone,
        "RescueAuthority": static.rescue_authority,
        "RescueAuthority-Phone": static.rescue_authority_phone,
        "OPR-Note": (
            f"Waypoints planned at {plan.planning_speed_kn:.1f} kn — actual speed will vary."
        ),
    }
    if static.operator_dob is not None:
        values["OPR-Age"] = _age(static.operator_dob, today)
    for name in static.equipment_aboard:
        values[name] = "/Yes"

    for i, member in enumerate(crew[:12], start=1):
        p = f"POB-{i:02d}"
        values[f"{p}Name"] = member.name
        if member.phone:
            values[f"{p}HomePhone"] = member.phone
        if member.gender:
            values[f"{p}Gender"] = member.gender
        if member.dob is not None:
            values[f"{p}Age"] = member.age(today)
        if member.plb_uin:
            values[f"{p}PLBnum"] = member.plb_uin
        if member.pfd:
            values[f"{p}PFD"] = "/Yes"
        if member.note:
            values[f"{p}Note"] = member.note

    if vessel.water is not None:
        crew_count = max(len(crew), 1)
        values["ADD-WaterDays"] = f"{vessel.water.tank_days(crew_count):.0f}"
    if food_days is not None:
        values["ADD-FoodDays"] = f"{food_days:.0f}"

    values.update(_itinerary_fields(plan, analysis, clock))
    return {k: v for k, v in values.items() if v not in (None, "")}


def fill_float_plan_pdf(
    template_path: str | Path, out_path: str | Path, values: dict[str, str]
) -> Path:
    """Write ``template_path`` to ``out_path`` with ``values`` filled in."""
    template = Path(template_path)
    if not template.is_file():
        raise FloatPlanError(f"{template}: not found")
    try:
        reader = PdfReader(template)
    except (PyPdfError, OSError) as exc:
        raise FloatPlanError(f"{template}: could not be read ({exc})") from exc

    known = set(reader.get_fields() or {})
    unknown = set(values) - known
    if unknown:
        raise FloatPlanError(
            f"{template}: unknown field(s), template may have changed: {sorted(unknown)}"
        )

    writer = PdfWriter()
    writer.append(reader)
    for page in writer.pages:
        writer.update_page_form_field_values(page, values, auto_regenerate=False)
    # Ask viewers to (re)render field appearances rather than trust pypdf's own.
    writer.set_need_appearances_writer(True)

    out = Path(out_path)
    with out.open("wb") as fh:
        writer.write(fh)
    return out
