"""Tests for the papers subpackage."""

from __future__ import annotations

import os
import textwrap
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from passage_planner.errors import PapersError
from passage_planner.papers import DocKind, Status, check_papers, load_overrides, save_overrides
from passage_planner.papers.scan import _classify, scan_documents

CUTOFF = date(2025, 1, 1)


def _touch(p: Path, when: date) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"%PDF-1.4 test")
    epoch = time.mktime(when.timetuple())
    os.utime(p, (epoch, epoch))


# -- classification ------------------------------------------------


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("USCG Certificate 21 Apr 2025.pdf", DocKind.VESSEL_DOCUMENTATION),
        ("Coast_Guard_COD.pdf", DocKind.VESSEL_DOCUMENTATION),
        ("ULS License - Ship Recreational - ABC1234.pdf", DocKind.RADIO_LICENCE),
        ("EPIRB_registration_Letter.pdf", DocKind.EPIRB_REGISTRATION),
        ("Policy - 2025.pdf", DocKind.INSURANCE_POLICY),
        ("letter_of_compliance.pdf", DocKind.INSURANCE_PROOF),
        ("Lee_Passport.pdf", DocKind.PASSPORT),
        ("oilplacard.pdf", DocKind.MARPOL_PLACARD),
        ("float plan template.pdf", DocKind.FLOAT_PLAN_TEMPLATE),
        ("CBP DTOPS - Checkout Payment Confirmation.pdf", DocKind.CBP_DECAL),
        ("25-034 EXAMPLE BOAT_51 Hylas_Jane Skipper_C&V.pdf", DocKind.SURVEY),
    ],
)
def test_classify(name: str, kind: DocKind) -> None:
    assert _classify(name) is kind


@pytest.mark.parametrize(
    "name",
    ["Lee_BahamaTravelHealthVisa.pdf", "Gabby_RabiesRecord.pdf", "Pets_IMP1.pdf",
     "Owner-Document.pdf", "SailboatData.com - FIRST 47.pdf"],
)
def test_obsolete_names_are_dropped(name: str) -> None:
    assert _classify(name) is None


# -- scan de-dup and currency ------------------------------------


def test_checklist_picks_the_current_copy_over_legacy(tmp_path: Path) -> None:
    _touch(tmp_path / "Coast_Guard_COD.pdf", date(2022, 1, 29))
    _touch(tmp_path / "Purchase" / "USCG Certificate 21 Apr 2025.pdf", date(2025, 5, 15))
    _touch(tmp_path / "oilplacard.pdf", date(2024, 1, 1))
    _touch(tmp_path / "trashplacard.pdf", date(2024, 1, 1))
    _touch(tmp_path / "float plan template.pdf", date(2024, 1, 1))
    _touch(tmp_path / "Insurance" / "Policy - 2025.pdf", date(2026, 6, 1))
    report = check_papers(
        [tmp_path], trip="domestic",
        passage_end=date.today() + timedelta(days=3), acquired_after=CUTOFF,
    )
    cod = next(f for f in report.findings if f.kind is DocKind.VESSEL_DOCUMENTATION)
    assert cod.doc is not None and "2025" in cod.doc.path


def test_scan_de_dups_root_and_subdir_copies(tmp_path: Path) -> None:
    _touch(tmp_path / "Lee_Passport.pdf", date(2022, 3, 15))
    _touch(tmp_path / "IDs" / "Lee_Passport.pdf", date(2022, 3, 15))
    docs = scan_documents([tmp_path], current_after=CUTOFF)
    assert len([d for d in docs if d.kind is DocKind.PASSPORT]) == 1


def test_scan_missing_dirs_raises(tmp_path: Path) -> None:
    with pytest.raises(PapersError, match="none of the papers"):
        scan_documents([tmp_path / "nope"], current_after=CUTOFF)


# -- checklist ---------------------------------------------------


def _fixture_dir(tmp_path: Path) -> Path:
    _touch(tmp_path / "Purchase" / "USCG Certificate.pdf", date(2026, 5, 1))
    _touch(tmp_path / "FCC" / "ULS License ABC1234.pdf", date(2026, 3, 31))
    _touch(tmp_path / "Insurance" / "Policy - 2025.pdf", date(2026, 6, 1))
    _touch(tmp_path / "letter_of_compliance.pdf", date(2026, 3, 20))
    _touch(tmp_path / "oilplacard.pdf", date(2024, 4, 16))       # non-lapsing, old ok
    _touch(tmp_path / "float plan template.pdf", date(2022, 11, 12))
    _touch(tmp_path / "Lee_Passport.pdf", date(2022, 3, 15))
    # in a "current" subdir but well past its 24-month cycle -> STALE (not legacy)
    _touch(tmp_path / "FCC" / "EPIRB_registration_Letter.pdf", date(2024, 2, 1))
    # no DTOPS decal at all
    return tmp_path


