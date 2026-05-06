"""BotService unit tests — uses InMemoryHostOps + in-memory SQLite.

The fakes give us deterministic CLI responses + a pretend filesystem keyed by
``Path``. We never spawn a real subprocess and never touch the real Hermes
install, so these tests run in milliseconds.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import (
    BotStatus,
    CliResult,
    HermesCliAdapter,
    ProcessInfo,
    ProfileFsAdapter,
)
from app.auth.crypto import decrypt_str
from app.models.bot import Bot
from app.schemas.bot import BotCloneIn, BotCreateIn, BotRenameIn
from app.services.bot import BotNotFoundError, BotService, DuplicateBotError
from tests.adapters.fakes import InMemoryHostOps

HERMES_HOME = Path("/h")
ARCHIVE_DIR = Path("/h/archives")


def _build_service(host: InMemoryHostOps, session: AsyncSession) -> BotService:
    cli = HermesCliAdapter(host)
    fs = ProfileFsAdapter(host, hermes_home=HERMES_HOME)
    return BotService(session=session, cli=cli, fs=fs, archive_dir=ARCHIVE_DIR)


def _seed_default_profile(host: InMemoryHostOps) -> None:
    """Make ProfileFsAdapter.list_profiles() include 'default'."""
    host.fs[HERMES_HOME / "config.yaml"] = "version: 1\n"


def _seed_named_profile(host: InMemoryHostOps, name: str) -> None:
    """Make ProfileFsAdapter.list_profiles() include the given named profile.

    InMemoryHostOps treats any path under ``profiles/<name>/`` as evidence the
    profile dir exists — we drop a placeholder file to satisfy ``list_dir``.
    """
    host.fs[HERMES_HOME / "profiles" / name / "config.yaml"] = "version: 1\n"


# ------------------------------------------------------------------------
# list_bots
# ------------------------------------------------------------------------


async def test_list_bots_merges_filesystem_with_db_and_computes_status(
    session: AsyncSession,
) -> None:
    host = InMemoryHostOps()
    _seed_default_profile(host)
    _seed_named_profile(host, "alpha")
    bot = Bot(name="alpha", tags=[])
    session.add(bot)
    await session.commit()

    svc = _build_service(host, session)
    out = await svc.list_bots()

    names = sorted(b.name for b in out)
    assert names == ["alpha", "default"]
    # Neither has .env nor gateway.pid → grey.
    by_name = {b.name: b for b in out}
    assert by_name["alpha"].status == BotStatus.GREY
    assert by_name["default"].status == BotStatus.GREY
    # 'alpha' has no .env → "未配置 .env" branch is hit before gateway probe.
    assert "未配置" in by_name["alpha"].why


async def test_list_bots_filters_by_q_substring_match(session: AsyncSession) -> None:
    host = InMemoryHostOps()
    _seed_named_profile(host, "alpha")
    _seed_named_profile(host, "beta")
    _seed_named_profile(host, "alpha-prod")
    svc = _build_service(host, session)

    out = await svc.list_bots(q="alpha")
    names = sorted(b.name for b in out)
    assert names == ["alpha", "alpha-prod"]


async def test_list_bots_filters_by_status(session: AsyncSession) -> None:
    host = InMemoryHostOps()
    _seed_named_profile(host, "alpha")
    _seed_named_profile(host, "beta")
    _seed_named_profile(host, "gamma")
    svc = _build_service(host, session)

    out = await svc.list_bots(status_filter="grey")
    assert len(out) == 3
    out_red = await svc.list_bots(status_filter="red")
    assert out_red == []


async def test_list_bots_filters_by_tag(session: AsyncSession) -> None:
    """B1: ?tag=prod returns Bots whose tags JSON contains 'prod'."""
    host = InMemoryHostOps()
    _seed_named_profile(host, "alpha")
    _seed_named_profile(host, "beta")
    _seed_named_profile(host, "gamma")
    session.add_all(
        [
            Bot(name="alpha", tags=["prod", "ai"]),
            Bot(name="beta", tags=["staging"]),
            Bot(name="gamma", tags=["prod"]),
        ]
    )
    await session.commit()
    svc = _build_service(host, session)

    out_prod = sorted(b.name for b in await svc.list_bots(tag="prod"))
    assert out_prod == ["alpha", "gamma"]

    out_staging = sorted(b.name for b in await svc.list_bots(tag="staging"))
    assert out_staging == ["beta"]

    out_missing = await svc.list_bots(tag="missing")
    assert out_missing == []


async def test_list_bots_orders_by_last_active_desc(session: AsyncSession) -> None:
    """W1: most-recently-active first; NULL last_active_at sorts to the end."""
    host = InMemoryHostOps()
    _seed_named_profile(host, "alpha")
    _seed_named_profile(host, "beta")
    _seed_named_profile(host, "gamma")
    _seed_named_profile(host, "delta")
    now = datetime.now(UTC)
    session.add_all(
        [
            Bot(name="alpha", tags=[], last_active_at=now - timedelta(days=3)),  # T1 oldest
            Bot(name="beta", tags=[], last_active_at=now - timedelta(days=1)),  # T2
            Bot(name="gamma", tags=[], last_active_at=now),  # T3 newest
            Bot(name="delta", tags=[], last_active_at=None),  # never active
        ]
    )
    await session.commit()
    svc = _build_service(host, session)

    out = await svc.list_bots()
    assert [b.name for b in out] == ["gamma", "beta", "alpha", "delta"]


# ------------------------------------------------------------------------
# Gateway probe (W3)
# ------------------------------------------------------------------------


async def test_list_bots_status_green_when_gateway_pid_alive(session: AsyncSession) -> None:
    """W3: default profile + .env + valid gateway.pid + alive process → GREEN."""
    host = InMemoryHostOps()
    _seed_default_profile(host)
    host.fs[HERMES_HOME / ".env"] = "FEISHU_APP_ID=cli_x\nFEISHU_APP_SECRET=s\n"
    host.fs[HERMES_HOME / "gateway.pid"] = (
        '{"pid": 12345, "kind": "hermes-gateway", "argv": [], "start_time": null}'
    )
    host.process_table[12345] = ProcessInfo(
        pid=12345, cmdline=["hermes"], environ={}, is_alive=True
    )
    svc = _build_service(host, session)
    out = await svc.list_bots()
    assert len(out) == 1
    assert out[0].status == BotStatus.GREEN
    assert out[0].why == "运行中"


async def test_list_bots_status_red_when_gateway_pid_orphaned(session: AsyncSession) -> None:
    """W3: pid file present, but process_table empty → RED 残留."""
    host = InMemoryHostOps()
    _seed_default_profile(host)
    host.fs[HERMES_HOME / ".env"] = "FEISHU_APP_ID=cli_x\n"
    host.fs[HERMES_HOME / "gateway.pid"] = (
        '{"pid": 99999, "kind": "hermes-gateway", "argv": [], "start_time": null}'
    )
    # process_table intentionally empty — pid 99999 is gone.
    svc = _build_service(host, session)
    out = await svc.list_bots()
    assert out[0].status == BotStatus.RED
    assert "PID" in out[0].why or "残留" in out[0].why


async def test_list_bots_status_grey_when_no_gateway_pid_file(session: AsyncSession) -> None:
    """W3: no pid file → GREY 'Gateway 未启动'."""
    host = InMemoryHostOps()
    _seed_default_profile(host)
    host.fs[HERMES_HOME / ".env"] = "FEISHU_APP_ID=cli_x\n"
    svc = _build_service(host, session)
    out = await svc.list_bots()
    assert out[0].status == BotStatus.GREY
    assert "Gateway" in out[0].why


# ------------------------------------------------------------------------
# Skills count (W4)
# ------------------------------------------------------------------------


async def test_list_bots_skills_count_reflects_filesystem(session: AsyncSession) -> None:
    """W4: 2 .md files under skills/ → skills_count == 2."""
    host = InMemoryHostOps()
    _seed_named_profile(host, "alpha")
    host.fs[HERMES_HOME / "profiles" / "alpha" / "skills" / "skill_a.md"] = ""
    host.fs[HERMES_HOME / "profiles" / "alpha" / "skills" / "skill_b.md"] = ""
    svc = _build_service(host, session)
    out = await svc.list_bots()
    by_name = {b.name: b for b in out}
    assert by_name["alpha"].skills_count == 2


async def test_list_bots_skills_count_reflects_nested_skill_md(
    session: AsyncSession,
) -> None:
    """Hermes v0.8: skills/<category>/<skill>/SKILL.md → real skill count."""
    host = InMemoryHostOps()
    _seed_named_profile(host, "alpha")
    host.fs[
        HERMES_HOME / "profiles" / "alpha" / "skills" / "dev" / "plan" / "SKILL.md"
    ] = ""
    host.fs[
        HERMES_HOME / "profiles" / "alpha" / "skills" / "dev" / "DESCRIPTION.md"
    ] = ""
    svc = _build_service(host, session)
    out = await svc.list_bots()
    by_name = {b.name: b for b in out}
    assert by_name["alpha"].skills_count == 1


async def test_list_bots_skills_count_zero_when_skills_dir_missing(
    session: AsyncSession,
) -> None:
    """W4: no skills dir → skills_count == 0 (no exception)."""
    host = InMemoryHostOps()
    _seed_named_profile(host, "alpha")  # no skills subdir
    svc = _build_service(host, session)
    out = await svc.list_bots()
    assert out[0].skills_count == 0


async def test_list_bots_reads_model_name_from_config_yaml(
    session: AsyncSession,
) -> None:
    host = InMemoryHostOps()
    _seed_named_profile(host, "alpha")
    host.fs[HERMES_HOME / "profiles" / "alpha" / "config.yaml"] = (
        "model:\n  provider: openai-codex\n  default: gpt-5.5\n"
    )
    svc = _build_service(host, session)
    out = await svc.list_bots()
    assert out[0].model_name == "gpt-5.5"


# ------------------------------------------------------------------------
# create_bot
# ------------------------------------------------------------------------


async def test_create_bot_validates_name_then_calls_hermes_then_writes_db_and_env(
    session: AsyncSession,
) -> None:
    host = InMemoryHostOps()
    # profile_create returns 0 on success.
    host.queue_response(["profile", "create", "--no-alias", "alpha"], CliResult(0, "ok\n", ""))
    svc = _build_service(host, session)

    out = await svc.create_bot(
        BotCreateIn(name="alpha", feishu_app_id="cli_x", feishu_app_secret="secret")
    )

    # Hermes called with the right args.
    assert host.calls[0][0] == ["profile", "create", "--no-alias", "alpha"]

    # DB row exists.
    bot = (await session.scalars(select(Bot).where(Bot.name == "alpha"))).first()
    assert bot is not None
    assert bot.feishu_app_id == "cli_x"
    assert bot.feishu_app_secret_enc is not None
    # Encrypted column round-trips to plaintext.
    assert decrypt_str(bot.feishu_app_secret_enc) == "secret"

    # .env written to disk in plaintext, mode 0600 enforced by adapter contract.
    env_path = HERMES_HOME / "profiles" / "alpha" / ".env"
    assert env_path in host.fs
    body = host.fs[env_path]
    assert "FEISHU_APP_ID=cli_x" in body
    assert "FEISHU_APP_SECRET=secret" in body
    assert "FEISHU_CONNECTION_MODE=websocket" in body
    assert "FEISHU_GROUP_POLICY=open" in body
    assert "FEISHU_REQUIRE_MENTION=true" in body
    assert "FEISHU_GROUP_STRATEGY" not in body

    # BotOut returned with correct shape.
    assert out.name == "alpha"
    assert out.feishu_app_secret_last4 == "cret"


async def test_create_bot_rolls_back_db_on_hermes_failure(session: AsyncSession) -> None:
    """Hermes returns 'already exists' → DuplicateBotError, no DB row, no .env."""
    host = InMemoryHostOps()
    host.queue_response(
        ["profile", "create", "--no-alias", "alpha"],
        CliResult(1, "Profile 'alpha' already exists\n", ""),
    )
    svc = _build_service(host, session)

    with pytest.raises(DuplicateBotError):
        await svc.create_bot(BotCreateIn(name="alpha", feishu_app_id="x", feishu_app_secret="s"))

    bot = (await session.scalars(select(Bot).where(Bot.name == "alpha"))).first()
    assert bot is None
    assert HERMES_HOME / "profiles" / "alpha" / ".env" not in host.fs


def test_create_bot_rejects_default_name_at_validator() -> None:
    """Pydantic-level rejection of reserved 'default' — never reaches the service."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        BotCreateIn(name="default")


