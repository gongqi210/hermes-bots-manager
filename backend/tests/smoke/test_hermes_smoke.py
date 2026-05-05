"""Real-machine smoke tests for HermesCliAdapter.

Gated by ``pytest -m smoke``. Requires ``hermes`` v0.7+ on PATH.
Closes Phase 2 success criterion #4 — verifies adapters work against the real CLI surface.

Run with: make smoke
Skip condition: smoke tests are automatically skipped in normal ``pytest`` runs;
they only execute when ``-m smoke`` is explicitly passed (conftest gating).
"""

from __future__ import annotations

import logging
import shutil
import uuid

import pytest

from app.adapters import HermesCliAdapter, HermesCliError, LocalHostOps

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(not shutil.which("hermes"), reason="hermes CLI not installed"),
]


@pytest.fixture
def adapter() -> HermesCliAdapter:
    return HermesCliAdapter(LocalHostOps())


async def test_smoke_profile_list_includes_default(adapter: HermesCliAdapter) -> None:
    """`hermes profile list` succeeds and parses to include 'default'."""
    profiles = await adapter.profile_list()
    names = [p.name for p in profiles]
    assert "default" in names, f"expected 'default' in {names}"


async def test_smoke_doctor_default_profile(adapter: HermesCliAdapter) -> None:
    """`hermes doctor` exits 0 for default."""
    ok = await adapter.doctor()
    assert ok is True


async def test_smoke_profile_show_default_returns_path(adapter: HermesCliAdapter) -> None:
    """`hermes profile show default` returns a parseable result with non-empty path."""
    # NOTE: 'default' shows special — its path differs. We just assert no crash + non-empty path.
    result = await adapter.profile_show("default")
    assert result.path != "", "expected non-empty path for default"


async def test_smoke_create_and_delete_throwaway_profile(adapter: HermesCliAdapter) -> None:
    """Round-trip: create then delete a uniquely-named profile.

    Uses an unmistakable name prefix ``gsd-smoke-`` so accidental leftovers are easy to find.
    Cleanup runs even if assertion fails (try/finally).
    """
    name = f"gsd-smoke-{uuid.uuid4().hex[:8]}"
    try:
        await adapter.profile_create(name)
        profiles = await adapter.profile_list()
        assert any(p.name == name for p in profiles), f"created profile {name} not in list"
    finally:
        try:
            await adapter.profile_delete(name)
        except Exception as e:
            # Best-effort cleanup — log if cleanup fails.
            logging.getLogger(__name__).exception("smoke cleanup failed for %s: %s", name, e)


async def test_smoke_profile_create_invalid_name_raises(
    adapter: HermesCliAdapter,
) -> None:
    """Hermes rejects names violating its own regex; adapter classifies.

    Name with uppercase violates Hermes' own [a-z0-9][a-z0-9_-]{0,63} regex.
    """
    with pytest.raises(HermesCliError) as exc:
        await adapter.profile_create("BAD-NAME-UPPERCASE")
    assert exc.value.hint in ("invalid_name", "unknown"), exc.value.hint


async def test_smoke_profile_create_default_raises_reserved(adapter: HermesCliAdapter) -> None:
    """Hermes rejects creating 'default' as a regular profile."""
    with pytest.raises(HermesCliError) as exc:
        await adapter.profile_create("default")
    assert exc.value.hint in ("reserved_name", "duplicate", "unknown"), exc.value.hint
