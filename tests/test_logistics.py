"""Tests for passage_planner.logistics."""

from __future__ import annotations

from passage_planner.logistics import (
    CHECKED_FIELD_COUNT,
    LOGISTICS_FIELDS,
    coverage_gaps,
    logistics_template,
    strip_template_preamble,
    unresearched_ports,
)


def test_template_has_a_section_and_all_fields_per_port() -> None:
    md = logistics_template(["West End", "George Town"], route_name="FLD to T&C")
    assert "# Shore logistics for FLD to T&C — draft" in md
    assert "### West End" in md and "### George Town" in md
    for label, _ in LOGISTICS_FIELDS:
        assert md.count(f"- **{label}:**") == 2  # once per port


def test_template_without_route_name() -> None:
    md = logistics_template(["Bimini"])
    assert md.startswith("# Shore logistics — draft")


def test_coverage_gaps_flags_unfilled_fields() -> None:
    # West End fully skeletal; Bimini has two real fields filled.
    md = "\n".join([
        "### West End",
        "- **Groceries:**  *(research: shop)*",
        "- **Fuel dock:**  *(research: diesel)*",
        "",
        "### Bimini",
        "- **Groceries:** Brown's, 300 m walk, thin — supply boat Thursdays",
        "- **Fuel dock:** Blue Water Marina, 0700-1700, 7 ft alongside",
        "- **Money:**  *(research: currency)*",
    ])
    gaps = coverage_gaps(md)
    assert set(gaps) == {"West End", "Bimini"}
    assert "Groceries" in gaps["West End"]        # only a hint -> counts as missing
    assert "Groceries" not in gaps["Bimini"]      # filled
    assert "Fuel dock" not in gaps["Bimini"]
    assert "Money" in gaps["Bimini"]              # still a hint
    assert "Notes" not in gaps["West End"]        # Notes is optional


def test_coverage_gaps_empty_when_all_filled() -> None:
    body = ["### Nassau"]
    for label, _ in LOGISTICS_FIELDS:
        body.append(f"- **{label}:** something concrete here")
    assert coverage_gaps("\n".join(body)) == {}


def _filled(port: str, n_filled: int) -> str:
    """A port section with the first ``n_filled`` checked fields filled in and
    the rest left as untouched research hints."""
    lines = [f"### {port}"]
    checked = [(lbl, hint) for lbl, hint in LOGISTICS_FIELDS if lbl != "Notes"]
    for i, (label, hint) in enumerate(checked):
        if i < n_filled:
            lines.append(f"- **{label}:** something concrete here")
        else:
            lines.append(f"- **{label}:**  *(research: {hint})*")
    return "\n".join(lines)


def test_checked_field_count_excludes_optional_notes() -> None:
    checked = [label for label, _ in LOGISTICS_FIELDS if label != "Notes"]
    assert len(checked) == CHECKED_FIELD_COUNT


def test_unresearched_ports_flags_an_untouched_template() -> None:
    # The 2026-09-18 incident: template generated, submitted without research.
    md = logistics_template(["Eleuthera", "Nassau"])
    flagged = unresearched_ports(md)
    assert set(flagged) == {"Eleuthera", "Nassau"}
    assert len(flagged["Eleuthera"]) == CHECKED_FIELD_COUNT


def test_unresearched_ports_flags_only_the_blank_port() -> None:
    md = _filled("Nassau", CHECKED_FIELD_COUNT) + "\n\n" + _filled("Bimini", 0)
    assert set(unresearched_ports(md)) == {"Bimini"}


def test_unresearched_ports_ignores_small_gaps() -> None:
    # A researched port with a few honest unknowns is the briefing's own
    # §7b coverage note's job, not the submit guard's.
    md = _filled("Nassau", CHECKED_FIELD_COUNT - 3)
    assert coverage_gaps(md)  # there are gaps ...
    assert unresearched_ports(md) == {}  # ... but not enough to block a submit


def test_unresearched_ports_threshold_is_more_than_half_empty() -> None:
    half_empty = CHECKED_FIELD_COUNT // 2 + 1  # strictly more than half missing
    md_over = _filled("Over", CHECKED_FIELD_COUNT - half_empty)
    md_at = _filled("At", CHECKED_FIELD_COUNT - half_empty + 1)
    assert "Over" in unresearched_ports(md_over)
    assert "At" not in unresearched_ports(md_at)


def test_unresearched_ports_empty_for_blank_or_headingless_text() -> None:
    assert unresearched_ports("") == {}
    assert unresearched_ports("   \n") == {}
    assert unresearched_ports("Just some free text with no port headings.") == {}


def test_strip_template_preamble_keeps_only_the_port_sections() -> None:
    md = logistics_template(["Nassau"], route_name="FLD to T&C")
    out = strip_template_preamble(md)
    assert out.startswith("### Nassau")
    assert "Shore logistics" not in out
    assert "Fill each port section" not in out


def test_strip_template_preamble_keeps_a_hand_written_preamble() -> None:
    md = (
        "# Shore logistics for FLD to T&C — filled 2026-09-18\n\n"
        "Researched from X.\n\n"
        "### Nassau\n\n"
        "- **Groceries:** ok\n"
    )
    assert strip_template_preamble(md) == md.strip()


def test_strip_template_preamble_is_a_noop_on_a_headingless_text() -> None:
    assert strip_template_preamble("  just notes  ") == "just notes"


def test_split_ports_keeps_a_single_comma_name_whole_when_newline_terminated() -> None:
    from passage_planner.logistics import split_ports

    name = "Salt Pond (Thompson Bay), Long Island (Bahamas)"
    assert split_ports(name + "\n") == [name]
    assert split_ports("West End, George Town") == ["West End", "George Town"]  # CLI form
