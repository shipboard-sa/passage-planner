"""Web front end for the routing pipeline: a form that collects the inputs
`passage-planner brief` needs and runs the same generation code in-process.

Purpose: FastAPI web UI for generating passage briefings, as an alternative
    to the `passage-planner brief` CLI command.
Version: v8 (2026-09-24) - GET /api/health (liveness for the Docker healthcheck; depends on
    nothing). v7 (2026-09-22) - GET/POST /api/papers (all DocKind rows + their override fields, one
    save writes config/papers.toml's whole [documents] table) and GET/POST /api/comms
    (config/comms.toml), for the Documents and Comms form screens (papers.py/comms.py's new
    save_overrides()/save_comms_config()). v6: POST /api/fleet/lookup parses a pasted MarineTraffic
    Overview page and/or ORC certificate (boat_lookup.py) into spec fields and a real polar;
    POST/PUT /api/fleet now accepts polar_pol_text (written to config/polars/<id>.pol,
    boat.polar_file set server-side - never accepted as a bare path from the client) and preserves
    an existing polar across an edit that doesn't repaste one. BoatIn gained call_sign. v5:
    /api/arrival-places (+ /suggest) serve the arrival pick list, and port lists accept
    newline/semicolon separators. v4: /api/brief takes the CLI's advanced options (skip toggles,
    --compare, four override files). v3: the /api/logistics-research endpoints (status, start, poll,
    cancel) over research_jobs.ResearchJobs, which the frontend's ResearchPanel.tsx calls.
Scope: PRODUCTION - passage-planner web entry point.
Architecture: passage-planner subsystem. Wraps passage_planner.service.run_brief
    (the same code the CLI in src/passage_planner/cli.py calls) behind a form,
    serves frontend/dist as static files. Entry point for
    `uvicorn backend.main:app --port 8100`.

Run with: uvicorn backend.main:app --reload --port 8100  (from 
"""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from passage_planner import boat_lookup
from passage_planner import config as routing_config
from passage_planner import fleet as fleet_lib
from passage_planner.arrival import COUNTRIES, ArrivalPlaceError, load_places, nearest
from passage_planner.comms import CommsConfig, load_comms_config, save_comms_config
from passage_planner.errors import ResearchUnavailableError, RoutingError
from passage_planner.floatplan import load_crew_roster
from passage_planner.hydro import assess_route
from passage_planner.hydro import soundings as depth_soundings
from passage_planner.logistics import (
    CHECKED_FIELD_COUNT,
    logistics_template,
    split_ports,
    unresearched_ports,
)
from passage_planner.papers import KIND_LABEL, DocKind, load_overrides, save_overrides
from passage_planner.passage import PassagePlan
from passage_planner.polar import polar_to_pol_text
from passage_planner.research import STOP_ROLES, research_status
from passage_planner.research_jobs import ResearchBusyError, ResearchJobs
from passage_planner.route import Route, load_route, parse_gpx
from passage_planner.service import BriefResult, clock_for, parse_depart, run_brief

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
UPLOAD_DIR = ROOT / "var" / "web_uploads"
BRIEFING_DIR = ROOT / "var" / "web_briefings"
LOGISTICS_DIR = ROOT / "var" / "logistics"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
BRIEFING_DIR.mkdir(parents=True, exist_ok=True)

# In-memory index of generated briefings, keyed by a per-run id. This is a
# single-user LAN tool (no auth, matches vessel-ops-multiuser-hosting's
# trust boundary) so losing this on a server restart is acceptable for v1.
_RUNS: dict[str, dict[str, Path]] = {}

# Shore-logistics research jobs (headless Claude, one port at a time). Finished
# ports are also written to var/logistics/research_<id>_<port>.md.
_RESEARCH = ResearchJobs(LOGISTICS_DIR)

app = FastAPI(title="Hylas Routing — Passage Briefing")


@app.get("/api/health")
def health() -> dict[str, str]:
    """Liveness only: touches no InfluxDB, disk or network, so a slow dependency
    can never turn into a container restart loop (Docker spec v5)."""
    return {"status": "ok"}


