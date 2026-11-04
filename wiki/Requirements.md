<!--
Purpose: What passage-planner is for, who uses it, its requirements, constraints and non-goals - the
    "what must it do" page a rebuild would start from.
Version: v1 (2026-10-07) - new page for the rebuild-from-spec wiki pass; collected from the README,
    CHECKPOINT, Design-Decisions and the owner's recorded rules (sources named inline).
Scope: PRODUCTION - engineering wiki for passage-planner (
Architecture: passage-planner documentation. Linked from Home.md. How the requirements are met:
    Architecture.md, Logic-Flows.md; why: Design-Decisions.md.
Invocation: n/a (reference doc)
-->

# Requirements

## Purpose

passage-planner is the pre-departure passage planner for SV Example, a 1990 your vessel
cutter. Given a route drawn in Navionics (GPX) and a departure time, it produces one scannable
briefing that answers "should we go, when, and what do we need to know" for that passage: weather
along the route at the times the boat will be there, leg-by-leg sail/motor decisions from the boat's
polar, fuel and water, depth and tide gates, navigational warnings, arrival paperwork (customs,
immigration, State Department), shore logistics at each stop, maintenance due, comms plan, watch
plan and float plan.

The briefing follows the phases of the skipper's Departure/Arrival SOP (the README calls it the SOP "on
steroids"). It is decision support next to Windy and the NWS text forecasts, not a substitute for
professional routing (Roadmap, weather-routing note).

## Users

| User | Uses it for |
|---|---|
| the owner (skipper, owner) | Plans every passage; reads the briefing operationally (prepping to cast off or underway). Owns the requirements. |
| mate (crew, the owner's partner) | Reads the briefing; listed in the crew roster for the float plan. |
| Shore contact | Receives the float plan summary / USPS Float Plan PDF. |
| The cockpit tablet (mx-inv) | Reads the newest CLI briefing file for the watch-handover weather line (see [Data Flows](Data-Flows-and-Interfaces.md)). |
| Claude sessions | Maintain the code and the reference data (arrival places, CIQ, comms registries). |

## Functional requirements

1. **Route in, briefing out.** Accept a Navionics/OpenCPN GPX (`<rte>` or `<trk>`), a departure
   (local time at the first waypoint unless a zone is given) and a planning speed; write the briefing
   as `.md`, `.html` and `.docx`.
2. **Weather at the right time and place.** Forecast wind, gust, pressure, waves, swell and ocean
   current at evenly spaced points along the route at each point's ETA; re-time and re-fetch until
   the ETA is stable.
3. **Boat-specific performance.** Speed from the boat's polar by true wind angle and speed, capped at
   the practical hull maximum; the owner's light-air rule (sail down to about 4-5 kn, motor below) and
   upwind rule ("just motor into it if it makes sense") decide sail / motorsail / motor per leg.
   Current corrects speed over ground.
4. **Safety flags.** Wave height, sustained wind, gust, beating into head seas, wind against current,
   foul current, fleet speed below target - each a WARNING or CAUTION in the leg table and executive
   summary. Thresholds live in `config/vessel.toml`, not code.
5. **Fleet planning.** Plan shared stops for the boats travelling together: the deepest arrival /
   departure / anchoring draft (a lifting keel counts keel-up) and the tallest air draft govern.
6. **Depth and tide.** Per-waypoint depth check (NOAA ENC where it exists, GEBCO triage, saved chart
   depths elsewhere) against planning draft plus under-keel clearance, with tide height at ETA and a
   tide-gate window when marginal.
7. **Arrival paperwork.** Per-country customs/immigration dossier, U.S. State Department advisory and
   embassy, and a document-readiness check of the boat's papers for the trip type.
8. **Shore logistics.** Per-stop groceries, fuel dock depth, water, marina, haul-out limits, money,
   contacts, security awareness; researchable on demand ("Research with Claude"), always reviewed by a
   person before it goes into a briefing.
9. **Never silently incomplete.** Any optional feed that fails or is skipped renders an explicit
   PENDING block naming the manual check, so "checked, nothing found" is never confused with "never
   checked" (Design Decisions, PENDING pattern). One exception by design: the a marine forecaster block is
   omitted when no text was pasted (not subscribed is the normal state).
10. **Times local to the position.** Every timestamp is rendered in the civil zone of the place it
    describes.
11. **Float plan.** Fill the real USPS Float Plan PDF from the same passage data and the crew roster.
12. **Two front ends, one pipeline.** A CLI and a web form generate identical briefings.
13. **Optimisation (experimental).** Optimise a route inside a corridor around the drawn track and
    compare departure times (`solve`, `sweep`, CLI only).

## Constraints and standing rules (the owner's, recorded)

| Rule | Source |
|---|---|
| Briefing text is terse facts: numbers, thresholds, sources, what to check; no commentary or hedging. Limitations are flat callouts. | Design Decisions "Briefing text is terse facts" (2026-09-22) |
| The crew never edits config files: known lists are pick lists, data entry is a form with a Save button (arrival picker, depth check, fleet, documents, comms). | Design Decisions; Arrival Places origin |
| Planning draft 8.0 ft + 2.0 ft under-keel clearance; air draft 70 ft + 5 ft buffer (75 ft) - outside routes only, no ICW. | `config/vessel.toml` `[vessel.draft]`, `[vessel.air_draft]` |
| A lifting-keel buddy boat's keel-up draft governs arrival, departure and anchoring; keel-down is open water only. | Design Decisions "One fleet list"; `vessel.toml` Example Buddy notes |
| Research output is untrusted text: shown to a person for review, never executed, never auto-submitted. Consent on every run. | Logistics Research Design |
| Secrets only in environment variables, named by `*_env` keys; never in `config/` or the repo. | `vessel.toml` header |
| Paying for data is acceptable when it is genuinely better; currents and winds were judged free and good enough (2026-09-21). | Roadmap weather-routing note |
| The automation works with the skipper, never for him: suggestions to check in Navionics, never a safety claim. The solver's route is a suggestion. | Roadmap step 4; the owner's automation philosophy |
| Runs on Windows 11 (the laptop / Ship's PC) and must keep working when the boat LAN or the internet is partly down: each feed degrades on its own. | Design Decisions; Data Flows |
| LAN / tailnet trust boundary; no login of its own (same as mx-inv). | Design Decisions "No auth" |

## Vessel planning figures (inputs, all in `config/vessel.toml`)

Yanmar 4JH80 (80 hp); fuel about 200 gal (tank-vs-gauge not calibrated), 15% reserve; cruise 7.0 kn
at about 2.25 gph (PROVISIONAL), motorsail 1.6 gph (PROVISIONAL); fleet target average 7.0 kn;
light-air threshold 4.5 kn; hull max 9.0 kn; fresh water 150 gal at 3 gal/person/day (cooking and
hygiene; drinking water is bottled); watermaker installed, not commissioned; beam 14 ft 9 in;
displacement 35,000 lb (PROVISIONAL).

## Non-goals (decided; do not re-propose without new information)

- **An animated weather map.** the owner uses Windy for looking at weather (Roadmap).
- **A general PDF renderer.** `.docx`/`.html` only; PDF by export from Word (Design Decisions).
- **Scraping or fetching boat data from MarineTraffic / ORC.** Paste-in only (Design Decisions).
- **Unattended or scheduled research**, and research that writes anywhere but its own output.
- **A frontend router or client-state library** (one form, one result view).
- **A background weather-refresh daemon.** Every fetch is per briefing run.
- **USCG Local Notice to Mariners ingestion** (weekly PDF, no feed) - the briefing points at it.
- **Arrival places for Cuba, Haiti, Venezuela**, and tracking of unlisted places - deprioritised by
  the owner 2026-09-22 (CHECKPOINT v22).

Polar derivation was a non-goal while the builder's stack derived it; with that stack retired, who owns the
polar is open again - see [Roadmap](Roadmap.md).