async def test_create_bot_rejects_duplicate_at_db_unique_constraint(
    session: AsyncSession,
) -> None:
    """Two creates with the same name → second raises DuplicateBotError.

    We simulate the rare race where Hermes profile_create succeeds but the DB
    INSERT collides on uq_bots_name (eg another worker beat us to the commit).
    """
    host = InMemoryHostOps()
    host.queue_response(["profile", "create", "--no-alias", "alpha"], CliResult(0, "ok\n", ""))
    svc = _build_service(host, session)

    await svc.create_bot(BotCreateIn(name="alpha"))
    with pytest.raises(DuplicateBotError):
        await svc.create_bot(BotCreateIn(name="alpha"))


# ------------------------------------------------------------------------
# clone_bot / rename_bot / delete_bot
# ------------------------------------------------------------------------


async def test_clone_bot_uses_clone_from_flag(session: AsyncSession) -> None:
    host = InMemoryHostOps()
    host.queue_response(
        ["profile", "create", "--no-alias", "beta", "--clone-from", "alpha"],
        CliResult(0, "ok\n", ""),
    )
    svc = _build_service(host, session)

    out = await svc.clone_bot("alpha", BotCloneIn(new_name="beta"))

    assert host.calls[0][0] == ["profile", "create", "--no-alias", "beta", "--clone-from", "alpha"]
    bot = (await session.scalars(select(Bot).where(Bot.name == "beta"))).first()
    assert bot is not None
    assert out.name == "beta"