def _brief_args(route_path: Path, out_dir: Path, **core: Any) -> SimpleNamespace:
    """Build the same attribute set `service.run_brief` expects from the CLI's
    parsed `brief` subcommand, core fields overridden, everything else left at
    the CLI's own defaults (see `cli.py`'s `_build_parser`)."""
    defaults = dict(
        route=route_path,
        speed=6.5,
        country=None,
        crew="Skipper,Mate",
        watch_hours=6.0,
        step_nm=15.0,
        config=CONFIG_DIR,
        out_dir=out_dir,
        stem=None,
        no_tropical=False,
        compare=False,
        no_tides=False,
        no_notices=False,
        no_maintenance=False,
        no_advisories=False,
        no_papers=False,
        papers_file=None,
        depths_file=None,
        ciq_file=None,
        logistics_file=None,
        parker_file=None,
        comms_config=None,
        float_plan=False,
        float_plan_crew=None,
        food_days=None,
        fleet=None,
        solo=False,
        float_plan_config=None,
        crew_file=None,
        float_plan_template=None,
        float_plan_out=None,
        to_stdout=False,
    )
    defaults.update(core)
    return SimpleNamespace(**defaults)


@app.get("/api/logistics-template")
def get_logistics_template(ports: str, route_name: str | None = None) -> dict[str, str]:
    port_list = split_ports(ports)
    if not port_list:
        raise HTTPException(status_code=400, detail="give at least one port")
    return {"markdown": logistics_template(port_list, route_name=route_name)}


class LogisticsCoverageRequest(BaseModel):
    markdown: str


@app.post("/api/logistics-coverage")
def logistics_coverage(req: LogisticsCoverageRequest) -> dict[str, Any]:
    """Submit-guard check: which port sections in the shore-logistics text still
    look like an untouched template (more than half their fields empty). The
    form asks before submitting a briefing with such a section, since briefing
    section 7b would otherwise ship placeholder text (happened 2026-09-18)."""
    return {
        "checked": CHECKED_FIELD_COUNT,
        "unresearched": [
            {"port": port, "missing": missing}
            for port, missing in unresearched_ports(req.markdown).items()
        ],
    }


@app.get("/api/logistics-research/status")
def logistics_research_status() -> dict[str, Any]:
    """Whether the Research button can work here (CLI found, token set) and, if
    not, why. Offline and cheap, so the UI can call it on every page load."""
    st = research_status()
    return {"available": st.available, "reason": st.reason}


def _parse_roles(raw: str | None) -> dict[str, str]:
    """The optional ``roles`` form field: a JSON object of port -> stop role."""
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="roles is not valid JSON") from exc
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise HTTPException(status_code=400, detail="roles must map port names to roles")
    bad = sorted({v for v in data.values() if v and v not in STOP_ROLES})
    if bad:
        raise HTTPException(
            status_code=400, detail=f"unknown stop role {bad}; expected {sorted(STOP_ROLES)}"
        )
    return {k: v for k, v in data.items() if v}


@app.post("/api/logistics-research")
def start_logistics_research(
    ports: Annotated[str, Form()],
    route: UploadFile | None = None,
    crew_count: Annotated[int | None, Form()] = None,
    roles: Annotated[str | None, Form()] = None,
    fleet: Annotated[str | None, Form()] = None,
) -> dict[str, str]:
    """Start researching ``ports`` (comma-separated) in the background. The
    optional route GPX lets the model pick the settlement nearest the track when
    a name is ambiguous. 409 if a job is already running, 503 if the feature is
    not set up (see /status). Poll GET /api/logistics-research/{id}.
    ``roles`` is an optional JSON object mapping a port name to why the boat stops
    there ("check-in", "check-out" or "fuel")."""
    port_list = split_ports(ports)
    if not port_list:
        raise HTTPException(status_code=400, detail="give at least one port")
    role_map = _parse_roles(roles)
    st = research_status()
    if not st.available or st.exe is None:
        raise HTTPException(status_code=503, detail=st.reason)

    parsed: Route | None = None
    if route is not None and route.filename:
        with tempfile.TemporaryDirectory(prefix="hylas-route-") as tmp:
            gpx = Path(tmp) / "route.gpx"
            with gpx.open("wb") as f:
                shutil.copyfileobj(route.file, f)
            try:
                parsed = load_route(gpx)
            except RoutingError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        vessel = fleet_lib.apply_fleet(
            routing_config.load(CONFIG_DIR), fleet_lib.parse_ids(fleet)
        ).vessel
    except RoutingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        job_id = _RESEARCH.start(
            port_list,
            vessel=vessel,
            exe=st.exe,
            route=parsed,
            crew_count=crew_count,
            roles=role_map,
        )
    except ResearchBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ResearchUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"id": job_id}


