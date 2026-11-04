"""Tests for the comms module."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from passage_planner.comms import (
    CommsConfig,
    _cycle_status,
    load_comms_config,
    render_comms,
    resolve,
    save_comms_config,
)
from passage_planner.errors import CommsError


def test_resolve_and_aliases() -> None:
    assert resolve("Bahamas").key == "bahamas"
    assert resolve("TCI").key == "turks and caicos"
    assert resolve("bvi").name == "British Virgin Islands"
    assert resolve("US").key == "usa"
    assert resolve("Freedonia") is None


def test_resolve_eastern_caribbean_extension() -> None:
    assert resolve("Grenada").key == "grenada"
    assert resolve("Bonaire").key == "bonaire"
    assert resolve("Curaçao").key == "curacao"
    assert resolve("St Martin").key == "saint martin"
    assert resolve("St Maarten").key == "sint maarten"
    # Dutch and French sides of the same island are distinct jurisdictions.
    assert resolve("st martin").key != resolve("st maarten").key


def test_cycle_status_before_and_after_reset_day() -> None:
    # resets on the 14th; 20th is 6 days after, 24 days before the next reset
    assert _cycle_status(14, date(2026, 9, 20)) == (6, 24)
    # 5th is before this month's reset -> counts from last month's reset
    since, until = _cycle_status(14, date(2026, 9, 5))
    assert since == 22  # days since Aug 14
    assert until == 9   # days until Sep 14


def test_cycle_status_on_reset_day_is_day_zero() -> None:
    assert _cycle_status(14, date(2026, 9, 14)) == (0, 30)


def test_cycle_status_handles_year_wrap() -> None:
    since, until = _cycle_status(14, date(2026, 12, 20))
    assert since == 6
    assert until == 25  # to Jan 14, 2027


def test_load_shipped_comms_config() -> None:
    cfg = load_comms_config(Path("config/comms.toml"))
    assert cfg is not None
    assert "Personal Maritime" in cfg.starlink_plan
    assert cfg.tmobile_roaming_included is True
    assert "Caribbean" in cfg.maya_esim_scope
    assert cfg.starlink_billing_cycle_day == 24
    assert cfg.starlink_ocean_mode_usd_per_gb == 6.0
    # Personal Maritime has no monthly high-speed GB cap -- unlimited within 12 NM
    assert cfg.starlink_priority_gb is None
    assert "Spitz AX" in cfg.router_model


def test_load_comms_config_missing_file_returns_none(tmp_path: Path) -> None:
    assert load_comms_config(tmp_path / "nope.toml") is None


def test_load_comms_config_bad_cycle_day_rejected(tmp_path: Path) -> None:
    p = tmp_path / "comms.toml"
    p.write_text("[starlink]\nbilling_cycle_day = 40\n", encoding="utf-8")
    with pytest.raises(CommsError, match="1-28"):
        load_comms_config(p)


def test_load_comms_config_bad_toml_rejected(tmp_path: Path) -> None:
    p = tmp_path / "comms.toml"
    p.write_text("not [valid toml", encoding="utf-8")
    with pytest.raises(CommsError):
        load_comms_config(p)


def test_load_comms_config_full_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "comms.toml"
    p.write_text(
        """
[starlink]
plan = "Roam Unlimited"
billing_cycle_day = 1
priority_gb = 300
notes = "test note"

[t_mobile]
plan = "Go5G Plus"
roaming_included = true
notes = "roams fine"

[maya_esim]
scope = "regional"
notes = "works everywhere"

[router]
model = "Test Router 9000"
notes = "prioritize cellular over satellite"
""",
        encoding="utf-8",
    )
    cfg = load_comms_config(p)
    assert cfg == CommsConfig(
        starlink_plan="Roam Unlimited",
        starlink_billing_cycle_day=1,
        starlink_priority_gb=300.0,
        starlink_notes="test note",
        tmobile_plan="Go5G Plus",
        tmobile_roaming_included=True,
        tmobile_notes="roams fine",
        maya_esim_scope="regional",
        maya_esim_notes="works everywhere",
        router_model="Test Router 9000",
        router_notes="prioritize cellular over satellite",
    )


# -- save_comms_config (the Comms form's write side) -------------------------


def test_save_comms_config_round_trips_through_load(tmp_path: Path) -> None:
    p = tmp_path / "comms.toml"
    cfg = CommsConfig(
        starlink_plan="Personal Maritime",
        starlink_billing_cycle_day=24,
        starlink_priority_gb=None,
        starlink_ocean_mode_usd_per_gb=6.0,
        starlink_notes="test note",
        tmobile_plan="Go5G Plus",
        tmobile_roaming_included=True,
        tmobile_notes="roams fine",
        maya_esim_scope="regional",
        maya_esim_notes="works everywhere",
        router_model="Test Router 9000",
        router_notes="prioritize cellular over satellite",
    )
    save_comms_config(p, cfg)
    assert load_comms_config(p) == cfg


def test_save_comms_config_omits_an_empty_table(tmp_path: Path) -> None:
    p = tmp_path / "comms.toml"
    save_comms_config(p, CommsConfig(starlink_plan="Roam"))
    assert "[t_mobile]" not in p.read_text(encoding="utf-8")
    cfg = load_comms_config(p)
    assert cfg is not None
    assert cfg.starlink_plan == "Roam" and cfg.tmobile_plan == ""


def test_save_comms_config_rejects_bad_cycle_day(tmp_path: Path) -> None:
    p = tmp_path / "comms.toml"
    with pytest.raises(CommsError, match="1-28"):
        save_comms_config(p, CommsConfig(starlink_billing_cycle_day=40))


def test_save_comms_config_escapes_quotes(tmp_path: Path) -> None:
    p = tmp_path / "comms.toml"
    save_comms_config(p, CommsConfig(starlink_notes='say "hi"'))
    cfg = load_comms_config(p)
    assert cfg is not None and cfg.starlink_notes == 'say "hi"'


def test_save_comms_config_keeps_paragraph_breaks_in_a_multiline_note(tmp_path: Path) -> None:
    p = tmp_path / "comms.toml"
    note = "First point.\n\nSecond point, on its own paragraph."
    save_comms_config(p, CommsConfig(starlink_notes=note))
    cfg = load_comms_config(p)
    assert cfg is not None and cfg.starlink_notes == note
    assert '"""' in p.read_text(encoding="utf-8")


