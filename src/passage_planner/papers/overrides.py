"""``papers.toml`` — hand- or Claude-supplied issue/expiry dates per document.

    [documents."USCG Certificate of Documentation"]
    kind    = "vessel_documentation"
    path    = "../Papers of Record/Purchase/USCG Certificate 21 Apr 2025.pdf"
    issued  = "2025-04-21"
    expires = "2026-04-30"
    note    = ""

Reading an expiry off a PDF is a conversation step; this file is where the
answer lands so the briefing can act on it.

Purpose: load and (as of 2026-09-22) save config/papers.toml's per-DocKind
    overrides.
Version: v2 (2026-09-22) - added save_overrides() for the web form's
    Documents screen (backend/main.py's /api/papers), the atomic-write
    counterpart to load_overrides(). Only one override per DocKind is ever
    meaningful (load_overrides keys its result by kind), so the TOML table's
    title is always KIND_LABEL[kind] - never a user-editable field.
Scope: PRODUCTION - passage-planner's document-readiness overrides, read by
    checklist.py's assess_papers() and written by the web form.
Architecture: passage_planner.papers subsystem. Pure functions, no dependency
    on scan.py (which reads the real Papers of Record filesystem tree) -
    save_overrides() never touches that tree, only config/papers.toml.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from datetime import date
from pathlib import Path

from ..errors import PapersError
from .model import KIND_LABEL, DocKind, PaperDoc


def _as_date(value: object, ctx: str) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise PapersError(f"{ctx}: not an ISO date: {value!r}") from exc


def load_overrides(path: str | Path) -> dict[DocKind, PaperDoc]:
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PapersError(f"{p}: {exc}") from exc

    table = raw.get("documents", {})
    if not isinstance(table, dict):
        raise PapersError(f"{p}: [documents] must be a table")

    out: dict[DocKind, PaperDoc] = {}
    for title, entry in table.items():
        if not isinstance(entry, dict):
            raise PapersError(f"{p}: [documents.{title}] must be a table")
        kind_raw = str(entry.get("kind", "")).strip().lower()
        try:
            kind = DocKind(kind_raw)
        except ValueError as exc:
            raise PapersError(
                f"{p}: [documents.{title}] unknown kind {kind_raw!r}"
            ) from exc
        issued = _as_date(entry.get("issued"), f"{p}:{title}.issued")
        expires = _as_date(entry.get("expires"), f"{p}:{title}.expires")
        modified = expires or issued or date.today()
        out[kind] = PaperDoc(
            kind=kind,
            title=str(title),
            path=str(entry.get("path", "")),
            modified=modified,
            is_current=True,
            issued=issued,
            expires=expires,
            note=str(entry.get("note", "")),
        )
    return out


def _toml_str(value: str) -> str:
    """A TOML string literal for ``value``: a multi-line basic string
    (``\"\"\"..\"\"\"``) when it contains a newline - a multi-paragraph note
    stays readable on disk instead of losing its blank lines - falling back to
    a quoted single-line string when it doesn't, or when it contains a literal
    ``\"\"\"`` a multi-line string can't safely embed. Either way, backslashes
    are escaped and any other control character is replaced with a space."""
    escaped = value.replace("\\", "\\\\")
    if "\n" in escaped and '"""' not in escaped:
        body = "".join(c if c == "\n" or c >= " " or c == "\t" else " " for c in escaped)
        return '"""\n' + body + '"""'
    single_line = "".join(c if c >= " " or c == "\t" else " " for c in escaped).replace(
        '"', '\\"'
    )
    return '"' + single_line + '"'


def _validate_date(value: str, ctx: str) -> str:
    """``value`` (already a plain string from the form) as a confirmed ISO
    date, or ``""`` when blank."""
    text = value.strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise PapersError(f"{ctx}: not an ISO date (YYYY-MM-DD): {value!r}") from exc


def save_overrides(path: str | Path, entries: Mapping[DocKind, Mapping[str, str]]) -> None:
    """Write ``documents`` overrides atomically from the web form's per-kind
    fields (``path``, ``issued``, ``expires``, ``note`` - all plain strings).
    A kind whose fields are all blank is dropped (clears any prior override).
    The TOML table key is always ``KIND_LABEL[kind]`` - :func:`load_overrides`
    only ever reads ``kind``, so the title is just a human-readable label and
    never needs to be user-editable or round-tripped from the old file."""
    lines = [
        "# Hand- or Claude-supplied issue/expiry dates per document - see",
        "# overrides.py's module docstring for the shape. Written by the",
        "# Documents form; a stray manual edit here is still read normally.",
        "",
    ]
    for kind in DocKind:
        fields = entries.get(kind)
        if not fields:
            continue
        doc_path = str(fields.get("path", "")).strip()
        issued = _validate_date(str(fields.get("issued", "")), f"{kind.value}.issued")
        expires = _validate_date(str(fields.get("expires", "")), f"{kind.value}.expires")
        note = str(fields.get("note", "")).strip()
        if not (doc_path or issued or expires or note):
            continue
        title = KIND_LABEL.get(kind, kind.value)
        lines.append(f'[documents.{_toml_str(title)}]')
        lines.append(f"kind = {_toml_str(kind.value)}")
        if doc_path:
            lines.append(f"path = {_toml_str(doc_path)}")
        if issued:
            lines.append(f"issued = {_toml_str(issued)}")
        if expires:
            lines.append(f"expires = {_toml_str(expires)}")
        if note:
            lines.append(f"note = {_toml_str(note)}")
        lines.append("")

    p = Path(path)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    os.replace(tmp, p)