@app.get("/api/logistics-research/{job_id}")
def get_logistics_research(job_id: str) -> dict[str, Any]:
    snap = _RESEARCH.snapshot(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="no such research job")
    return snap


@app.delete("/api/logistics-research/{job_id}")
def cancel_logistics_research(job_id: str) -> dict[str, Any]:
    """Cancel a running job: the running port's Claude process is killed, later
    ports are not started, ports already finished are kept."""
    if not _RESEARCH.cancel(job_id):
        raise HTTPException(status_code=404, detail="no such research job")
    return {"cancelling": True}


@app.get("/api/arrival-places")
def get_arrival_places() -> dict[str, Any]:
    """The arrival pick list (data/arrival_places.toml), sorted by country, island
    and name. Identity data only; fees, hours and the like are researched per
    passage. A malformed data file is a 500 with the reason, not a silent gap."""
    try:
        places = load_places()
    except ArrivalPlaceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "places": [p.to_dict() for p in places],
        "countries": sorted(COUNTRIES.values()),
    }


@app.post("/api/arrival-places/suggest")
def suggest_arrival_places(route: UploadFile, limit: int = 6) -> dict[str, Any]:
    """The listed places nearest where the uploaded GPX route ends (and starts), so
    the form can offer 'your route ends near ...'. Distances in NM."""
    with tempfile.TemporaryDirectory(prefix="hylas-route-") as tmp:
        gpx = Path(tmp) / "route.gpx"
        with gpx.open("wb") as f:
            shutil.copyfileobj(route.file, f)
        try:
            parsed = load_route(gpx)
            places = load_places()
        except (RoutingError, ArrivalPlaceError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    end = parsed.waypoints[-1]
    start = parsed.waypoints[0]

    def pack(lat: float, lon: float) -> list[dict[str, Any]]:
        return [
            {**p.to_dict(), "distance_nm": round(d, 1)}
            for p, d in nearest(places, lat, lon, n=max(1, min(limit, 15)), max_nm=60)
        ]

    return {"end": pack(end.lat, end.lon), "start": pack(start.lat, start.lon)}


class SoundingIn(BaseModel):
    lat: float
    lon: float
    depth_ft: float
    source: str = ""
    notes: str = ""
    label: str = ""


class SoundingsIn(BaseModel):
    soundings: list[SoundingIn]


def _library_file() -> Path:
    return depth_soundings.library_path(CONFIG_DIR)


@app.post("/api/depth-check")
def depth_check(
    route: UploadFile,
    depart: Annotated[str, Form()],
    speed: Annotated[float, Form()] = 6.5,
    fleet: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """The Depth check screen's data: which waypoints of the uploaded route need a
    chart depth read (or are too shallow at the tide), what the automatic data says,
    and any depth already saved for the spot. Uses planning speed for the tide times.
    Makes the same NOAA / bathymetry calls as a briefing, so it takes a few seconds."""
    with tempfile.TemporaryDirectory(prefix="hylas-route-") as tmp:
        gpx = Path(tmp) / "route.gpx"
        with gpx.open("wb") as f:
            shutil.copyfileobj(route.file, f)
        try:
            app_cfg = fleet_lib.apply_fleet(
                routing_config.load(CONFIG_DIR), fleet_lib.parse_ids(fleet)
            )
            parsed = parse_gpx(gpx)
            depart_utc = parse_depart(depart, clock_for(app_cfg), parsed.waypoints[0])
            plan = PassagePlan(route=parsed, depart_utc=depart_utc, planning_speed_kn=speed)
            library = depth_soundings.load_soundings(_library_file())
            supplied = depth_soundings.soundings_to_depths(
                [(w.name, w.lat, w.lon) for w in parsed.waypoints], library
            )
            report = assess_route(app_cfg.vessel, plan, waypoint_depths=supplied)
        except RoutingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "route_name": parsed.name,
        "required_ft": report.required_charted_ft,
        "draft_ft": app_cfg.vessel.draft.planning_ft,
        "margin_ft": app_cfg.vessel.draft.ukc_margin_ft,
        "rows": depth_soundings.depth_rows(report, library),
    }


@app.get("/api/depth-soundings")
def list_depth_soundings() -> dict[str, Any]:
    try:
        library = depth_soundings.load_soundings(_library_file())
    except RoutingError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"soundings": [asdict(s) for s in library]}


