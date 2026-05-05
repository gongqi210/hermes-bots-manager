"""Tests for parsers.py — pure parser functions over Hermes v0.8.0 CLI output.

Every parser test loads a golden fixture committed in 02-01 from
``backend/tests/fixtures/hermes-cli/``. Fixtures are byte-exact captures from
real ``hermes`` runs — they are the frozen contract.

Covers (per Plan 02-02 Task 1 behavior spec):
  1. parse_profile_list — two profiles, default marked active
  2. parse_profile_list — header + separator skipped
  3. parse_profile_list — single-profile fixture
  4. parse_profile_list — em-dash placeholder maps to None
  5. parse_profile_show — fully-populated default fixture
  6. parse_profile_show — env_configured True when no "not configured" marker
  7-10. classify_create_error — duplicate / invalid_name / reserved_name / unknown
  11. parse_gateway_pid_file — well-formed JSON
  12. parse_gateway_pid_file — invalid JSON returns None (no raise)
"""

from __future__ import annotations

from pathlib import Path

from app.adapters.parsers import (
    GatewayPidFile,
    ProfileShow,
    ProfileSummary,
    classify_create_error,
    parse_gateway_pid_file,
    parse_profile_list,
    parse_profile_show,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "hermes-cli"


# ---------------------------------------------------------------------------
# parse_profile_list
# ---------------------------------------------------------------------------


def test_parse_profile_list_two_profiles_marks_default_active() -> None:
    text = (FIXTURES / "profile_list_2_profiles.txt").read_text(encoding="utf-8")
    rows = parse_profile_list(text)

    names = [r.name for r in rows]
    assert "default" in names
    assert "test-research-probe" in names

    default = next(r for r in rows if r.name == "default")
    assert isinstance(default, ProfileSummary)
    assert default.is_active is True
    assert default.gateway == "running"
    assert default.model == "MiniMax-M2.7"
    assert default.alias is None

    probe = next(r for r in rows if r.name == "test-research-probe")
    assert probe.is_active is False
    assert probe.model is None
    assert probe.gateway == "stopped"
    assert probe.alias == "test-research-probe"


def test_parse_profile_list_skips_header_and_separator() -> None:
    text = (FIXTURES / "profile_list_2_profiles.txt").read_text(encoding="utf-8")
    rows = parse_profile_list(text)
    for r in rows:
        assert r.name != "Profile"
        assert not r.name.startswith("─")


def test_parse_profile_list_only_default() -> None:
    text = (FIXTURES / "profile_list_only_default.txt").read_text(encoding="utf-8")
    rows = parse_profile_list(text)
    assert len(rows) == 1
    assert rows[0].name == "default"
    assert rows[0].is_active is True


def test_parse_profile_list_handles_em_dash_placeholder() -> None:
    text = (FIXTURES / "profile_list_2_profiles.txt").read_text(encoding="utf-8")
    rows = parse_profile_list(text)
    probe = next(r for r in rows if r.name == "test-research-probe")
    # Fixture column for probe.model is U+2014 → must map to None.
    assert probe.model is None


# ---------------------------------------------------------------------------
# parse_profile_show
# ---------------------------------------------------------------------------


def test_parse_profile_show_extracts_path_skills_env_alias() -> None:
    text = (FIXTURES / "profile_show_default.txt").read_text(encoding="utf-8")
    show = parse_profile_show(text)

    assert isinstance(show, ProfileShow)
    assert show.name == "test-research-probe"
    assert show.path == "/Users/example/.hermes/profiles/test-research-probe"
    assert show.gateway == "stopped"
    assert show.skills == 77
    assert show.env_configured is False
    assert show.soul_md_exists is True
    assert show.alias == "/Users/example/.local/bin/test-research-probe"


def test_parse_profile_show_env_configured_when_no_not_configured_marker() -> None:
    synthetic = (
        "Profile: x\n"
        "Path:    /tmp/x\n"
        "Gateway: stopped\n"
        "Skills:  0\n"
        ".env:    /path/to/.env\n"
        "SOUL.md: missing\n"
        "Alias:   —\n"
    )
    show = parse_profile_show(synthetic)
    assert show.env_configured is True
    assert show.soul_md_exists is False
    assert show.alias is None


# ---------------------------------------------------------------------------
# classify_create_error
# ---------------------------------------------------------------------------


def test_classify_create_error_dup() -> None:
    text = (FIXTURES / "profile_create_dup_error.txt").read_text(encoding="utf-8")
    assert classify_create_error(text) == "duplicate"


def test_classify_create_error_invalid_name() -> None:
    text = (FIXTURES / "profile_create_invalid_name.txt").read_text(encoding="utf-8")
    assert classify_create_error(text) == "invalid_name"


def test_classify_create_error_reserved_default() -> None:
    text = (FIXTURES / "profile_create_default_rejected.txt").read_text(encoding="utf-8")
    assert classify_create_error(text) == "reserved_name"


def test_classify_create_error_unknown() -> None:
    assert classify_create_error("some other error") == "unknown"


# ---------------------------------------------------------------------------
# parse_gateway_pid_file
# ---------------------------------------------------------------------------


def test_parse_gateway_pid_json() -> None:
    text = (FIXTURES / "gateway_pid_default.json").read_text(encoding="utf-8")
    result = parse_gateway_pid_file(text)
    assert result is not None
    assert isinstance(result, GatewayPidFile)
    assert result.pid == 2909
    assert result.kind == "hermes-gateway"
    assert "gateway" in result.argv
    assert result.start_time is None


def test_parse_gateway_pid_json_invalid_returns_none() -> None:
    assert parse_gateway_pid_file("not json") is None
    assert parse_gateway_pid_file("") is None
    assert parse_gateway_pid_file('{"missing_pid": true}') is None
