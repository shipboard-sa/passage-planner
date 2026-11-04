"""Document-readiness data model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class DocKind(StrEnum):
    VESSEL_DOCUMENTATION = "vessel_documentation"   # USCG COD / state registration
    RADIO_LICENCE = "radio_licence"                 # FCC ship station licence
    RADIO_OPERATOR = "radio_operator"               # restricted radiotelephone permit
    EPIRB_REGISTRATION = "epirb_registration"
    INSURANCE_POLICY = "insurance_policy"
    INSURANCE_PROOF = "insurance_proof"             # certificate / letter of compliance
    PASSPORT = "passport"
    BOATER_EDUCATION = "boater_education"
    FISHING_LICENCE = "fishing_licence"
    MARPOL_PLACARD = "marpol_placard"
    FLOAT_PLAN_TEMPLATE = "float_plan_template"
    CBP_DECAL = "cbp_decal"                         # DTOPS annual user-fee decal
    SURVEY = "survey"
    CREW_ID = "crew_id"
    DINGHY_REGISTRATION = "dinghy_registration"
    OTHER = "other"


KIND_LABEL: dict[DocKind, str] = {
    DocKind.VESSEL_DOCUMENTATION: "USCG Certificate of Documentation / registration",
    DocKind.RADIO_LICENCE: "FCC Ship Station Licence",
    DocKind.RADIO_OPERATOR: "Restricted Radiotelephone Operator Permit",
    DocKind.EPIRB_REGISTRATION: "406 EPIRB registration (NOAA)",
    DocKind.INSURANCE_POLICY: "Yacht insurance policy",
    DocKind.INSURANCE_PROOF: "Proof of insurance / letter of compliance",
    DocKind.PASSPORT: "Passports",
    DocKind.BOATER_EDUCATION: "Boater education card",
    DocKind.FISHING_LICENCE: "Fishing licence",
    DocKind.MARPOL_PLACARD: "MARPOL oil + trash placards (must be posted aboard)",
    DocKind.FLOAT_PLAN_TEMPLATE: "Float plan template",
    DocKind.CBP_DECAL: "CBP annual user-fee decal (DTOPS)",
    DocKind.SURVEY: "Current condition & valuation survey",
    DocKind.CREW_ID: "Crew photo ID",
    DocKind.DINGHY_REGISTRATION: "Dinghy registration",
    DocKind.OTHER: "Other",
}


class Status(StrEnum):
    PRESENT = "present"
    STALE = "stale"          # older than its renewal cycle, no explicit expiry
    EXPIRING = "expiring"    # explicit expiry falls before the passage window closes
    EXPIRED = "expired"
    MISSING = "missing"
    LEGACY_ONLY = "legacy_only"  # only stale copies from before the vessel was acquired


@dataclass(frozen=True, slots=True)
class PaperDoc:
    kind: DocKind
    title: str
    path: str
    modified: date
    is_current: bool                 # in a current subdir or newer than the cutoff
    issued: date | None = None       # from an override
    expires: date | None = None      # from an override
    note: str = ""


@dataclass(frozen=True, slots=True)
class DocFinding:
    kind: DocKind
    required: bool
    status: Status
    doc: PaperDoc | None
    message: str


@dataclass(frozen=True, slots=True)
class PapersReport:
    trip: str                        # "domestic" | "international"
    generated: date
    scanned: tuple[str, ...]
    findings: tuple[DocFinding, ...]
    unclassified_current: tuple[PaperDoc, ...] = ()

    @property
    def blocking(self) -> tuple[DocFinding, ...]:
        bad = {Status.MISSING, Status.EXPIRED, Status.LEGACY_ONLY}
        return tuple(f for f in self.findings if f.required and f.status in bad)

    @property
    def warnings(self) -> tuple[DocFinding, ...]:
        return tuple(
            f for f in self.findings
            if f.status in {Status.STALE, Status.EXPIRING}
            or (not f.required and f.status in {Status.MISSING, Status.LEGACY_ONLY})
        )

    @property
    def ok(self) -> tuple[DocFinding, ...]:
        return tuple(f for f in self.findings if f.status is Status.PRESENT)