@app.put("/api/depth-soundings")
def save_depth_soundings(body: SoundingsIn) -> dict[str, Any]:
    """Add or update depths read off a chart. A depth within 0.1 NM of a saved one
    replaces it. They are used automatically by every later briefing whose route
    passes within half a nautical mile."""
    try:
        library = depth_soundings.load_soundings(_library_file())
        for item in body.soundings:
            library = depth_soundings.upsert(
                library, lat=item.lat, lon=item.lon, depth_ft=item.depth_ft,
                source=item.source, notes=item.notes, label=item.label,
            )
        depth_soundings.save_soundings(_library_file(), library)
    except RoutingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"saved": len(body.soundings), "total": len(library)}


@app.delete("/api/depth-soundings/{sounding_id}")
def forget_depth_sounding(sounding_id: str) -> dict[str, Any]:
    try:
        library = depth_soundings.load_soundings(_library_file())
        kept = depth_soundings.remove(library, sounding_id)
        if len(kept) == len(library):
            raise HTTPException(status_code=404, detail="no such saved depth")
        depth_soundings.save_soundings(_library_file(), kept)
    except RoutingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"removed": sounding_id, "total": len(kept)}


class BoatIn(BaseModel):
    """A boat as the form saves it. Loose types on purpose: ``fleet.boat_from_dict``
    validates and words the errors."""

    name: str
    hull: str = "monohull"
    make: str = ""
    model: str = ""
    draft_ft: float | None = None
    lifting_keel: bool = False
    draft_up_ft: float | None = None
    air_draft_ft: float | None = None
    loa_ft: float | None = None
    beam_ft: float | None = None
    displacement_lb: float | None = None
    mmsi: str = ""
    call_sign: str = ""
    notes: str = ""
    telemetry: bool = False
    usual_companion: bool = False


class BoatSaveIn(BaseModel):
    boat: BoatIn
    replaces: str | None = None  # the id being edited, so a rename replaces it
    # A real polar (.pol text) from the "Look up this boat" paste box's parsed
    # ORC VPP table - never accepted as a bare `polar_file` path from the client
    # (see fleet.py's boat_from_dict path-traversal guard). None/omitted on an
    # ordinary edit keeps whatever polar the boat already had on file.
    polar_pol_text: str | None = None


def _fleet_file() -> Path:
    return fleet_lib.library_path(CONFIG_DIR)


def _polars_dir() -> Path:
    d = CONFIG_DIR / "polars"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fleet_boats() -> list[Any]:
    seed = routing_config.load(CONFIG_DIR).vessel.buddy_boats
    return fleet_lib.load_fleet(_fleet_file(), seed)


def _fleet_response(boats: list[Any]) -> dict[str, Any]:
    ordered = sorted(boats, key=lambda b: b.name.lower())
    return {
        "boats": [fleet_lib.boat_to_dict(b) for b in ordered],
        "hulls": list(routing_config.HULLS),
        "usual": [b.id for b in ordered if b.usual_companion],
    }


