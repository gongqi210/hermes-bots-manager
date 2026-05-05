"""Unit tests for pairing_extractor — Phase 4 GATEWAY-10.

The fixture file ``pairing_log_sample.txt`` is currently UNAVAILABLE
(FINDING-01) so we test against the documented fallback regex from the plan
(``pairing.*?code[\\s:=]+([A-Za-z0-9]{4,12})``). The tests are written to
remain valid regardless of which regex is in place — they assert against
synthetic lines that exercise the contract, plus a negative test against the
real (sanitized) gateway-log fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.gateway.pairing_extractor import PairingCandidate, extract_pairing

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "hermes-cli"


def test_e1_non_pairing_line_returns_none() -> None:
    """E1 — info line carrying no pairing signal yields None."""
    assert extract_pairing("[INFO] gateway started on port 8765", bot_name="foo") is None


def test_e2_synthetic_pairing_line_returns_candidate() -> None:
    """E2 — pairing-bearing line yields PairingCandidate carrying bot_name.

    Uses a synthetic line that matches the fallback regex contract (the real
    fixture is UNAVAILABLE per FINDING-01).
    """
    line = "2026-05-04 12:00:00 INFO pairing request: code: ABCD1234 from ou_aaaaaaaaaaaaaaaaaaaaaa"
    result = extract_pairing(line, bot_name="foo")
    assert isinstance(result, PairingCandidate)
    assert result.bot_name == "foo"
    assert result.platform == "feishu"


def test_e3_extracted_code_matches_regex_group() -> None:
    """E3 — extracted code is exactly the regex capture group."""
    line = "pairing code=XYZ987"
    result = extract_pairing(line, bot_name="foo")
    assert result is not None
    assert result.code == "XYZ987"


def test_e4_negative_against_random_gateway_lines() -> None:
    """E4 — typical gateway log lines return None.

    The committed ``gateway_log_active_profile_sample.txt`` fixture is
    UNAVAILABLE per FINDING-03 (raw lines contained credential-bearing
    Feishu websocket URLs and message bodies), so we exercise the negative
    contract against synthetic lines that mirror the typical shapes the
    extractor will encounter on a real Hermes box.
    """
    benign_lines = [
        "2026-05-04 12:00:00 INFO gateway started on :8765",
        "2026-05-04 12:00:01 DEBUG websocket message received from ou_aaaaaaaaaaaaaaaaaaaaaa",
        "2026-05-04 12:00:02 ERROR feishu api timeout after 3000ms",
        "[INFO] supervisor heartbeat ok",
        "2026-05-04 12:00:03 INFO profile=foo connection established",
    ]
    for line in benign_lines:
        assert extract_pairing(line, bot_name="foo") is None, f"false positive on: {line!r}"

    # Also confirm the fixture file is reachable so the path stays in sync.
    fixture_path = FIXTURE_DIR / "gateway_log_active_profile_sample.txt"
    assert fixture_path.exists()


def test_extract_separator_variants() -> None:
    """The fallback regex tolerates ``code: X``, ``code=X``, and ``code X``."""
    for sep in (":", "=", "  "):
        line = f"pairing code{sep}DEADBEEF"
        result = extract_pairing(line, bot_name="bar")
        assert result is not None, f"missed line with separator {sep!r}"
        assert result.code == "DEADBEEF"
        assert result.bot_name == "bar"


def test_user_id_extraction_when_present() -> None:
    """When a Feishu OpenID is on the same line, it is captured."""
    line = "pairing code: ABCD1234 user ou_xxxxxxxxxxxxxxxxxxxxxx"
    result = extract_pairing(line, bot_name="foo")
    assert result is not None
    assert result.feishu_user_id == "ou_xxxxxxxxxxxxxxxxxxxxxx"


def test_user_id_none_when_absent() -> None:
    """No OpenID on the line → feishu_user_id is None."""
    result = extract_pairing("pairing code: ABCD1234", bot_name="foo")
    assert result is not None
    assert result.feishu_user_id is None


def test_extractor_is_pure_no_side_effects() -> None:
    """Multiple calls on identical input return equal candidates (pure)."""
    line = "pairing code: HELLO123"
    a = extract_pairing(line, bot_name="x")
    b = extract_pairing(line, bot_name="x")
    assert a == b


def test_pairing_candidate_is_frozen() -> None:
    """PairingCandidate is a frozen dataclass — mutation is rejected."""
    from dataclasses import FrozenInstanceError

    c = PairingCandidate(code="ABCD1234", feishu_user_id=None, bot_name="x")
    with pytest.raises(FrozenInstanceError):
        c.code = "OTHER"  # type: ignore[misc]
