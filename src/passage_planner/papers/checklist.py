"""Assess scanned documents against a per-trip required-documents matrix."""

from __future__ import annotations

from datetime import date, timedelta

from .model import KIND_LABEL, DocFinding, DocKind, PaperDoc, PapersReport, Status

# Renewal cycle in months — used to flag a "stale" doc when no explicit expiry
# is on file. None = doesn't lapse.
_RENEWAL_MONTHS: dict[DocKind, int | None] = {
    DocKind.VESSEL_DOCUMENTATION: 60,  # CG-1270 certs now carry a 5-year expiry
    DocKind.RADIO_LICENCE: 120,
    DocKind.RADIO_OPERATOR: None,
    DocKind.EPIRB_REGISTRATION: 24,
    DocKind.INSURANCE_POLICY: 12,
    DocKind.INSURANCE_PROOF: 12,
    DocKind.PASSPORT: 120,
    DocKind.BOATER_EDUCATION: None,
    DocKind.FISHING_LICENCE: 12,
    DocKind.MARPOL_PLACARD: None,
    DocKind.FLOAT_PLAN_TEMPLATE: None,
    DocKind.CBP_DECAL: 12,
    DocKind.SURVEY: None,
    DocKind.CREW_ID: None,
    DocKind.DINGHY_REGISTRATION: 12,
}

# (kind, required?) per trip.
_DOMESTIC: list[tuple[DocKind, bool]] = [
    (DocKind.VESSEL_DOCUMENTATION, True),
    (DocKind.INSURANCE_POLICY, True),
    (DocKind.INSURANCE_PROOF, False),
    (DocKind.MARPOL_PLACARD, True),
    (DocKind.FLOAT_PLAN_TEMPLATE, True),
    (DocKind.EPIRB_REGISTRATION, False),
    (DocKind.RADIO_LICENCE, False),
    (DocKind.BOATER_EDUCATION, False),
    (DocKind.SURVEY, False),
]
_INTERNATIONAL: list[tuple[DocKind, bool]] = [
    (DocKind.VESSEL_DOCUMENTATION, True),
    (DocKind.INSURANCE_POLICY, True),
    (DocKind.INSURANCE_PROOF, True),
    (DocKind.MARPOL_PLACARD, True),
    (DocKind.FLOAT_PLAN_TEMPLATE, True),
    (DocKind.RADIO_LICENCE, True),
    (DocKind.RADIO_OPERATOR, False),
    (DocKind.EPIRB_REGISTRATION, True),
    (DocKind.PASSPORT, True),
    (DocKind.CBP_DECAL, True),        # for the US return
    (DocKind.BOATER_EDUCATION, False),
    (DocKind.SURVEY, False),
]

def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def assess_papers(
    docs: list[PaperDoc],
    overrides: dict[DocKind, PaperDoc],
    *,
    trip: str,
    passage_end: date,
    buffer_days: int = 30,
    today: date | None = None,
) -> PapersReport:
    today = today or date.today()
    horizon = passage_end + timedelta(days=buffer_days)
    matrix = _INTERNATIONAL if trip == "international" else _DOMESTIC

    by_kind: dict[DocKind, list[PaperDoc]] = {}
    for d in docs:
        by_kind.setdefault(d.kind, []).append(d)

    findings: list[DocFinding] = []
    for kind, required in matrix:
        ov = overrides.get(kind)
        candidates = by_kind.get(kind, [])
        lapses = _RENEWAL_MONTHS.get(kind) is not None
        # A doc that never lapses is fine at any age; only date-gate the ones
        # that renew.
        pool = [d for d in candidates if d.is_current] if lapses else candidates
        chosen = ov or (max(pool, key=lambda d: d.modified) if pool else None)
        legacy_only = lapses and not pool and bool(candidates)

        status, msg = _evaluate(kind, chosen, legacy_only, today, horizon)
        findings.append(DocFinding(kind, required, status, chosen, msg))

    covered = {k for k, _ in matrix}
    extras = tuple(
        d for d in docs
        if d.is_current and d.kind not in covered and d.kind is not DocKind.OTHER
    )
    return PapersReport(
        trip=trip,
        generated=today,
        scanned=tuple(sorted({str(d.path).rsplit("\\", 1)[0] for d in docs})[:8]),
        findings=tuple(findings),
        unclassified_current=extras,
    )


def _evaluate(
    kind: DocKind,
    doc: PaperDoc | None,
    legacy_only: bool,
    today: date,
    horizon: date,
) -> tuple[Status, str]:
    label = KIND_LABEL.get(kind, kind.value)
    if doc is None:
        if legacy_only:
            return Status.LEGACY_ONLY, f"{label}: only pre-2025 copies found — get the current one."
        return Status.MISSING, f"{label}: not found in the papers directories."

    if doc.expires is not None:
        if doc.expires < today:
            return Status.EXPIRED, f"{label}: **expired {doc.expires:%Y-%m-%d}**."
        if doc.expires <= horizon:
            return Status.EXPIRING, (
                f"{label}: expires {doc.expires:%Y-%m-%d} — renew before departure."
            )
        return Status.PRESENT, f"{label}: on file, valid to {doc.expires:%Y-%m-%d}."

    cycle = _RENEWAL_MONTHS.get(kind)
    if cycle is not None and _months_between(doc.modified, today) >= cycle:
        return Status.STALE, (
            f"{label}: newest copy is {doc.modified:%Y-%m-%d} — past a ~{cycle}-month "
            "renewal cycle; confirm it's current (add the expiry to papers.toml)."
        )
    return Status.PRESENT, f"{label}: on file ({doc.modified:%Y-%m-%d})."