@app.get("/api/fleet")
def get_fleet() -> dict[str, Any]:
    """The boats that may travel with the vessel (config/fleet.json, or the
    vessel.toml boats until the list is first saved) and which are usual companions."""
    try:
        return _fleet_response(_fleet_boats())
    except RoutingError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# The web form uses POST for saving and removing: mx-inv's /routing reverse proxy only
# forwards GET and POST, so PUT/DELETE never reach this app through :8000. PUT/DELETE
# stay as the conventional aliases for direct callers (:8100, tests, scripts).
@app.put("/api/fleet")
@app.post("/api/fleet")
def save_fleet_boat(body: BoatSaveIn) -> dict[str, Any]:
    """Add a boat or update one (send ``replaces`` with the old id when renaming).
    A freshly-parsed ``polar_pol_text`` is written to ``config/polars/<id>.pol``;
    without one, an edit keeps whatever polar the boat already had on file."""
    try:
        existing = _fleet_boats()
        boat = fleet_lib.boat_from_dict(body.boat.model_dump())
        if body.polar_pol_text:
            pol_path = _polars_dir() / f"{boat.id}.pol"
            pol_path.write_text(body.polar_pol_text, encoding="utf-8")
            boat = replace(boat, polar_file=f"polars/{boat.id}.pol")
        else:
            prior = next((b for b in existing if b.id == (body.replaces or boat.id)), None)
            if prior is not None:
                boat = replace(boat, polar_file=prior.polar_file)
        boats = fleet_lib.upsert(existing, boat, replaces=body.replaces)
        fleet_lib.save_fleet(_fleet_file(), boats)
    except RoutingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _fleet_response(boats)


class BoatLookupIn(BaseModel):
    marinetraffic_text: str = ""
    orc_text: str = ""


@app.post("/api/fleet/lookup")
def lookup_boat(body: BoatLookupIn) -> dict[str, Any]:
    """Parse a pasted MarineTraffic Overview page and/or ORC certificate into
    boat-spec fields, and a real polar when the ORC text has a VPP table.
    Neither source documents a stable public API (see boat_lookup.py's module
    docstring), so nothing is fetched here - the person pastes what their own
    browser already loaded. Nothing is saved; the Fleet form's Save button
    still does that, same as every other field."""
    mt = boat_lookup.parse_marinetraffic(body.marinetraffic_text)
    orc, polar = boat_lookup.parse_orc(body.orc_text)
    combined = boat_lookup.merge(mt, orc)
    return {
        "mmsi": combined.mmsi,
        "call_sign": combined.call_sign,
        "flag": combined.flag,
        "loa_ft": combined.loa_ft,
        "beam_ft": combined.beam_ft,
        "draft_ft": combined.draft_ft,
        "displacement_lb": combined.displacement_lb,
        "messages": combined.messages,
        "polar_pol_text": polar_to_pol_text(polar, header="ORC certificate (pasted)")
        if polar is not None else None,
    }


@app.delete("/api/fleet/{boat_id}")
@app.post("/api/fleet/{boat_id}/remove")
def remove_fleet_boat(boat_id: str) -> dict[str, Any]:
    try:
        boats = fleet_lib.remove(_fleet_boats(), boat_id)
        fleet_lib.save_fleet(_fleet_file(), boats)
    except RoutingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _fleet_response(boats)


def _papers_file() -> Path:
    return CONFIG_DIR / "papers.toml"


def _papers_response() -> dict[str, Any]:
    """Every DocKind (only one override is ever meaningful per kind - see
    overrides.py) with its label and current override fields, blank when
    unset."""
    overrides = load_overrides(_papers_file())
    docs = []
    for kind in DocKind:
        doc = overrides.get(kind)
        docs.append({
            "kind": kind.value,
            "label": KIND_LABEL.get(kind, kind.value),
            "path": doc.path if doc else "",
            "issued": doc.issued.isoformat() if doc and doc.issued else "",
            "expires": doc.expires.isoformat() if doc and doc.expires else "",
            "note": doc.note if doc else "",
        })
    return {"documents": docs}


