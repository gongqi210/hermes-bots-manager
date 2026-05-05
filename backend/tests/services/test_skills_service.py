"""Skills service unit tests — Tasks 1 & 2 (TDD).

Tests cover:
- Task 1: shadow detection, disabled scan, requires_tools parsing, dep-check
- Task 2: filesystem diff-sync (sync_skills_fs)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from app.adapters.profile_fs import ProfileFsAdapter
from tests.adapters.fakes import InMemoryHostOps

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HERMES_HOME = Path("/h")


def _make_fs(host: InMemoryHostOps) -> ProfileFsAdapter:
    return ProfileFsAdapter(host, hermes_home=HERMES_HOME)


def _write_skill_md(host: InMemoryHostOps, skill_path: Path, meta: dict) -> None:
    """Write a SKILL.md with YAML frontmatter into the in-memory host."""
    frontmatter = yaml.safe_dump(meta, allow_unicode=True)
    host.fs[skill_path] = f"---\n{frontmatter}---\n\nSkill body.\n"


# ---------------------------------------------------------------------------
# Task 1 Tests
# ---------------------------------------------------------------------------


async def test_discover_skills_shadow_detection() -> None:
    """Profile skill shadows global with same name.

    The winner (profile skill) should have shadowed_source="global".
    The global version should NOT be in the result list.
    """
    from app.services.management import discover_skills

    host = InMemoryHostOps()
    fs = _make_fs(host)

    profile_skills = HERMES_HOME / "profiles" / "mybot" / "skills"
    global_skills = Path("/global/skills")

    # Profile skill "coder"
    _write_skill_md(
        host,
        profile_skills / "coder" / "SKILL.md",
        {"name": "coder", "description": "profile coder", "category": "dev"},
    )
    # Global skill "coder" (same name — should be shadowed)
    _write_skill_md(
        host,
        global_skills / "coder" / "SKILL.md",
        {"name": "coder", "description": "global coder", "category": "dev"},
    )
    # Global skill "search" (no profile equivalent — should appear)
    _write_skill_md(
        host,
        global_skills / "search" / "SKILL.md",
        {"name": "search", "description": "web search", "category": "tools"},
    )

    items = await discover_skills(fs, host, "mybot", global_skills_dir=global_skills)

    names = {i["name"] for i in items}
    # Only one "coder", plus "search"
    assert "coder" in names
    assert "search" in names
    assert len([i for i in items if i["name"] == "coder"]) == 1

    coder = next(i for i in items if i["name"] == "coder")
    assert coder["source"] == "profile"
    assert coder.get("shadowed_source") == "global", (
        "profile skill overriding a global should have shadowed_source='global'"
    )


async def test_discover_skills_includes_disabled_dir(tmp_path: Path) -> None:
    """Skills in .disabled/ are returned with enabled=False and source='profile'."""
    from app.adapters.local_hostops import LocalHostOps
    from app.services.management import discover_skills

    # Build real filesystem structure
    bot_profile = tmp_path / "profiles" / "mybot"
    skills_dir = bot_profile / "skills"
    disabled_dir = skills_dir / ".disabled"
    disabled_skill = disabled_dir / "archiver"
    disabled_skill.mkdir(parents=True)
    (disabled_skill / "SKILL.md").write_text(
        "---\nname: archiver\ndescription: archive skill\ncategory: util\n---\n"
    )

    # Also a normal enabled skill
    enabled_skill = skills_dir / "searcher"
    enabled_skill.mkdir(parents=True)
    (enabled_skill / "SKILL.md").write_text(
        "---\nname: searcher\ndescription: search skill\ncategory: tools\n---\n"
    )

    hermes_home = tmp_path
    host = LocalHostOps()
    fs = ProfileFsAdapter(host, hermes_home=hermes_home)

    items = await discover_skills(fs, host, "mybot", global_skills_dir=None)

    disabled_items = [i for i in items if i["name"] == "archiver"]
    assert len(disabled_items) == 1, "archiver skill from .disabled/ should be in results"
    assert disabled_items[0]["enabled"] is False
    assert disabled_items[0]["source"] == "profile"

    enabled_items = [i for i in items if i["name"] == "searcher"]
    assert len(enabled_items) == 1
    assert enabled_items[0]["enabled"] is True


async def test_parse_skill_md_requires_tools() -> None:
    """parse_skill_md reads requires_tools from frontmatter."""
    from app.services.management import parse_skill_md

    text = "---\nname: ripper\ndescription: uses ripgrep\nrequires_tools:\n  - ripgrep\n  - ffmpeg\n---\n"
    result = parse_skill_md(text)
    assert result.get("requires_tools") == ["ripgrep", "ffmpeg"]


async def test_check_missing_deps_returns_absent_tools() -> None:
    """check_missing_deps returns tools not found via shutil.which."""
    from app.schemas.management import SkillItem
    from app.services.management import check_missing_deps

    skill = SkillItem(name="ripper", requires_tools=["ripgrep", "ffmpeg"])

    with patch("shutil.which", side_effect=lambda t: "/usr/bin/ffmpeg" if t == "ffmpeg" else None):
        missing = check_missing_deps(skill)

    assert missing == ["ripgrep"]


async def test_skill_item_shadowed_source_serializes() -> None:
    """SkillItem with shadowed_source='global' serializes without error."""
    from app.schemas.management import SkillItem

    item = SkillItem(
        name="test",
        shadowed_source="global",
        missing_deps=["ripgrep"],
    )
    data = item.model_dump()
    assert data["shadowed_source"] == "global"
    assert data["missing_deps"] == ["ripgrep"]


# ---------------------------------------------------------------------------
# Task 2 Tests
# ---------------------------------------------------------------------------


async def test_sync_skills_fs_disables_skill(tmp_path: Path) -> None:
    """Skills not in enabled_names are moved to .disabled/."""
    from app.services.management import sync_skills_fs

    skills_dir = tmp_path / "skills"
    skill_a = skills_dir / "A"
    skill_b = skills_dir / "B"
    skill_c = skills_dir / "C"
    for d in (skill_a, skill_b, skill_c):
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: test\n---\n")

    await sync_skills_fs(skills_dir, enabled_names={"A", "B"})

    disabled_dir = skills_dir / ".disabled"
    assert (disabled_dir / "C").is_dir(), "C should be moved to .disabled/C"
    assert skill_a.is_dir(), "A should still be enabled"
    assert skill_b.is_dir(), "B should still be enabled"


async def test_sync_skills_fs_re_enables_skill(tmp_path: Path) -> None:
    """Skills in .disabled/ that are in enabled_names are moved back."""
    from app.services.management import sync_skills_fs

    skills_dir = tmp_path / "skills"
    disabled_dir = skills_dir / ".disabled"
    disabled_c = disabled_dir / "C"
    disabled_c.mkdir(parents=True)
    (disabled_c / "SKILL.md").write_text("---\nname: C\n---\n")
    (skills_dir / "A").mkdir()
    (skills_dir / "B").mkdir()

    await sync_skills_fs(skills_dir, enabled_names={"A", "B", "C"})

    assert (skills_dir / "C").is_dir(), "C should be moved back to skills/C"
    assert not (disabled_dir / "C").exists(), "C should not remain in .disabled/"


async def test_sync_skills_fs_is_idempotent(tmp_path: Path) -> None:
    """Running sync_skills_fs twice with same args produces same result."""
    from app.services.management import sync_skills_fs

    skills_dir = tmp_path / "skills"
    for name in ("A", "B"):
        d = skills_dir / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: test\n---\n")

    await sync_skills_fs(skills_dir, enabled_names={"A"})
    await sync_skills_fs(skills_dir, enabled_names={"A"})  # second call

    assert (skills_dir / "A").is_dir()
    assert (skills_dir / ".disabled" / "B").is_dir()


async def test_sync_skills_fs_missing_skill_no_exception(tmp_path: Path) -> None:
    """sync_skills_fs does not raise when a skill directory doesn't exist."""
    from app.services.management import sync_skills_fs

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True)

    # No skill directories at all — disabling nonexistent skill should not raise
    await sync_skills_fs(skills_dir, enabled_names=set())  # nothing to disable, nothing exists


async def test_sync_skills_fs_called_in_put_handler() -> None:
    """Verify sync_skills_fs is called from the put_skills handler (code inspection)."""
    import inspect

    import app.api.v1.management as mgmt_module

    source = inspect.getsource(mgmt_module.put_skills)
    assert "sync_skills_fs" in source, "put_skills handler must call sync_skills_fs"
    assert "snapshot_profile" in source, "put_skills handler must call snapshot_profile before sync"
