"""Tests for the ciq subpackage."""

from __future__ import annotations

from passage_planner.ciq import ciq_template, render_dossier, resolve


def test_resolve_and_aliases() -> None:
    assert resolve("Bahamas").key == "bahamas"
    assert resolve("TCI").key == "turks and caicos"
    assert resolve("bvi").name == "British Virgin Islands"
    assert resolve("US").key == "usa"
    assert resolve("Freedonia") is None


def test_render_dossier_multi_country_and_qflag() -> None:
    md = render_dossier(["USA", "Bahamas", "Turks and Caicos"])
    assert "### United States" in md
    assert "### The Bahamas" in md
    assert "### Turks & Caicos Islands" in md
    assert "click2clear.bahamas.gov.bs" in md
    assert "yellow Q flag" in md
    assert "SECONDARY" in md  # noonsite cross-check line


def test_dossier_carries_verified_fees_and_offices() -> None:
    md = render_dossier(["USA", "Bahamas", "Turks and Caicos"])
    assert "- **Fees:**" in md
    assert "- **Where / who:**" in md
    assert "$34.83" in md                       # CBP decal
    assert "$750" in md and "50-100 ft" in md   # Bahamas 51 ft cruising permit
    assert "$300, valid 90 days" in md          # TCI cruising permit
    assert "Checked against the primary sources on 2026-09-09" in md


def test_unverified_country_says_so() -> None:
    from dataclasses import replace

    from passage_planner.ciq.data import REGISTRY
    from passage_planner.ciq.render import _known

    stale = replace(REGISTRY["usa"], verified="")
    assert "Not yet verified" in _known(stale)


def test_render_dossier_unknown_country_named_skeleton() -> None:
    md = render_dossier(["Atlantis"])
    assert "### Atlantis" in md
    assert "Not in the built-in registry" in md
    assert "ports of entry" in md.lower()


def test_ciq_template_has_verify_urls_and_instructions() -> None:
    t = ciq_template(["Dominican Republic", "Puerto Rico"])
    assert "aduanas.gob.do" in t
    assert "cbp.gov" in t
    assert "brief --ciq-file" in t
    assert "Claude conversation" in t