@app.get("/api/papers")
def get_papers() -> dict[str, Any]:
    try:
        return _papers_response()
    except RoutingError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class PaperEntryIn(BaseModel):
    kind: str
    path: str = ""
    issued: str = ""
    expires: str = ""
    note: str = ""


class PapersSaveIn(BaseModel):
    documents: list[PaperEntryIn]


@app.put("/api/papers")
@app.post("/api/papers")
def save_papers(body: PapersSaveIn) -> dict[str, Any]:
    """Save every document's override fields in one call (the form has a
    fixed, known set of DocKind rows - there's no add/remove/rename)."""
    entries: dict[DocKind, dict[str, str]] = {}
    try:
        for entry in body.documents:
            try:
                kind = DocKind(entry.kind)
            except ValueError as exc:
                raise RoutingError(f"unknown document kind {entry.kind!r}") from exc
            entries[kind] = {
                "path": entry.path, "issued": entry.issued,
                "expires": entry.expires, "note": entry.note,
            }
        save_overrides(_papers_file(), entries)
        return _papers_response()
    except RoutingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _comms_file() -> Path:
    return CONFIG_DIR / "comms.toml"


def _comms_to_dict(cfg: CommsConfig | None) -> dict[str, Any]:
    cfg = cfg or CommsConfig()
    return {
        "starlink_plan": cfg.starlink_plan,
        "starlink_billing_cycle_day": cfg.starlink_billing_cycle_day,
        "starlink_priority_gb": cfg.starlink_priority_gb,
        "starlink_ocean_mode_usd_per_gb": cfg.starlink_ocean_mode_usd_per_gb,
        "starlink_notes": cfg.starlink_notes,
        "tmobile_plan": cfg.tmobile_plan,
        "tmobile_roaming_included": cfg.tmobile_roaming_included,
        "tmobile_notes": cfg.tmobile_notes,
        "maya_esim_scope": cfg.maya_esim_scope,
        "maya_esim_notes": cfg.maya_esim_notes,
        "router_model": cfg.router_model,
        "router_notes": cfg.router_notes,
    }


@app.get("/api/comms")
def get_comms() -> dict[str, Any]:
    try:
        return _comms_to_dict(load_comms_config(_comms_file()))
    except RoutingError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class CommsSaveIn(BaseModel):
    starlink_plan: str = ""
    starlink_billing_cycle_day: int | None = None
    starlink_priority_gb: float | None = None
    starlink_ocean_mode_usd_per_gb: float | None = None
    starlink_notes: str = ""
    tmobile_plan: str = ""
    tmobile_roaming_included: bool = False
    tmobile_notes: str = ""
    maya_esim_scope: str = ""
    maya_esim_notes: str = ""
    router_model: str = ""
    router_notes: str = ""


@app.put("/api/comms")
@app.post("/api/comms")
def save_comms(body: CommsSaveIn) -> dict[str, Any]:
    try:
        cfg = CommsConfig(**body.model_dump())
        save_comms_config(_comms_file(), cfg)
        return _comms_to_dict(load_comms_config(_comms_file()))
    except RoutingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/crew")
def get_crew_roster() -> list[dict[str, Any]]:
    """The regular-crew roster (`config/crew.toml`) — the float plan's real
    persons-on-board data (phone/DOB/PLB), as opposed to `crew`'s freeform
    display names. Empty list if the file doesn't exist yet."""
    roster = load_crew_roster(CONFIG_DIR / "crew.toml")
    return [
        {
            "key": key,
            "name": member.name,
            "phone": member.phone,
            "gender": member.gender,
            "dob": member.dob.isoformat() if member.dob else None,
            "plb_uin": member.plb_uin,
        }
        for key, member in sorted(roster.items())
    ]