async def test_rename_bot_calls_hermes_rename_and_updates_db(session: AsyncSession) -> None:
    host = InMemoryHostOps()
    # Seed a Bot row so rename has something to update.
    session.add(Bot(name="alpha", tags=[]))
    await session.commit()

    host.queue_response(["profile", "rename", "alpha", "alpha2"], CliResult(0, "ok\n", ""))
    svc = _build_service(host, session)

    out = await svc.rename_bot("alpha", BotRenameIn(new_name="alpha2"))

    assert host.calls[0][0] == ["profile", "rename", "alpha", "alpha2"]
    assert out.name == "alpha2"
    bot = (await session.scalars(select(Bot).where(Bot.name == "alpha2"))).first()
    assert bot is not None
    bot_old = (await session.scalars(select(Bot).where(Bot.name == "alpha"))).first()
    assert bot_old is None


async def test_rename_missing_bot_raises_not_found(session: AsyncSession) -> None:
    host = InMemoryHostOps()
    host.queue_response(["profile", "rename", "ghost", "ghost2"], CliResult(0, "ok\n", ""))
    svc = _build_service(host, session)
    with pytest.raises(BotNotFoundError):
        await svc.rename_bot("ghost", BotRenameIn(new_name="ghost2"))


async def test_delete_bot_archives_then_deletes(session: AsyncSession, tmp_path: Path) -> None:
    """delete should call profile_export then profile_delete and remove DB row."""
    host = InMemoryHostOps()
    session.add(Bot(name="alpha", tags=[]))
    await session.commit()
    archive_dir = tmp_path / "archives"

    cli = HermesCliAdapter(host)
    fs = ProfileFsAdapter(host, hermes_home=HERMES_HOME)
    svc = BotService(session=session, cli=cli, fs=fs, archive_dir=archive_dir)

    # Both calls succeed.
    host.queue_response(None, CliResult(0, "ok\n", ""))

    await svc.delete_bot("alpha", confirm_name="alpha")

    # First call was profile export with -o pointing at our archive_dir.
    assert host.calls[0][0][:3] == ["profile", "export", "alpha"]
    assert host.calls[0][0][3] == "-o"
    assert "alpha-" in host.calls[0][0][4]
    assert host.calls[0][0][4].endswith(".tar.gz")

    # Second call was profile delete -y.
    assert host.calls[1][0] == ["profile", "delete", "-y", "alpha"]

    # DB row removed.
    bot = (await session.scalars(select(Bot).where(Bot.name == "alpha"))).first()
    assert bot is None


async def test_delete_bot_validates_confirmation_name(
    session: AsyncSession, tmp_path: Path
) -> None:
    host = InMemoryHostOps()
    cli = HermesCliAdapter(host)
    fs = ProfileFsAdapter(host, hermes_home=HERMES_HOME)
    svc = BotService(session=session, cli=cli, fs=fs, archive_dir=tmp_path)
    with pytest.raises(ValueError):
        await svc.delete_bot("alpha", confirm_name="wrong")
    # No CLI calls were made.
    assert host.calls == []


async def test_bot_secret_last4_in_botout_only(session: AsyncSession) -> None:
    """to_botout exposes last4 of plaintext, never raw ciphertext."""
    host = InMemoryHostOps()
    host.queue_response(["profile", "create", "--no-alias", "alpha"], CliResult(0, "ok\n", ""))
    svc = _build_service(host, session)
    out = await svc.create_bot(
        BotCreateIn(name="alpha", feishu_app_id="cli_x", feishu_app_secret="secret")
    )
    assert out.feishu_app_secret_last4 == "cret"
    # BotOut schema doesn't expose the encrypted column at all.
    assert "feishu_app_secret_enc" not in out.model_dump()
