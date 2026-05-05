"""BotService — orchestrates HermesCli + ProfileFs + DB to deliver BOT-* requirements.

Critical pattern (Pitfall #4): subprocess calls happen OUTSIDE the SQL
transaction. SQLite serializes writes, and a 60s ``hermes profile create``
under an open tx would starve every other writer until it returned.

Order of operations on create:
  1. Validate name (Pydantic at API boundary already; ProfileFsAdapter validates
     belt-and-suspenders).
  2. CLI ``profile create --no-alias`` (outside tx, may raise HermesCliError).
  3. DB INSERT (own tx, may raise IntegrityError on duplicate; we then
     best-effort delete the just-created profile to roll back).
  4. ``fs.write_env`` — plaintext to disk (mode 0600). DB already has the
     Fernet ciphertext.

Status probe (W3): for the default profile we read ``~/.hermes/gateway.pid``;
for named profiles we read ``<profile_dir>/gateway.pid`` (which v0.8 doesn't
write yet — Pitfall #1). When the file is missing we return
``(None, False)`` so :func:`decide_bot_status` returns GREY "Gateway 未启动".

Skills count (W4): scans ``<profile>/skills/`` and counts entries; missing dir
→ 0 (no exception).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import (
    BotStatus,
    HermesCliAdapter,
    HermesCliError,
    ProfileFsAdapter,
    decide_bot_status,
    parse_gateway_pid_file,
)
from app.adapters.hostops import HostOps
from app.auth.crypto import decrypt_str, encrypt_str
from app.models.bot import Bot
from app.schemas.bot import BotCloneIn, BotCreateIn, BotFeishuCredentialsIn, BotOut, BotRenameIn
from app.schemas.gateway import GatewayState, GatewayStatusOut

logger = logging.getLogger(__name__)


class DuplicateBotError(Exception):
    """Raised when a Bot with the requested name already exists.

    Mapped by the API layer to HTTP 409. The exception's ``str()`` is the
    offending name so the API can format ``"Bot '{e}' 已存在"``.
    """


class AppIdConflictError(Exception):
    """Raised when 一个飞书 App ID 已被其他 Bot 占用 — 引导用户去查重而非覆盖。"""


class BotNotFoundError(Exception):
    """Raised when a target Bot doesn't exist (rename / delete on missing name).

    Mapped by the API layer to HTTP 404.
    """


class BotService:
    def __init__(
        self,
        session: AsyncSession,
        cli: HermesCliAdapter,
        fs: ProfileFsAdapter,
        archive_dir: Path,
    ) -> None:
        self.session = session
        self.cli = cli
        self.fs = fs
        self.archive_dir = archive_dir

    # ------------------------------------------------------------------
    # W3 — Gateway probe helper (per-profile pid file is Phase 4 work).
    # ------------------------------------------------------------------
    async def _probe_gateway_state(self, name: str) -> tuple[str | None, bool]:
        """Read the gateway PID file and return ``(state, pid_alive)``.

        Returns:
          - ``(None, False)``     — pid file missing / unparseable → GREY
          - ``("running", True)`` — pid file present + process alive → GREEN
          - ``("running", False)``— pid file present, process gone → RED
            (decider matrix maps "running + not alive" to RED "PID 文件存在但进程不在")
        """
        # Pitfall #1: v0.8 Hermes shares one ``~/.hermes/gateway.pid`` across
        # profiles. Phase 4 introduces per-profile gateway with per-profile pid
        # files. For now: default → root pid file; named → not probed (returns
        # GREY because the per-profile pid file we look for doesn't exist).
        if name == "default":
            pid_file = self.fs.hermes_home / "gateway.pid"
        else:
            pid_file = self.fs.profile_dir(name) / "gateway.pid"

        if not await self.fs.host.path_exists(pid_file):
            return (None, False)
        try:
            content = await self.fs.host.read_text(pid_file)
        except FileNotFoundError:
            return (None, False)
        parsed = parse_gateway_pid_file(content)
        if parsed is None:
            return (None, False)
        proc = self.fs.host.get_process_info(parsed.pid)
        if proc is None or not proc.is_alive:
            # File exists but process is gone — surface as 'running + not alive'
            # to the decider; it returns RED with "PID 文件存在但进程不在 (残留)".
            return ("running", False)
        return ("running", True)

    # ------------------------------------------------------------------
    # W4 — Skills count helper.
    # ------------------------------------------------------------------
    async def _count_skill_markers(self, path: Path, *, depth: int = 0, max_depth: int = 3) -> int:
        """Count recursive ``SKILL.md`` markers under a Hermes skills directory."""
        marker = path / "SKILL.md"
        try:
            if await self.fs.host.path_exists(marker):
                return 1
            if depth >= max_depth:
                return 0
            entries = await self.fs.host.list_dir(path)
        except (FileNotFoundError, NotADirectoryError, OSError):
            return 0

        total = 0
        for entry in entries:
            if entry.startswith("."):
                continue
            total += await self._count_skill_markers(
                path / entry,
                depth=depth + 1,
                max_depth=max_depth,
            )
        return total

    async def _count_skills(self, name: str) -> int:
        """Count real Hermes skills under ``<profile>/skills/``.

        Hermes v0.8 installs skills as ``skills/<category>/<skill>/SKILL.md``.
        Older Phase 2 tests used direct files under ``skills/``; if no
        ``SKILL.md`` markers are found, keep that legacy entry-count fallback
        so filesystem-only profiles still show a non-zero rough count.
        """
        try:
            skills_dir = self.fs.profile_dir(name) / "skills"
            if not await self.fs.host.path_exists(skills_dir):
                return 0
            marker_count = await self._count_skill_markers(skills_dir)
            if marker_count:
                return marker_count
            entries = await self.fs.host.list_dir(skills_dir)
            return len(entries)
        except (FileNotFoundError, NotADirectoryError, OSError):
            return 0

    async def _read_model_name(self, name: str) -> str | None:
        """Read the operator-visible model name from ``config.yaml`` if present."""
        path = self.fs.config_path(name)
        try:
            if not await self.fs.host.path_exists(path):
                return None
            text = await self.fs.host.read_text(path)
            parsed = yaml.safe_load(text) or {}
        except (FileNotFoundError, yaml.YAMLError):
            return None
        if not isinstance(parsed, dict):
            return None
        model_block: Any = parsed.get("model")
        if not isinstance(model_block, dict):
            return None
        value = model_block.get("default") or model_block.get("model")
        return value if isinstance(value, str) and value.strip() else None

    # ------------------------------------------------------------------
    # List + filter.
    # ------------------------------------------------------------------
    async def list_bots(
        self,
        *,
        q: str | None = None,
        status_filter: str | None = None,
        tag: str | None = None,  # B1: tag filter
    ) -> list[BotOut]:
        """Source-of-truth merge: filesystem first, DB metadata enriches.

        - ``q``: substring match on Bot name (case-insensitive).
        - ``status_filter``: exact match on status enum value (green/yellow/red/grey).
        - ``tag``: exact membership in the Bot's tags list (B1).

        Sort: ``last_active_at`` DESC (most recent first); NULL last_active_at
        sorts to the end; ties broken by name ASC. (W1)
        """
        fs_names = await self.fs.list_profiles()
        db_rows: list[Bot] = list((await self.session.scalars(select(Bot))).all())
        db_by_name = {b.name: b for b in db_rows}

        results: list[BotOut] = []
        for name in fs_names:
            bot = db_by_name.get(name)
            env_exists = await self.fs.host.path_exists(self.fs.env_path(name))
            profile_exists = True
            gw_state, alive = await self._probe_gateway_state(name)
            color, why = decide_bot_status(
                profile_exists=profile_exists,
                env_exists=env_exists,
                gateway_state=gw_state,
                pid_alive=alive,
                cmdline_matches=False,
                cmdline_check_enabled=False,  # Phase 4 flips
            )
            skills_n = await self._count_skills(name)
            config_model_name = await self._read_model_name(name)
            results.append(
                self._to_botout(
                    name,
                    bot,
                    status=color,
                    why=why,
                    skills_count=skills_n,
                    config_model_name=config_model_name,
                )
            )

        if q:
            ql = q.lower()
            results = [r for r in results if ql in r.name.lower()]
        if status_filter:
            results = [r for r in results if r.status.value == status_filter]
        if tag:
            results = [r for r in results if tag in r.tags]

        # W1: last_active_at DESC; NULL → -inf (sorts to end); name ASC tiebreak.
        # We negate timestamp so smaller (more recent) sorts first under sort()
        # default ascending. NULL gets 0 → sorts after any positive timestamp
        # (ie at the end), matching "Bots that have never run go to the bottom".
        results.sort(
            key=lambda b: (
                -(b.last_active_at.timestamp() if b.last_active_at else 0),
                b.name,
            )
        )
        return results

    def _to_botout(
        self,
        name: str,
        bot: Bot | None,
        *,
        status: BotStatus,
        why: str,
        skills_count: int = 0,
        config_model_name: str | None = None,
    ) -> BotOut:
        last4: str | None = None
        app_id: str | None = None
        tags: list[str] = []
        last_active_at: datetime | None = None
        created_at = datetime.now(UTC)
        model_name: str | None = config_model_name
        id_ = 0
        # Phase 3 wizard config defaults (used when bot is None — filesystem-only).
        domain = "feishu"
        connection_mode = "websocket"
        group_strategy = "mention"
        if bot is not None:
            id_ = bot.id
            app_id = bot.feishu_app_id
            tags = list(bot.tags or [])
            last_active_at = bot.last_active_at
            created_at = bot.created_at
            model_name = config_model_name or bot.model_name
            domain = bot.domain or domain
            connection_mode = bot.connection_mode or connection_mode
            group_strategy = bot.group_strategy or group_strategy
            if bot.feishu_app_secret_enc:
                try:
                    plain = decrypt_str(bot.feishu_app_secret_enc)
                    last4 = plain[-4:] if len(plain) >= 4 else plain
                except Exception:
                    logger.warning("decrypt failed for bot %s", name)
        return BotOut(
            id=id_,
            name=name,
            feishu_app_id=app_id,
            feishu_app_secret_last4=last4,
            model_name=model_name,
            tags=tags,
            skills_count=skills_count,
            today_message_count=0,  # Phase 4
            last_heartbeat_at=None,  # Phase 4
            status=status,
            why=why,
            last_active_at=last_active_at,
            created_at=created_at,
            domain=domain,
            connection_mode=connection_mode,
            group_strategy=group_strategy,
        )

    # ------------------------------------------------------------------
    # Mutation flows.
    # ------------------------------------------------------------------
    async def create_bot(self, payload: BotCreateIn) -> BotOut:
        """Create a Bot: CLI create → DB INSERT → write .env.

        Rollback strategy: if the DB INSERT fails (eg unique-name collision
        from a parallel request — shouldn't happen in single-worker mode but
        defense in depth), we attempt to ``profile_delete`` the just-created
        Hermes profile so the filesystem state stays consistent.
        """
        try:
            await self.cli.profile_create(payload.name)
        except HermesCliError as e:
            if e.hint == "duplicate":
                raise DuplicateBotError(payload.name) from e
            raise

        secret_plain = (
            payload.feishu_app_secret.get_secret_value() if payload.feishu_app_secret else None
        )
        secret_enc = encrypt_str(secret_plain) if secret_plain else None

        try:
            bot = Bot(
                name=payload.name,
                feishu_app_id=payload.feishu_app_id,
                feishu_app_secret_enc=secret_enc,
                tags=list(payload.tags),
                domain=payload.domain,
                connection_mode=payload.connection_mode,
                group_strategy=payload.group_strategy,
            )
            self.session.add(bot)
            await self.session.commit()
            await self.session.refresh(bot)
        except IntegrityError as e:
            await self.session.rollback()
            try:
                await self.cli.profile_delete(payload.name)
            except HermesCliError:
                logger.exception("rollback profile_delete failed for %s", payload.name)
            raise DuplicateBotError(payload.name) from e

        # Plaintext .env on disk (Hermes reads literal KEY=VALUE; mode 0600).
        env_dict: dict[str, str] = {}
        if payload.feishu_app_id:
            env_dict["FEISHU_APP_ID"] = payload.feishu_app_id
        if secret_plain:
            env_dict["FEISHU_APP_SECRET"] = secret_plain
        if env_dict:
            await self.fs.write_env(payload.name, env_dict)

        return self._to_botout(payload.name, bot, status=BotStatus.GREY, why="未配置 Gateway")

    async def clone_bot(self, source_name: str, payload: BotCloneIn) -> BotOut:
        """Clone an existing profile via ``hermes profile create --clone-from``.

        Hermes copies SOUL.md + skills/ + .env to the new profile. We then
        insert a DB row for the clone (with its own tags = []).
        """
        try:
            await self.cli.profile_create(payload.new_name, clone_from=source_name)
        except HermesCliError as e:
            if e.hint == "duplicate":
                raise DuplicateBotError(payload.new_name) from e
            raise
        try:
            bot = Bot(name=payload.new_name, tags=[])
            self.session.add(bot)
            await self.session.commit()
            await self.session.refresh(bot)
        except IntegrityError as e:
            await self.session.rollback()
            raise DuplicateBotError(payload.new_name) from e
        return self._to_botout(
            payload.new_name, bot, status=BotStatus.GREY, why="克隆完成 — 未配置 Gateway"
        )

    async def rename_bot(self, old_name: str, payload: BotRenameIn) -> BotOut:
        """Rename via ``hermes profile rename`` then update the DB row.

        Raises BotNotFoundError if the DB row is missing (filesystem-only
        Bots can't be renamed via this API yet — they need a Bot create first
        per phase-2 conventions).
        """
        await self.cli.profile_rename(old_name, payload.new_name)
        bot = (await self.session.scalars(select(Bot).where(Bot.name == old_name))).first()
        if bot is None:
            raise BotNotFoundError(old_name)
        bot.name = payload.new_name
        try:
            await self.session.commit()
            await self.session.refresh(bot)
        except IntegrityError as e:
            await self.session.rollback()
            raise DuplicateBotError(payload.new_name) from e
        return self._to_botout(payload.new_name, bot, status=BotStatus.GREY, why="重命名完成")

    async def delete_bot(self, name: str, *, confirm_name: str) -> None:
        """Archive then delete: ``profile export -o`` → ``profile delete -y``.

        Archive failure is logged but does not abort delete — better to lose
        the archive than to leave a half-deleted Bot. The 30-day cleanup task
        sweeps stale tarballs.

        ``confirm_name`` MUST equal ``name`` — guards against typo'd DELETEs.
        """
        if confirm_name != name:
            raise ValueError(f"二次确认名称不匹配：期望 '{name}'，收到 '{confirm_name}'")  # noqa: RUF001

        self.archive_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        archive_path = self.archive_dir / f"{name}-{ts}.tar.gz"
        try:
            await self.cli.profile_export(name, output_path=archive_path)
        except HermesCliError:
            logger.warning("profile_export failed for %s; continuing with delete", name)

        await self.cli.profile_delete(name)

        bot = (await self.session.scalars(select(Bot).where(Bot.name == name))).first()
        if bot is not None:
            await self.session.delete(bot)
            await self.session.commit()

    # ------------------------------------------------------------------
    # Phase 4 — compute_gateway_status (NFR-06 triple-PID validation).
    # ------------------------------------------------------------------
    async def compute_gateway_status(
        self,
        bot_name: str,
        *,
        host: HostOps,
        fs: ProfileFsAdapter,
        cli: HermesCliAdapter,
    ) -> GatewayStatusOut:
        """Triple-PID validation per NFR-06 / Pattern 4 of 04-RESEARCH.md.

        Layers:
          1. ``HostOps.get_process_info(pid).is_alive`` (psutil pid_exists +
             status check).
          2. ``HostOps.signal_zero(pid)`` (``os.kill(pid, 0)`` no-op signal —
             confirms the process accepts signals from this UID).
          3. ``HostOps.read_active_profile() == bot_name`` (singleton-gateway
             attribution per FINDING-03 — defends against the "running but
             for the wrong profile" case, Pitfall #1).

        Per FINDING-03 / D-17, when layer 3 fails (cmdline_match=False) we
        return GREY/"stopped" rather than RED/"error" — it is not this Bot's
        fault that the singleton gateway is currently bound to a sibling
        profile. ``cmdline_check_enabled=True`` flips on the Phase 4 decider
        branch.
        """
        from datetime import UTC, datetime

        # cli is read for parity with the route signature even though we no
        # longer need to shell out (status is filesystem-driven). Future-
        # proofing: if Hermes adds a queryable status RPC we can call it
        # without breaking the public interface.
        _ = cli

        profile_exists = await fs.host.path_exists(fs.profile_dir(bot_name))
        env_exists = await fs.host.path_exists(fs.env_path(bot_name))

        # Hermes v0.8 has existed in both shapes:
        #   - older singleton gateway writes ``~/.hermes/gateway.pid``;
        #   - current named-profile services write
        #     ``~/.hermes/profiles/<name>/gateway.pid``.
        # Prefer the scoped file for named profiles, then fall back to the
        # singleton file so the default profile and older installs still work.
        scoped_pid_file_path = fs.profile_dir(bot_name) / "gateway.pid"
        singleton_pid_file_path = fs.hermes_home / "gateway.pid"
        pid_file_path = singleton_pid_file_path
        pid_file_is_scoped = False
        if bot_name != "default" and await fs.host.path_exists(scoped_pid_file_path):
            pid_file_path = scoped_pid_file_path
            pid_file_is_scoped = True
        pid: int | None = None
        gateway_state_str: str | None = None
        pid_alive = False
        cmdline_matches = False
        active_profile: str | None = None

        if await fs.host.path_exists(pid_file_path):
            try:
                txt = await host.read_text(pid_file_path)
                parsed = parse_gateway_pid_file(txt)
                pid = parsed.pid if parsed is not None else None
            except FileNotFoundError:
                pid = None

        if pid is not None:
            # File exists, so Hermes thinks a gateway is up. The decider needs
            # ``gateway_state="running"`` to fire its pid-alive / cmdline
            # branches (this matches Phase 2 _probe_gateway_state semantics).
            gateway_state_str = "running"
            info = host.get_process_info(pid)
            if info is not None and info.is_alive and await host.signal_zero(pid):
                # Layer 3: signal_zero confirms the process accepts signals
                # from us (defends against zombie / wrong-UID rows).
                pid_alive = True
                if pid_file_is_scoped:
                    active_profile = bot_name
                    cmdline_matches = True
                else:
                    active_profile = await host.read_active_profile()
                    cmdline_matches = active_profile == bot_name

        bot_status, why = decide_bot_status(
            profile_exists=profile_exists,
            env_exists=env_exists,
            gateway_state=gateway_state_str,
            pid_alive=pid_alive,
            cmdline_matches=cmdline_matches,
            cmdline_check_enabled=True,  # Phase 4: GATEWAY-14 + Pitfall #1
        )

        state = self._map_gateway_state(
            bot_status=bot_status,
            gateway_state=gateway_state_str,
            pid_alive=pid_alive,
            cmdline_matches=cmdline_matches,
            env_exists=env_exists,
            profile_exists=profile_exists,
        )

        # Update the cached state + transition timestamp on the Bot row.
        bot = (
            await self.session.execute(select(Bot).where(Bot.name == bot_name))
        ).scalar_one_or_none()
        last_changed: datetime | None = None
        if bot is not None:
            if bot.gateway_state_cache != state:
                bot.gateway_state_cache = state
                bot.gateway_state_changed_at = datetime.now(UTC)
                await self.session.commit()
            last_changed = bot.gateway_state_changed_at

        return GatewayStatusOut(
            bot_name=bot_name,
            state=state,
            why=why,
            last_state_changed_at=last_changed,
            pid=pid,
            active_profile=active_profile,
            is_active_profile=cmdline_matches,
        )

    @staticmethod
    def _map_gateway_state(
        *,
        bot_status: BotStatus,
        gateway_state: str | None,
        pid_alive: bool,
        cmdline_matches: bool,
        env_exists: bool,
        profile_exists: bool,
    ) -> GatewayState:
        """Map decide_bot_status output + raw flags to the 5-state vocabulary."""
        if not profile_exists or not env_exists:
            return "unconfigured"
        if gateway_state == "running" and pid_alive and cmdline_matches:
            return "running"
        if gateway_state == "starting":
            return "starting"
        if bot_status == BotStatus.RED:
            return "error"
        return "stopped"

    async def update_feishu_credentials(
        self, name: str, payload: BotFeishuCredentialsIn
    ) -> BotOut:
        """Wizard step 2: save App ID, App Secret, domain, connection, and group.

        - Missing bot -> BotNotFoundError.
        - App ID owned by another bot -> AppIdConflictError. Same bot may update.
        - Encrypt secret in DB and rewrite .env for Hermes.
        """
        bot = (await self.session.scalars(select(Bot).where(Bot.name == name))).first()
        if bot is None:
            raise BotNotFoundError(name)

        # Uniqueness check across other Bots.
        other = (
            await self.session.scalars(
                select(Bot).where(
                    Bot.feishu_app_id == payload.feishu_app_id,
                    Bot.name != name,
                )
            )
        ).first()
        if other is not None:
            raise AppIdConflictError(other.name)

        plain = payload.feishu_app_secret.get_secret_value()
        bot.feishu_app_id = payload.feishu_app_id
        bot.feishu_app_secret_enc = encrypt_str(plain)
        bot.domain = payload.domain
        bot.connection_mode = payload.connection_mode
        bot.group_strategy = payload.group_strategy
        bot.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(bot)

        env_dict = await self.fs.read_env(name)
        env_dict["FEISHU_APP_ID"] = payload.feishu_app_id
        env_dict["FEISHU_APP_SECRET"] = plain
        env_dict["FEISHU_CONNECTION_MODE"] = "websocket"
        if payload.domain == "lark":
            env_dict["FEISHU_DOMAIN"] = "lark"
        else:
            env_dict.pop("FEISHU_DOMAIN", None)
        if payload.group_strategy != "mention":
            env_dict["FEISHU_GROUP_STRATEGY"] = payload.group_strategy
        else:
            env_dict.pop("FEISHU_GROUP_STRATEGY", None)
        await self.fs.write_env(name, env_dict)

        return self._to_botout(name, bot, status=BotStatus.GREY, why="飞书凭证已保存")

    async def reset_secret(self, name: str, new_secret: SecretStr) -> BotOut:
        """FEISHU-04: replace ``feishu_app_secret_enc`` in DB and rewrite ``.env``.

        Order of operations:
          1. Load Bot row by name (404 if missing)
          2. Encrypt new secret with Fernet (NFR-02)
          3. Update DB row + bump updated_at
          4. Rewrite ``.env`` with new plaintext value (mode 0600)

        DB write happens BEFORE filesystem write — if the .env rewrite fails,
        the next gateway start would still authenticate with the new secret
        from the DB-driven config flow (Phase 4). Stale .env is recoverable;
        stale DB ciphertext breaks the BotOut last4 display.
        """
        bot = (await self.session.scalars(select(Bot).where(Bot.name == name))).first()
        if bot is None:
            raise BotNotFoundError(name)

        plain = new_secret.get_secret_value()
        bot.feishu_app_secret_enc = encrypt_str(plain)
        # SQLAlchemy onupdate fires automatically, but stamp explicitly for
        # consistency with create flow (audit log uses updated_at).
        bot.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(bot)

        # Rewrite .env preserving any existing keys that aren't FEISHU_APP_SECRET.
        env_dict = await self.fs.read_env(name)
        env_dict["FEISHU_APP_SECRET"] = plain
        if bot.feishu_app_id and "FEISHU_APP_ID" not in env_dict:
            env_dict["FEISHU_APP_ID"] = bot.feishu_app_id
        await self.fs.write_env(name, env_dict)

        return self._to_botout(name, bot, status=BotStatus.GREY, why="Secret 已重置")