@app.post("/api/brief")
def create_brief(
    route: UploadFile,
    depart: Annotated[str, Form()],
    speed: Annotated[float, Form()] = 6.5,
    country: Annotated[str | None, Form()] = None,
    crew: Annotated[str, Form()] = "Skipper,Mate",
    watch_hours: Annotated[float, Form()] = 6.0,
    logistics_markdown: Annotated[str | None, Form()] = None,
    float_plan: Annotated[bool, Form()] = False,
    float_plan_crew: Annotated[str | None, Form()] = None,
    food_days: Annotated[float | None, Form()] = None,
    fleet: Annotated[str | None, Form()] = None,
    compare: Annotated[bool, Form()] = False,
    no_tropical: Annotated[bool, Form()] = False,
    no_tides: Annotated[bool, Form()] = False,
    no_notices: Annotated[bool, Form()] = False,
    no_maintenance: Annotated[bool, Form()] = False,
    no_advisories: Annotated[bool, Form()] = False,
    no_papers: Annotated[bool, Form()] = False,
    papers_file: UploadFile | None = None,
    depths_file: UploadFile | None = None,
    ciq_file: UploadFile | None = None,
    comms_config: UploadFile | None = None,
) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    upload_path = UPLOAD_DIR / f"{run_id}_{route.filename or 'route.gpx'}"
    out_dir = BRIEFING_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    with upload_path.open("wb") as f:
        shutil.copyfileobj(route.file, f)

    logistics_path: Path | None = None
    if logistics_markdown and logistics_markdown.strip():
        logistics_path = UPLOAD_DIR / f"{run_id}_logistics.md"
        logistics_path.write_text(logistics_markdown, encoding="utf-8")

    # Optional override files (same as the CLI's --papers-file etc.): saved next
    # to the route upload for the duration of the run, removed in the finally.
    extra_paths: list[Path] = []

    def save_extra(up: UploadFile | None, tag: str) -> Path | None:
        if up is None or not up.filename:
            return None
        path = UPLOAD_DIR / f"{run_id}_{tag}_{Path(up.filename).name}"
        with path.open("wb") as f:
            shutil.copyfileobj(up.file, f)
        extra_paths.append(path)
        return path

    args = _brief_args(
        upload_path, out_dir,
        compare=compare, no_tropical=no_tropical, no_tides=no_tides,
        no_notices=no_notices, no_maintenance=no_maintenance, no_papers=no_papers,
        no_advisories=no_advisories,
        papers_file=save_extra(papers_file, "papers"),
        depths_file=save_extra(depths_file, "depths"),
        ciq_file=save_extra(ciq_file, "ciq"),
        comms_config=save_extra(comms_config, "comms"),
        depart=depart, speed=speed, country=country or None, crew=crew,
        watch_hours=watch_hours, logistics_file=logistics_path,
        float_plan=float_plan, float_plan_crew=float_plan_crew or None,
        food_days=food_days, fleet=fleet,
    )
    try:
        result: BriefResult = run_brief(args)
    except RoutingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        upload_path.unlink(missing_ok=True)
        if logistics_path is not None:
            logistics_path.unlink(missing_ok=True)
        for extra in extra_paths:
            extra.unlink(missing_ok=True)

    _RUNS[run_id] = result.paths
    a = result.analysis
    return {
        "id": run_id,
        "route_name": a.route_name,
        "total_nm": a.total_nm,
        "duration_h": a.duration_h,
        "sailing_fraction": a.sailing_fraction,
        "total_fuel_gal": a.total_fuel_gal,
        "depart": result.clock.label(a.depart, result.route.waypoints[0].lat,
                                      result.route.waypoints[0].lon),
        "eta": result.clock.label(a.eta, result.route.waypoints[-1].lat,
                                   result.route.waypoints[-1].lon),
        "warnings": result.warnings,
        "markdown": result.markdown,
        "files": sorted(result.paths.keys()),
    }


@app.get("/api/brief/{run_id}/download/{kind}")
def download_brief_file(run_id: str, kind: str) -> FileResponse:
    paths = _RUNS.get(run_id)
    if paths is None or kind not in paths:
        raise HTTPException(status_code=404, detail="no such briefing/file")
    path = paths[kind]
    if not path.exists():
        raise HTTPException(status_code=404, detail="file no longer on disk")
    return FileResponse(path, filename=path.name)


# Serve the built React app (frontend/dist) when present, so the whole thing
# runs from one process/port — same pattern as mx-inv's run-live.bat.
_dist = ROOT / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
