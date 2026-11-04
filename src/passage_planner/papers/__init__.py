"""Document readiness: scan the papers-of-record, check against a per-trip
required-documents matrix, flag gaps and lapsing paperwork.

Filenames classify the documents; a ``papers.toml`` (hand- or Claude-populated
from reading the PDFs) supplies issue/expiry dates so the briefing can flag
anything that lapses before or during a passage.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

from .checklist import assess_papers
from .model import (
    KIND_LABEL,
    DocFinding,
    DocKind,
    PaperDoc,
    PapersReport,
    Status,
)
from .overrides import load_overrides, save_overrides
from .scan import scan_documents

__all__ = [
    "KIND_LABEL",
    "DocFinding",
    "DocKind",
    "PaperDoc",
    "PapersReport",
    "Status",
    "assess_papers",
    "check_papers",
    "load_overrides",
    "save_overrides",
    "scan_documents",
]


def check_papers(
    dirs: Sequence[str | Path],
    *,
    trip: str,
    passage_end: date,
    acquired_after: date,
    overrides_path: str | Path | None = None,
    buffer_days: int = 30,
) -> PapersReport:
    """Scan + assess in one call."""
    docs = scan_documents([Path(d) for d in dirs], current_after=acquired_after)
    overrides = load_overrides(overrides_path) if overrides_path else {}
    return assess_papers(
        docs, overrides, trip=trip, passage_end=passage_end, buffer_days=buffer_days
    )
