"""Tests for passage_planner.research (no Claude CLI is ever launched here)."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from passage_planner import config
from passage_planner.errors import ResearchOutputError, ResearchUnavailableError
from passage_planner.logistics import (
    CHECKED_FIELD_COUNT,
    LOGISTICS_FIELDS,
    coverage_gaps,
    unresearched_ports,
)
from passage_planner.research import (
    ENV_EXE,
    ENV_TOKEN,
    STOP_ROLES,
    RunOutput,
    RunRequest,
    build_args,
    build_prompt,
    child_env,
    find_claude_exe,
    incomplete_section,
    research_port,
    research_status,
    route_context,
    validate_section,
    vessel_facts,
)
from passage_planner.route import Route, Waypoint

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
NOW = datetime(2026, 9, 21, 15, 30, tzinfo=UTC)


def _vessel() -> config.VesselConfig:
    return config.load(CONFIG_DIR).vessel


def _route(n: int = 3) -> Route:
    wps = tuple(
        Waypoint(name=f"WP{i + 1}", lat=25.0 + i * 0.1, lon=-77.0 - i * 0.1) for i in range(n)
    )
    # route_context() only reads waypoints, so legs are left empty.
    return Route(name="Test route", waypoints=wps, legs=())


def _section(port: str, filled: int = CHECKED_FIELD_COUNT, resolved: bool = True) -> str:
    lines = [f"### {port}", ""]
    if resolved:
        lines += [f"> Resolved as: {port} (unambiguous).", ""]
    checked = [lbl for lbl, _ in LOGISTICS_FIELDS if lbl != "Notes"]
    for i, label in enumerate(checked):
        if i < filled:
            lines.append(f"- **{label}:** found detail (source, 2026-09).")
        else:
            lines.append(f"- **{label}:**  *(research: still a hint)*")
    return "\n".join(lines)


def _cli_json(result: str, **extra: object) -> str:
    payload: dict[str, object] = {
        "is_error": False, "result": result, "duration_ms": 61000,
        "total_cost_usd": 0.42, "usage": {"output_tokens": 1234}, **extra,
    }
    return json.dumps(payload)


class FakeRunner:
    def __init__(self, out: RunOutput) -> None:
        self.out = out
        self.requests: list[RunRequest] = []

    def __call__(self, req: RunRequest) -> RunOutput:
        self.requests.append(req)
        return self.out


ENV_OK = {ENV_TOKEN: "tok", "ANTHROPIC_API_KEY": "sk-should-be-stripped", "PATH": "x"}


def _research(
    out: RunOutput, port: str = "Spanish Wells", **kw: object
) -> tuple[object, FakeRunner]:
    runner = FakeRunner(out)
    result = research_port(
        port, vessel=_vessel(), exe=Path("claude.exe"), env=ENV_OK, run=runner,
        now=lambda: NOW, **kw,  # type: ignore[arg-type]
    )
    return result, runner


# --- locating the CLI -------------------------------------------------------


def _bundle(root: Path, *versions: str) -> None:
    for v in versions:
        d = root / "Claude" / "claude-code" / v
        d.mkdir(parents=True)
        (d / "claude.exe").write_text("")


def test_find_exe_override_wins(tmp_path: Path) -> None:
    exe = tmp_path / "my-claude.exe"
    exe.write_text("")
    assert find_claude_exe({ENV_EXE: str(exe)}, which=lambda _n: "/usr/bin/claude") == exe


def test_find_exe_bad_override_is_loud(tmp_path: Path) -> None:
    with pytest.raises(ResearchUnavailableError):
        find_claude_exe({ENV_EXE: str(tmp_path / "missing.exe")}, which=lambda _n: None)


def test_find_exe_prefers_path_over_bundled(tmp_path: Path) -> None:
    _bundle(tmp_path, "2.1.275")
    got = find_claude_exe({"APPDATA": str(tmp_path)}, which=lambda _n: "/usr/bin/claude")
    assert got == Path("/usr/bin/claude")


def test_find_exe_picks_highest_bundled_version_numerically(tmp_path: Path) -> None:
    _bundle(tmp_path, "2.1.9", "2.1.275", "2.10.0", "2.2.0")
    got = find_claude_exe({"APPDATA": str(tmp_path)}, which=lambda _n: None)
    assert got is not None and got.parent.name == "2.10.0"  # not a string sort


def _packaged(local: Path, *versions: str, family: str = "Claude_pzs8sxrjxfjjc") -> None:
    """The Store/MSIX layout: the app's %APPDATA% is really a private folder under
    %LOCALAPPDATA%\\Packages, invisible to processes outside the package."""
    for v in versions:
        d = local / "Packages" / family / "LocalCache" / "Roaming" / "Claude" / "claude-code" / v
        d.mkdir(parents=True)
        (d / "claude.exe").write_text("")


def test_find_exe_finds_the_packaged_app_copy_from_outside_the_package(tmp_path: Path) -> None:
    # The backend runs from a normal process: APPDATA has no Claude folder at all.
    real_appdata, local = tmp_path / "Roaming", tmp_path / "Local"
    real_appdata.mkdir()
    _packaged(local, "2.1.275")
    got = find_claude_exe(
        {"APPDATA": str(real_appdata), "LOCALAPPDATA": str(local)}, which=lambda _n: None
    )
    assert got is not None and got.parent.name == "2.1.275"
    assert "Packages" in got.parts


def test_find_exe_takes_the_highest_version_across_both_locations(tmp_path: Path) -> None:
    roaming, local = tmp_path / "Roaming", tmp_path / "Local"
    _bundle(roaming.parent, "2.1.9")  # <tmp>/Claude/claude-code/2.1.9
    _packaged(local, "2.1.275")
    env = {"APPDATA": str(roaming.parent), "LOCALAPPDATA": str(local)}
    got = find_claude_exe(env, which=lambda _n: None)
    assert got is not None and got.parent.name == "2.1.275"


def test_find_exe_ignores_unrelated_packages(tmp_path: Path) -> None:
    _packaged(tmp_path, "9.9.9", family="SomeOtherApp_abc123")
    assert find_claude_exe({"LOCALAPPDATA": str(tmp_path)}, which=lambda _n: None) is None


def test_find_exe_none_when_nothing_installed(tmp_path: Path) -> None:
    assert find_claude_exe({"APPDATA": str(tmp_path)}, which=lambda _n: None) is None
    assert find_claude_exe({}, which=lambda _n: None) is None


# --- status -----------------------------------------------------------------


def test_status_reasons_are_actionable(tmp_path: Path) -> None:
    none = research_status({}, which=lambda _n: None)
    assert not none.available and "not found" in none.reason
    no_tok = research_status({}, which=lambda _n: "/bin/claude")
    assert not no_tok.available and ENV_TOKEN in no_tok.reason and "setup-token" in no_tok.reason
    ok = research_status({ENV_TOKEN: "t"}, which=lambda _n: "/bin/claude")
    assert ok.available and ok.exe == Path("/bin/claude")


def test_status_blank_token_counts_as_missing() -> None:
    assert not research_status({ENV_TOKEN: "  "}, which=lambda _n: "/bin/claude").available


def test_status_bad_override_reported_not_raised(tmp_path: Path) -> None:
    st = research_status({ENV_EXE: str(tmp_path / "nope.exe")}, which=lambda _n: None)
    assert not st.available and "not a file" in st.reason


# --- environment / argv ------------------------------------------------------


def test_child_env_strips_metered_credentials_case_insensitively() -> None:
    env = child_env({"anthropic_api_key": "a", "ANTHROPIC_AUTH_TOKEN": "b",
                     ENV_TOKEN: "keep", "PATH": "p"})
    assert env == {ENV_TOKEN: "keep", "PATH": "p"}


def test_child_env_strips_inherited_claude_session_state_but_keeps_the_token() -> None:
    # A backend launched from inside a Claude session inherits that session's
    # routing/auth plumbing; the research child must not, or it could be sent to
    # the app's proxy instead of using the subscription token.
    inherited = {
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:1234",
        "CLAUDECODE": "1",
        "CLAUDE_PID": "42",
        "CLAUDE_AGENT_SDK_VERSION": "1.0",
        "CLAUDE_CODE_CHILD_SESSION": "1",
        "CLAUDE_CODE_MESSAGING_TOKEN": "secret",
        "CLAUDE_CODE_OAUTH_SCOPES": "user:inference",
        "USE_LOCAL_OAUTH": "1",
        "USE_STAGING_OAUTH": "1",
    }
    # unrelated secrets are not the child's business either
    unrelated = {"HYLAS_INFLUX_TOKEN": "i", "GH_TOKEN": "g", "SOME_PASSWORD": "p"}
    keep = {ENV_TOKEN: "sub-token", "PATH": "p", "SYSTEMROOT": "C:\\Windows"}
    assert child_env({**inherited, **unrelated, **keep}) == keep


def test_args_never_bare_and_restrict_tools() -> None:
    args = build_args(Path("claude.exe"), "SYS", model="haiku", effort="high")
    assert "--bare" not in args
    assert args[args.index("--tools") + 1] == "WebSearch,WebFetch"
    assert args[args.index("--allowedTools") + 1 : args.index("--allowedTools") + 3] == (
        "WebSearch", "WebFetch")
    for flag in ("-p", "--no-session-persistence", "--strict-mcp-config", "--max-budget-usd"):
        assert flag in args
    assert args[args.index("--output-format") + 1] == "json"
    assert args[args.index("--append-system-prompt") + 1] == "SYS"
    assert args[args.index("--model") + 1] == "haiku"
    # the prompt goes on stdin, never on the command line
    assert not any("Research shore logistics" in a for a in args)


# --- prompt ------------------------------------------------------------------


def test_prompt_carries_boat_facts_route_and_skeleton() -> None:
    p = build_prompt("Spanish Wells", vessel=_vessel(), route=_route(), crew_count=2,
                     today=date(2026, 9, 21))
    for needle in ("2026-09-21", "Spanish Wells", "51 ft LOA", "14 ft 9 in", "8 ft",
                   "70 ft", "all-electric", "Example Buddy", "Crew aboard: 2",
                   "Resolved as:", "### Spanish Wells"):
        assert needle in p.user, needle
    assert "WP1" in p.user and "WP3" in p.user
    for label, _ in LOGISTICS_FIELDS:
        assert f"- **{label}:**" in p.user
    assert "untrusted" in p.system and "not confirmed from published sources" in p.system


def test_system_prompt_carries_the_rules_the_live_runs_showed_were_needed() -> None:
    # Live run 2 (2026-09-21) called marina water "fine to drink" and gave no
    # full source URLs; run-to-run contact numbers differed.
    system = build_prompt("Nassau", vessel=_vessel()).system
    assert "never advise that it is safe to drink" in system
    assert "Sources: [name](url)" in system and "full URLs" in system
    assert "Phone numbers" in system and "operator's own site" in system


def test_security_field_is_checked_and_the_prompt_frames_it_as_awareness_only() -> None:
    # 2026-09-21: the owner asked for recent theft/incident reports (the Caribbean Safety &
    # Security Net) as situational awareness. It is a real checked field, and the
    # prompt forbids calling a place safe on the strength of "no reports".
    assert "Security / recent incidents" in [label for label, _ in LOGISTICS_FIELDS]
    system = build_prompt("Nassau", vessel=_vessel()).system
    assert "thecssn.org" in system and "Noonsite" in system
    assert "situational awareness" in system
    assert "never write that a place is safe" in system
    assert "no reports found in" in system


def test_prompt_states_ambiguity_policy_and_has_no_question_channel() -> None:
    p = build_prompt("Eleuthera", vessel=_vessel())
    assert "cannot ask questions" in p.user
    assert "nearest the route" in p.user
    assert "(no route supplied)" in p.user


def test_vessel_facts_buddy_boat_keel_up_and_down() -> None:
    facts = vessel_facts(_vessel())
    # Ports, marinas and anchorages are judged against the keel-UP draft; the keel-down
    # draft is open-water only and must be labelled as irrelevant at a port.
    assert "draws 8 ft for arrival, departure and anchoring" in facts
    assert "14 ft keel-down draft is for open-water sailing only" in facts
    assert "90 ft air draft" in facts


def test_route_context_is_thinned_but_keeps_ends() -> None:
    text = route_context(_route(100))
    points = [ln for ln in text.splitlines() if ln.startswith("- ")]
    assert len(points) <= 20
    assert points[0].startswith("- 1. WP1") and points[-1].startswith("- 100. WP100")
    assert route_context(_route(5)).count("\n- ") == 5


# --- validating the reply ----------------------------------------------------


def test_validate_strips_preamble_fence_and_extra_sections() -> None:
    extra = "\n### Other port\n- **Groceries:** x\n```\n"
    reply = "Here you go!\n```markdown\n" + _section("Nassau") + extra
    v = validate_section(reply, "Nassau")
    assert v.markdown.startswith("### Nassau\n") and "Here you go" not in v.markdown
    assert "Other port" not in v.markdown and "```" not in v.markdown
    assert v.gaps == () and v.resolved_as == "Resolved as: Nassau (unambiguous)."


def test_validate_rewrites_heading_to_port_as_typed() -> None:
    v = validate_section(_section("Spanish Wells, Eleuthera"), "Eleuthera")
    assert v.markdown.splitlines()[0] == "### Eleuthera"
    assert v.gaps == ()  # every field was filled, and the rewritten heading matches


def test_validate_reports_gaps_without_failing() -> None:
    v = validate_section(_section("Nassau", filled=CHECKED_FIELD_COUNT - 2), "Nassau")
    assert len(v.gaps) == 2


def test_validate_rejects_replies_that_are_not_sections() -> None:
    with pytest.raises(ResearchOutputError) as no_heading:
        validate_section("I could not find anything, sorry.", "Nassau")
    assert "sorry" in no_heading.value.raw
    with pytest.raises(ResearchOutputError):
        validate_section("### Nassau\nJust prose, no field bullets.", "Nassau")


def test_incomplete_section_cannot_pass_for_data() -> None:
    md = incomplete_section("Nassau", "usage cap reached", NOW)
    assert md.startswith("### Nassau\n")
    assert "> RESEARCH INCOMPLETE: usage cap reached at 2026-09-21 15:30 UTC" in md
    assert "Nassau" in coverage_gaps(md)
    assert "Nassau" in unresearched_ports(md)  # the submit guard will catch it


# --- research_port -----------------------------------------------------------


def test_research_ok_path_and_subprocess_contract() -> None:
    result, runner = _research(RunOutput(0, _cli_json(_section("Spanish Wells")), ""))
    assert result.status == "ok" and result.error is None  # type: ignore[attr-defined]
    assert result.gaps == () and result.cost_usd == 0.42  # type: ignore[attr-defined]
    assert result.duration_s == 61.0  # type: ignore[attr-defined]
    req = runner.requests[0]
    assert "Research shore logistics" in req.stdin and "Spanish Wells" in req.stdin
    assert "ANTHROPIC_API_KEY" not in req.env and req.env[ENV_TOKEN] == "tok"
    assert "--bare" not in req.args and req.timeout_s == 900.0
    assert req.cwd.name.startswith("hylas-research-")  # a throwaway dir, not the repo


def test_research_runs_from_a_fresh_dir_outside_the_project(tmp_path: Path) -> None:
    _, runner = _research(RunOutput(0, _cli_json(_section("Nassau")), ""), "Nassau",
                          workdir=tmp_path)
    assert runner.requests[0].cwd == tmp_path


def test_research_cli_error_becomes_incomplete_placeholder() -> None:
    err = json.dumps({"is_error": True, "result": "Not logged in · Please run /login"})
    result, _ = _research(RunOutput(1, err, ""))
    assert result.status == "failed" and "Not logged in" in result.error  # type: ignore[attr-defined]
    assert "> RESEARCH INCOMPLETE: Not logged in" in result.markdown  # type: ignore[attr-defined]


def test_research_timeout_becomes_incomplete_placeholder() -> None:
    result, _ = _research(RunOutput(-1, "", "", timed_out=True), timeout_s=120.0)
    assert result.status == "failed" and "timed out after 2 min" in result.error  # type: ignore[attr-defined]


def test_research_unparseable_output_is_a_failure_not_a_crash() -> None:
    result, _ = _research(RunOutput(2, "Traceback: boom", "stderr text"))
    assert result.status == "failed" and "exit 2" in result.error  # type: ignore[attr-defined]
    assert "> RESEARCH INCOMPLETE" in result.markdown  # type: ignore[attr-defined]


def test_research_unusable_reply_keeps_raw_text() -> None:
    result, _ = _research(RunOutput(0, _cli_json("No idea, sorry."), ""))
    assert result.status == "failed" and "No idea" in result.raw  # type: ignore[attr-defined]


def test_research_requires_the_token_before_starting() -> None:
    runner = FakeRunner(RunOutput(0, "{}", ""))
    with pytest.raises(ResearchUnavailableError):
        research_port("Nassau", vessel=_vessel(), exe=Path("c.exe"), env={"PATH": "x"}, run=runner)
    assert runner.requests == []  # nothing was launched


# --- reply normalisation (found in the first live run, 2026-09-21) -----------


def test_validate_canonicalises_field_labels_the_model_reformatted() -> None:
    # The live run wrote "Trash/recycling" (no spaces); the skeleton label is
    # "Trash / recycling". A mismatch counted the field as an empty gap.
    reply = _section("Nassau").replace("**Trash / recycling:**", "**Trash/recycling:**")
    reply = reply.replace("**Groceries:**", "**groceries:**")
    v = validate_section(reply, "Nassau")
    assert "- **Trash / recycling:**" in v.markdown and "- **Groceries:**" in v.markdown
    assert "Trash/recycling" not in v.markdown
    assert v.gaps == ()


def test_validate_leaves_unknown_labels_alone() -> None:
    reply = _section("Nassau") + "\n- **Anchorage tips:** hold in sand\n"
    assert "- **Anchorage tips:**" in validate_section(reply, "Nassau").markdown


def test_validate_tightens_blank_lines_between_field_bullets_only() -> None:
    loose = "### Nassau\n\n> Resolved as: Nassau (unambiguous).\n\n" + "\n\n".join(
        f"- **{lbl}:** detail" for lbl, _ in LOGISTICS_FIELDS
    ) + "\n\nSources: [a](https://a.example)\n"
    md = validate_section(loose, "Nassau").markdown
    assert not re.search(r"^- \*\*.*\n\n- \*\*", md, re.MULTILINE)  # none between two bullets
    assert md.count("\n\n") == 3  # after heading, after the quote, before Sources
    assert md.rstrip().endswith("Sources: [a](https://a.example)")


def test_research_records_turns_and_flags_an_answer_with_no_tool_use() -> None:
    good, _ = _research(RunOutput(0, _cli_json(_section("Nassau"), num_turns=9), ""), "Nassau")
    assert good.turns == 9 and good.warnings == ()  # type: ignore[attr-defined]
    silent, _ = _research(RunOutput(0, _cli_json(_section("Nassau"), num_turns=1), ""), "Nassau")
    assert silent.status == "ok"  # type: ignore[attr-defined]
    assert any("no web research" in w for w in silent.warnings)  # type: ignore[attr-defined]


def test_research_flags_denied_tool_calls_and_tolerates_missing_turns() -> None:
    denied = _cli_json(_section("Nassau"), num_turns=6, permission_denials=[{"tool": "WebFetch"}])
    r, _ = _research(RunOutput(0, denied, ""), "Nassau")
    assert any("denied" in w for w in r.warnings)  # type: ignore[attr-defined]
    unknown, _ = _research(RunOutput(0, _cli_json(_section("Nassau")), ""), "Nassau")
    assert unknown.turns is None and unknown.warnings == ()  # type: ignore[attr-defined]


def test_security_rule_knows_cssn_does_not_cover_the_bahamas() -> None:
    # The Salt Pond run found, and the CSSN FAQ confirms, that CSSN excludes the
    # Bahamas (it refers those reports to Noonsite). The prompt must not tell the
    # researcher to search it there, and must ask for an honest "incomplete".
    system = build_prompt("Nassau", vessel=_vessel()).system
    assert "NOT The Bahamas" in system and "Tribune242" in system
    # ... but it does cover the Turks and Caicos (a run wrongly excluded them, 2026-09-21)
    assert "including the Turks and Caicos" in system
    assert "do not claim a CSSN search" in system
    assert "the check is incomplete" in system


def test_fee_rule_defers_to_the_official_schedule() -> None:
    system = build_prompt("Nassau", vessel=_vessel()).system
    assert "may be stale" in system and "official schedule" in system


def test_stop_role_adds_a_focus_section_and_no_role_adds_nothing() -> None:
    plain = build_prompt("Salt Pond", vessel=_vessel(), today=date(2026, 9, 21)).user
    assert "Why the boat stops here" not in plain
    out = build_prompt("Salt Pond", vessel=_vessel(), today=date(2026, 9, 21),
                       role="check-out").user
    assert "## Why the boat stops here" in out and "clears OUT" in out
    assert "zarpe" in out and "Key contacts and Notes" in out
    assert "clears INTO" in build_prompt("West End", vessel=_vessel(), role="check-in").user
    assert "fuel and water" in build_prompt("Nassau", vessel=_vessel(), role="fuel").user
    # the output format is unchanged by a role
    for label, _ in LOGISTICS_FIELDS:
        assert f"- **{label}:**" in out
    assert set(STOP_ROLES) == {"check-in", "check-out", "fuel"}


def test_unknown_stop_role_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown stop role"):
        build_prompt("Nassau", vessel=_vessel(), role="party")


def test_buddy_line_carries_make_model_hull_and_the_multihull_beam_warning() -> None:
    from passage_planner.fleet import boat_from_dict
    from passage_planner.research import _buddy_line

    cat = boat_from_dict({
        "name": "SV Salt Cat", "hull": "catamaran", "make": "Lagoon", "model": "45",
        "draft_ft": 4, "air_draft_ft": 62, "loa_ft": 45, "beam_ft": 24,
    })
    line = _buddy_line(cat)
    assert "SV Salt Cat (Lagoon 45, catamaran)" in line
    assert "45 ft LOA, 24 ft beam" in line
    assert "beam, not her draft" in line and "4 ft draft" in line
    mono = boat_from_dict({"name": "SV Blue Mono", "draft_ft": 6, "air_draft_ft": 60})
    assert _buddy_line(mono) == "- Buddy boat SV Blue Mono: 6 ft draft, 60 ft air draft."