def test_render_comms_no_config_is_pending() -> None:
    md = render_comms(["bahamas"], None, date(2026, 9, 20))
    assert "PENDING" in md
    assert "### The Bahamas" in md


def test_render_comms_no_countries() -> None:
    md = render_comms([], None, date(2026, 9, 20))
    assert "No destination given" in md


def test_render_comms_unknown_country_skeleton() -> None:
    md = render_comms(["Freedonia"], None, date(2026, 9, 20))
    assert "### Freedonia" in md
    assert "Not in the built-in comms registry" in md


def test_render_comms_early_cycle_says_lean_in() -> None:
    # a capped monthly plan (e.g. Roam 300GB) -- pacing against the cap applies
    cfg = CommsConfig(
        starlink_plan="Roam 300GB", starlink_billing_cycle_day=1, starlink_priority_gb=300
    )
    md = render_comms(["usa"], cfg, date(2026, 9, 3))
    assert "plenty of headroom" in md


def test_render_comms_late_cycle_says_favor_alternatives() -> None:
    cfg = CommsConfig(
        starlink_plan="Roam 300GB", starlink_billing_cycle_day=1, starlink_priority_gb=300
    )
    md = render_comms(["usa"], cfg, date(2026, 9, 29))
    assert "late in the cycle" in md


def test_render_comms_uncapped_plan_tracks_ocean_mode_not_cycle_position() -> None:
    # Personal Maritime / Roam Unlimited: no monthly GB cap -- no headroom/lateness framing
    cfg = CommsConfig(starlink_plan="Personal Maritime", starlink_billing_cycle_day=24)
    md = render_comms(["usa"], cfg, date(2026, 9, 20))
    assert "plenty of headroom" not in md
    assert "late in the cycle" not in md
    assert "track Ocean-Mode" in md


def test_render_comms_shows_ocean_mode_rate() -> None:
    cfg = CommsConfig(starlink_plan="Personal Maritime", starlink_ocean_mode_usd_per_gb=6.0)
    md = render_comms(["usa"], cfg, date(2026, 9, 20))
    assert "$6/GB" in md


def test_render_comms_carries_a_short_offshore_reminder_not_the_long_terms() -> None:
    md = render_comms(["bahamas"], None, date(2026, 9, 20))
    assert "Offshore reminder" in md and "$6/GB" in md and "12 NM" in md
    assert "Ocean Mode" in md
    # the long plan-terms paragraph is gone
    assert "only suspended" not in md and "land use is" not in md
    assert len(md.split("Offshore reminder")[1].split("\n")[0]) < 200


def test_render_comms_shows_suspend_deadline_at_next_reset() -> None:
    cfg = CommsConfig(starlink_plan="Personal Maritime", starlink_billing_cycle_day=24)
    md = render_comms(["usa"], cfg, date(2026, 9, 20))
    assert "request it before **2026-09-24**" in md
    assert "no mid-cycle proration" in md


def test_render_comms_shows_router_line_when_configured() -> None:
    cfg = CommsConfig(
        starlink_plan="Personal Maritime",
        router_model="GL.iNet Spitz AX (GL-X3000)",
        router_notes="Failover mode, cheapest first",
    )
    md = render_comms(["usa"], cfg, date(2026, 9, 20))
    assert "GL.iNet Spitz AX (GL-X3000)" in md
    assert "Failover mode, cheapest first" in md


def test_render_comms_no_router_note_when_no_router_configured() -> None:
    cfg = CommsConfig(starlink_plan="Personal Maritime")
    md = render_comms(["usa"], cfg, date(2026, 9, 20))
    assert "priority-ordered failover" not in md
