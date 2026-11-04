<!--
Purpose: Design and maintenance notes for the Caribbean arrival-places pick list and the "Arrival & stops" picker in the web form.
Version: v3 (2026-10-07) - status refreshed: stop roles (check in / check out / fuel) on picked stops, unlisted-place tracking dropped by the owner 2026-09-22, counts re-checked against the file. v2 (2026-09-21) - schema, loader, merge tooling, API and picker built; all three tranches researched (310 places, 34 countries), awaiting the skipper's review.
Scope: PRODUCTION - documents a live part of passage-planner (
Architecture: routing subsystem. Data file data/arrival_places.toml, loaded by src/passage_planner/arrival.py, served by backend/main.py (/api/arrival-places), consumed by frontend/src/ArrivalPicker.tsx (hosted in App.tsx). Orthogonal to ciq/ (country dossier) and logistics.py (per-port research).
Invocation: n/a (reference doc); the data file is validated/merged with `python -m passage_planner.arrival [merge] data/arrival_places.toml [FILE.toml ...]`.
-->

# Arrival places (pick list)

Origin: 2026-09-21. Typing a destination country and port names into two free-text fields
produced typos, ambiguous names ("Eleuthera") and blank briefing sections. The crew works in
Word, so the fix is a **pick list** of the normal arrival places by country and island, not
another field to type into.

## What the list is (and is not)

Stable **identity** facts only, one `[[place]]` per harbour or anchorage:

| Field | Meaning |
|---|---|
| `id` | `<iso2>-<slug>`, e.g. `gd-tyrell-bay`; the prefix must match `country` |
| `name`, `island`, `country`, `region` | how a cruiser names it; `island` is the coast for a mainland place |
| `lat`, `lon` | the harbour or anchorage itself (used to suggest places near a route's end) |
| `kind` | `port_of_entry` (customs and immigration clear a yacht here) / `harbour` (a normal stop, clear elsewhere) / `anchorage` |
| `clearance` | one factual line on how a yacht clears in, or where it must clear instead |
| `also_known_as` | alternate spellings, searched by the picker |
| `sources`, `verified` | URLs actually read, and the date checked |

Anything that changes stays **just-in-time research**: fees, hours, phone numbers, pre-arrival
systems, depths, marina details, and security incidents. The list never carries those, so it does
not go stale the way the CIQ registry can.

The label shown in the picker (and used as the port name everywhere downstream) is
`Name, Island (Country)`, e.g. `Tyrell Bay, Carriacou (Grenada)`.

## Where it is used

- **The picker** (`ArrivalPicker.tsx`), country first: choose a country (only countries that have
  places, with counts), then its places grouped by region (the Bahamas: Abacos, Exumas, ...) or by
  island where a country has one region (Grenada: Carriacou, Grenada, Petite Martinique), with a
  filter box for the long lists; repeat for each country you stop in. A name search across all
  countries (alias, island, region or country) stays as a shortcut. Chips
  for the chosen arrival and stops; "Other (not on the list)" with a country dropdown for anything
  unlisted (marked, and it still flows to research); and, once a route GPX is chosen, "Your route
  ends near ..." from `POST /api/arrival-places/suggest` (nearest listed places, 60 NM cap).
  Each chosen stop can be marked **Check in**, **Check out** or **Fuel**; the role is sent to the
  researcher (`roles`, `research.STOP_ROLES`) so it looks for the clearance office, outbound
  clearance or fuel-dock details respectively.
- **Destination country** for the arrival dossier (`--to`): derived from the chosen stops, no
  longer typed. Countries outside `ciq/`'s built-in seven get its "fill from official
  sources" checklist. Automated arrival/immigration research for them was dropped from tracking
  on 2026-09-22 (the "Other" + research-on-demand path covers it); not built.
- **Shore-logistics ports**: the template and the "Research with Claude" button work on the chosen
  stops. Names are posted newline-separated because a picked name contains a comma; see
  `logistics.split_ports()` (newline or semicolon when present, else commas, so the CLI's
  `--ports "A,B"` still works).

## Maintenance

- The file is written and refreshed by Claude sessions from government customs pages, Noonsite and
  cruising guides; the crew never edits it. Research agents each write one per-country file;
  `python -m passage_planner.arrival merge data/arrival_places.toml FILE.toml ...` validates all of
  them together (a place in two files is an error, not an overwrite), warns about two places closer
  than 0.3 NM (usually one place entered twice), and writes the canonical, sorted file.
  `python -m passage_planner.arrival FILE.toml` just validates.
- The loader enforces, per entry: a known country code that matches `country`, a valid `kind`, real
  URLs, an ISO date, and **a position inside a coarse box for that country** (`COUNTRY_BOXES`), which
  catches a wrong sign, swapped digits or a place filed under the wrong country. `tests/test_arrival.py`
  runs all of this on the shipped file.
- Allowed countries are the `COUNTRIES` table in `arrival.py` (all planned tranches are already in
  it). Cuba, Haiti and Venezuela are deliberately absent for now.
- Re-verify entries when a country changes its rules; `verified` says when each was last checked.

## Current data (2026-09-21)

310 places in 34 countries and territories: 161 ports of entry, 69 harbours, 80 anchorages. Built by
eight research agents from official customs and tourism pages, Noonsite (where readable), cruising
guides, Wikipedia and OpenStreetMap. Known limits, all recorded in each entry's `clearance` text:
32 entries say clearing status is "not confirmed" or should be confirmed (11 of them Bahamas, 5
Grenada, where two official sources disagree on which ports clear yachts in); coordinates are
harbour-level for most but only town or island points for some (fine for "nearest to your route's
end", not for navigation); Noonsite's clearance pages are members-only, so its detail is thin.
A Word review sheet (one table per country, uncertain rows shaded, a column for the skipper's
mark) is generated from the data for review, not stored in the repo.

The near-duplicate check found one real error during the merge: Petite Martinique had been filed
about 1 NM north of its true position, on top of Petit St Vincent (corrected against Wikipedia).
The remaining warnings (three neighbouring Chaguaramas boatyards) are genuine separate places.

## Coverage plan

1. Tranche 1 (done): Bahamas, Turks and Caicos, Dominican Republic, Puerto Rico, US and British Virgin
   Islands.
2. Tranche 2 (done): Jamaica, Cayman Islands, the Leewards and Windwards (Anguilla, Sint Maarten,
   Saint-Martin, Saint-Barthelemy, St Kitts and Nevis, Antigua and Barbuda, Montserrat, Guadeloupe,
   Dominica, Martinique, St Lucia, St Vincent and the Grenadines, Barbados, Grenada) and Trinidad
   and Tobago.
3. Tranche 3 (done): Curacao, Aruba, Bonaire, and the Central American Caribbean coast: Colombia, Panama,
   Costa Rica, Nicaragua, Honduras, Belize, Guatemala, Mexico (Yucatan).

Not yet on the list: the US east and Gulf coast (departure and re-entry ports such as Key West,
Miami, Fort Lauderdale), Cuba, Haiti, Venezuela. A "request a new place" path for stops entered
under Other was considered and dropped from tracking by the owner on 2026-09-22 (not blocked, just
deprioritised); the US ports and the skipper's review stay on the [Roadmap](Roadmap.md).

## Related: State Department block

The destination countries derived from the picked stops also drive a State Department block in
the arrival dossier (advisory level, embassy contact), produced deterministically by
`consular.py`; see [Data Flows](Data-Flows-and-Interfaces.md#3-inbound-external-public-apis).
It works for every country in the list, unlike the CIQ registry.

## Security field (situational awareness)

Shore-logistics research now has a checked field, **Security / recent incidents**: the model
searches the Caribbean Safety & Security Net (thecssn.org, incident search by country and keyword,
plus its map) and Noonsite's piracy and security page, and reports the count of reports in the last
12 months, the date of the most recent and the usual type (mostly dinghy and outboard theft). It is
framed as awareness, never a verdict: "no reports found in <period>" is not "safe", and the prompt
forbids saying otherwise. Kept in the just-in-time research, not in this list, because incident
reports change week to week.
