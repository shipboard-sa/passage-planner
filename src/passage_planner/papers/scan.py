"""Walk the papers directories and classify what's there.

Classification is filename-pattern based. A file counts as *current* if it sits
in a known current subdirectory (``Purchase``, ``Concept 2025``, ``FCC``,
``IDs``) or its mtime is on/after the vessel-acquisition cutoff. Files whose
name matches an obsolete pattern (COVID, health visa, pet import, the prior
boat) are dropped entirely — they never enter a checklist.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path

from ..errors import PapersError
from .model import DocKind, PaperDoc

_CURRENT_SUBDIRS = {"purchase", "concept 2025", "fcc", "ids"}

# Never classified — obsolete, blank forms, or unrelated.
_OBSOLETE = [
    re.compile(p, re.I) for p in (
        r"covid", r"covod", r"vacrecord", r"booster", r"bahamatravelhealth",
        r"bahamastravelhealth", r"pet.?import", r"pets?_?imp", r"pet2_?imp",
        r"rabiesrecord", r"petshealthrecord", r"bahamas-pet",
        r"first ?47", r"beneteau", r"kemah", r"red fish underwater", r"\bpp0\b",
        r"lithium-whitepaper", r"hylas51_automation", r"hylas51_sparing",
        r"dominionpower", r"aqua finance", r"^inventr?o?y", r"questions for mike",
        r"selfiewith", r"thumbtack", r"anchoringrequest", r"debitauth", r"reportinvoice",
        # blank CBP forms / applications, not the credential itself
        r"cbp form (1300|6059|i-418)", r"form.?6059", r"cbp_?form_?i-418", r"form.?i-418",
        r"schedule_b", r"fcc-form-603", r"fcc registration system", r"^159\b",
        r"renewal quotation", r"renewal questionnaire", r"claim notification",
        r"add an operator", r"damage estimate", r"storm ?case", r"estimate\d",
        r"payment instructions", r"^applciation", r"^application form",
    )
]

_CLASSIFIERS: list[tuple[re.Pattern[str], DocKind]] = [
    (re.compile(r"(coast[_ ]?guard[_ ]?cod|uscg[_ ]?co[gd]|certificate.*documentation"
                r"|uscg certificate|cg-5397)", re.I), DocKind.VESSEL_DOCUMENTATION),
    (re.compile(r"(virginia dwr|watercraft registration|view registration"
                r"|vessel.*registration)", re.I), DocKind.VESSEL_DOCUMENTATION),
    (re.compile(r"(uls license|ship station|radio.?station.?auth|fcc ?605|fcc-605"
                r"|ship recreational)", re.I), DocKind.RADIO_LICENCE),
    (re.compile(r"(restricted radiotelephone|radiotelephone operator"
                r"|fcc[_ ]?radio[_ ]?operation)", re.I), DocKind.RADIO_OPERATOR),
    (re.compile(r"epirb", re.I), DocKind.EPIRB_REGISTRATION),
    (re.compile(r"(policy ?- ?20\d\d|insurance.?binder|concept binder|owner_name.*binder"
                r"|\bh0_1\b|229405)", re.I), DocKind.INSURANCE_POLICY),
    (re.compile(r"(letter[_ ]?of[_ ]?compliance|\bloc\b|surplus lines|windstorm plan"
                r"|named storm plan|certificate of insurance)", re.I), DocKind.INSURANCE_PROOF),
    (re.compile(r"passport", re.I), DocKind.PASSPORT),
    (re.compile(r"(safe ?boater|boatingsafetycert|boater.?cert|vhf course"
                r"|boating.?safety)", re.I), DocKind.BOATER_EDUCATION),
    (re.compile(r"fishing ?licen[sc]e", re.I), DocKind.FISHING_LICENCE),
    (re.compile(r"(oilplacard|trashplacard|marpol|placard)", re.I), DocKind.MARPOL_PLACARD),
    (re.compile(r"float ?plan ?template", re.I), DocKind.FLOAT_PLAN_TEMPLATE),
    (re.compile(r"(dtops|user.?fee.?decal|checkout payment confirmation)", re.I),
     DocKind.CBP_DECAL),
    (re.compile(r"(survey|c&v|condition.*valuation|kms survey)", re.I), DocKind.SURVEY),
    (re.compile(r"(drivers?[_ ]?licens|_id\b|_va_?id|milid|-id(front|back)|_idback"
                r"|_idfront|twic)", re.I), DocKind.CREW_ID),
    (re.compile(r"(dingy|dinghy|wombat)", re.I), DocKind.DINGHY_REGISTRATION),
]

_DOC_EXTS = {".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png", ".jfif", ".xlsx"}


def _classify(name: str) -> DocKind | None:
    for pat in _OBSOLETE:
        if pat.search(name):
            return None
    for pat, kind in _CLASSIFIERS:
        if pat.search(name):
            return kind
    return None


def _title_for(kind: DocKind, path: Path) -> str:
    return path.stem.replace("_", " ").strip()


def scan_documents(dirs: list[Path], *, current_after: date) -> list[PaperDoc]:
    """Classified documents across ``dirs``, de-duplicated (best copy per (kind, key))."""
    best: dict[tuple[DocKind, str], PaperDoc] = {}
    scanned_any = False

    for root in dirs:
        if not root.is_dir():
            continue
        scanned_any = True
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in _DOC_EXTS:
                continue
            if any(part.lower() == "garmin" for part in p.parts):
                continue
            kind = _classify(p.name)
            if kind is None:
                continue
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=UTC).date()
            except OSError:
                continue
            parent = p.parent.name.lower()
            is_current = parent in _CURRENT_SUBDIRS or mtime >= current_after
            doc = PaperDoc(
                kind=kind,
                title=_title_for(kind, p),
                path=str(p),
                modified=mtime,
                is_current=is_current,
            )
            # Key by a normalised stem so root + subdir copies collapse.
            key = (kind, re.sub(r"[^a-z0-9]", "", p.stem.lower())[:24])
            existing = best.get(key)
            if existing is None or _better(doc, existing):
                best[key] = doc

    if not scanned_any:
        raise PapersError(f"none of the papers directories exist: {[str(d) for d in dirs]}")
    return sorted(best.values(), key=lambda d: (d.kind.value, d.modified), reverse=False)


def _better(a: PaperDoc, b: PaperDoc) -> bool:
    """Prefer current over legacy, then newer."""
    if a.is_current != b.is_current:
        return a.is_current
    return a.modified > b.modified