def test_international_checklist_flags_gaps_and_staleness(tmp_path: Path) -> None:
    report = check_papers(
        [_fixture_dir(tmp_path)],
        trip="international",
        passage_end=date.today() + timedelta(days=7),
        acquired_after=CUTOFF,
    )
    by_kind = {f.kind: f.status for f in report.findings}
    assert by_kind[DocKind.CBP_DECAL] is Status.MISSING
    assert by_kind[DocKind.EPIRB_REGISTRATION] is Status.STALE
    assert by_kind[DocKind.MARPOL_PLACARD] is Status.PRESENT      # non-lapsing, 2024 ok
    assert by_kind[DocKind.FLOAT_PLAN_TEMPLATE] is Status.PRESENT
    assert by_kind[DocKind.VESSEL_DOCUMENTATION] is Status.PRESENT
    assert any(f.kind is DocKind.CBP_DECAL for f in report.blocking)


def test_domestic_is_more_lenient(tmp_path: Path) -> None:
    report = check_papers(
        [_fixture_dir(tmp_path)], trip="domestic",
        passage_end=date.today() + timedelta(days=3), acquired_after=CUTOFF,
    )
    kinds = {f.kind for f in report.findings}
    assert DocKind.PASSPORT not in kinds      # not required domestically
    assert DocKind.CBP_DECAL not in kinds


def test_override_expiry_drives_status(tmp_path: Path) -> None:
    _fixture_dir(tmp_path)
    ov = tmp_path / "papers.toml"
    ov.write_text(textwrap.dedent("""
        [documents."Passports"]
        kind = "passport"
        expires = "2020-01-01"
    """).lstrip(), encoding="utf-8")
    report = check_papers(
        [tmp_path], trip="international",
        passage_end=date.today() + timedelta(days=3), acquired_after=CUTOFF,
        overrides_path=ov,
    )
    passport = next(f for f in report.findings if f.kind is DocKind.PASSPORT)
    assert passport.status is Status.EXPIRED
    assert "expired" in passport.message.lower()


def test_load_overrides_rejects_bad_kind(tmp_path: Path) -> None:
    ov = tmp_path / "p.toml"
    ov.write_text('[documents."X"]\nkind = "not_a_kind"\n', encoding="utf-8")
    with pytest.raises(PapersError, match="unknown kind"):
        load_overrides(ov)


# -- save_overrides (the Documents form's write side) ------------------------


def test_save_overrides_round_trips_through_load_overrides(tmp_path: Path) -> None:
    p = tmp_path / "papers.toml"
    save_overrides(p, {
        DocKind.PASSPORT: {
            "path": "../Papers of Record/Passports/scan.pdf",
            "issued": "2020-05-01",
            "expires": "2030-05-01",
            "note": "both crew",
        },
        DocKind.CBP_DECAL: {"expires": "2027-01-01"},
    })
    loaded = load_overrides(p)
    assert set(loaded) == {DocKind.PASSPORT, DocKind.CBP_DECAL}
    passport = loaded[DocKind.PASSPORT]
    assert passport.path == "../Papers of Record/Passports/scan.pdf"
    assert passport.issued == date(2020, 5, 1)
    assert passport.expires == date(2030, 5, 1)
    assert passport.note == "both crew"
    assert passport.title == "Passports"  # KIND_LABEL, not user-supplied
    assert loaded[DocKind.CBP_DECAL].expires == date(2027, 1, 1)


def test_save_overrides_drops_an_all_blank_kind(tmp_path: Path) -> None:
    p = tmp_path / "papers.toml"
    save_overrides(p, {DocKind.PASSPORT: {"expires": "2030-01-01"}, DocKind.SURVEY: {}})
    assert set(load_overrides(p)) == {DocKind.PASSPORT}


def test_save_overrides_clears_a_previously_saved_kind(tmp_path: Path) -> None:
    p = tmp_path / "papers.toml"
    save_overrides(p, {DocKind.PASSPORT: {"expires": "2030-01-01"}})
    save_overrides(p, {})  # form submitted with everything cleared
    assert load_overrides(p) == {}


def test_save_overrides_rejects_a_bad_date(tmp_path: Path) -> None:
    p = tmp_path / "papers.toml"
    with pytest.raises(PapersError, match="not an ISO date"):
        save_overrides(p, {DocKind.PASSPORT: {"expires": "not a date"}})


def test_save_overrides_escapes_quotes_and_backslashes(tmp_path: Path) -> None:
    p = tmp_path / "papers.toml"
    save_overrides(p, {DocKind.SURVEY: {"note": 'say "hi" and a \\backslash'}})
    assert load_overrides(p)[DocKind.SURVEY].note == 'say "hi" and a \\backslash'


def test_save_overrides_keeps_paragraph_breaks_in_a_multiline_note(tmp_path: Path) -> None:
    p = tmp_path / "papers.toml"
    note = "Step 1 done.\n\nStep 2: still pending, call FCC to confirm."
    save_overrides(p, {DocKind.RADIO_LICENCE: {"note": note}})
    assert load_overrides(p)[DocKind.RADIO_LICENCE].note == note
    assert '"""' in p.read_text(encoding="utf-8")


def test_save_overrides_falls_back_to_single_line_for_embedded_triple_quotes(
    tmp_path: Path,
) -> None:
    # A multi-line string can't safely embed a literal '"""', so this one rare
    # case falls back to a single-line string - any newline in it collapses to
    # a space, same as before this file gained multi-line support.
    p = tmp_path / "papers.toml"
    note = 'has a literal """ inside\nand a newline'
    save_overrides(p, {DocKind.SURVEY: {"note": note}})
    assert load_overrides(p)[DocKind.SURVEY].note == 'has a literal """ inside and a newline'
