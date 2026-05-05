"""WizardService — 7-step SSE orchestration for FEISHU-02.

Steps (Phase 3, FEISHU-02):
  1. 校验飞书凭证          — App ID/Secret format check (re-run always)
  2. 创建 Hermes Profile    — profile create (skipped if profile dir exists)
  3. 写入加密配置           — .env write + DB ciphertext (skipped if .env exists)
  4. 检测/安装 lark-oapi    — pip show + optional install (skipped if installed)
  5. 配置 Gateway           — gateway setup (skipped if gateway.yaml exists)
  6. 启动 Gateway           — gateway start (skipped if PID file + alive process)
  7. 飞书 API 联通测试      — hermes doctor (re-run always)

CRITICAL: Steps 1 and 7 always re-run (cheap, authoritative); steps 2-6 use
filesystem re-derivation (D-09) so the wizard doubles as a safe retry path.

SECRET SAFETY (FEISHU-08):
  - Plaintext .env on disk via ProfileFsAdapter.write_env (mode 0600)
  - Fernet ciphertext in DB via encrypt_str
  - SSE event payloads NEVER include the plaintext value (not even error
    frames; the secret_filter.py regex on root logger is the LAST defense,
    not the first — this service is the first)

DB SESSION (Pitfall #1):
  Step 3's DB write closes the session immediately afterward. Steps 4-7 only
  hit filesystem + subprocess, so SQLite is free for other API calls during
  the rest of the SSE stream.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable
from typing import Any

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.hermes_cli import HermesCliAdapter, HermesCliError
from app.adapters.parsers import parse_gateway_pid_file
from app.adapters.profile_fs import ProfileFsAdapter
from app.auth.crypto import encrypt_str
from app.models.bot import Bot

_STEP_LABELS = [
    "校验飞书凭证",
    "创建 Hermes Profile",
    "写入加密配置",
    "检测/安装 lark-oapi",
    "配置 Gateway",
    "启动 Gateway",
    "飞书 API 联通测试",
]


class WizardStepError(Exception):
    """Raised inside :class:`WizardService` when a single step fails.

    Attributes:
        step:           1-7 step number that errored
        user_message:   Chinese text safe to render in UI
        detail:         Technical detail for the SSE ``error`` field — MUST
                        NOT contain the plaintext App Secret
        fix_hint:       Slug driving frontend "fix suggestion" text
    """

    def __init__(
        self,
        step: int,
        user_message: str,
        detail: str,
        fix_hint: str = "unknown",
    ) -> None:
        self.step = step
        self.user_message = user_message
        self.detail = detail
        self.fix_hint = fix_hint
        super().__init__(user_message)


def _sse_frame(data: dict[str, Any]) -> str:
    """Format a dict as an SSE ``data:`` frame ending in the required ``\\n\\n``."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


class WizardService:
    """Orchestrates the 7-step Feishu Bot onboarding wizard.

    Construct with :class:`HermesCliAdapter`, :class:`ProfileFsAdapter`, and
    an :class:`AsyncSession`. The :meth:`run` method is an async generator
    that yields SSE frames; pipe it into :class:`StreamingResponse`.
    """

    def __init__(
        self,
        cli: HermesCliAdapter,
        fs: ProfileFsAdapter,
        session: AsyncSession,
    ) -> None:
        self.cli = cli
        self.fs = fs
        self.session = session

    async def run(
        self,
        name: str,
        feishu_app_id: str,
        feishu_app_secret: SecretStr,
        domain: str,
        connection_mode: str,
        group_strategy: str,
    ) -> AsyncIterator[str]:
        """Yield SSE frames for each of the 7 wizard steps + final done event.

        On step error, emits the error frame and returns early — no further
        steps execute and no terminal ``done`` event is emitted (frontend can
        detect end-of-stream via SSE close).
        """
        # ----- Step 1: validate App ID + Secret format (always re-run) -----
        async for f in self._emit_step(
            1,
            self._step1_validate(feishu_app_id, feishu_app_secret),
        ):
            yield f
            if '"status": "error"' in f:
                return

        # ----- Steps 2..6: idempotent with filesystem re-derivation -----
        for step_num in range(2, 7):
            label = _STEP_LABELS[step_num - 1]
            if await self._step_completed(name, step_num):
                yield _sse_frame(
                    {
                        "step": step_num,
                        "status": "success",
                        "message": f"{label} (已完成，跳过)",  # noqa: RUF001
                        "duration_ms": 0,
                    }
                )
                continue
            async for f in self._emit_step(
                step_num,
                self._run_step(
                    name,
                    step_num,
                    feishu_app_id,
                    feishu_app_secret,
                    domain,
                    connection_mode,
                    group_strategy,
                ),
            ):
                yield f
                if '"status": "error"' in f:
                    return

        # ----- Step 7: doctor (always re-run) -----
        async for f in self._emit_step(7, self._step7_doctor(name)):
            yield f
            if '"status": "error"' in f:
                return

        # ----- Terminal done event -----
        yield _sse_frame({"step": 0, "status": "done", "message": "向导完成"})

    # ------------------------------------------------------------------
    # Frame emission helper.
    # ------------------------------------------------------------------
    async def _emit_step(self, step: int, coro: Awaitable[None]) -> AsyncIterator[str]:
        """Wrap a step coroutine in running/success/error SSE frames."""
        label = _STEP_LABELS[step - 1]
        yield _sse_frame({"step": step, "status": "running", "message": label})
        t0 = asyncio.get_event_loop().time()
        try:
            await coro
            ms = int((asyncio.get_event_loop().time() - t0) * 1000)
            yield _sse_frame(
                {"step": step, "status": "success", "message": label, "duration_ms": ms}
            )
        except WizardStepError as e:
            ms = int((asyncio.get_event_loop().time() - t0) * 1000)
            yield _sse_frame(
                {
                    "step": step,
                    "status": "error",
                    "message": e.user_message,
                    "duration_ms": ms,
                    "error": e.detail,
                    "fix_hint": e.fix_hint,
                }
            )

    # ------------------------------------------------------------------
    # Idempotency probe.
    # ------------------------------------------------------------------
    async def _step_completed(self, name: str, step: int) -> bool:
        """Re-derive step completion from filesystem state (D-09). No DB query."""
        if step == 2:  # profile create — profile dir exists?
            return await self.fs.host.path_exists(self.fs.profile_dir(name))
        if step == 3:  # write .env — required Feishu keys exist?
            return await self._feishu_env_configured(name)
        if step == 4:  # check lark-oapi
            installed, _ = await self.cli.check_lark_oapi()
            return installed
        if step == 5:  # gateway setup — Feishu gateway is env-driven in Hermes v0.8.
            return await self._feishu_env_configured(name)
        if step == 6:  # gateway start — PID file + alive process?
            pid_file = self.fs.profile_dir(name) / "gateway.pid"
            if not await self.fs.host.path_exists(pid_file):
                return False
            try:
                content = await self.fs.host.read_text(pid_file)
            except FileNotFoundError:
                return False
            # Try JSON pid file format first, then plain integer.
            parsed = parse_gateway_pid_file(content)
            pid: int | None = None
            if parsed is not None:
                pid = parsed.pid
            else:
                try:
                    pid = int(content.strip())
                except (ValueError, TypeError):
                    return False
            proc = self.fs.host.get_process_info(pid)
            return proc is not None and proc.is_alive
        return False

    async def _feishu_env_configured(self, name: str) -> bool:
        env = await self.fs.read_env(name)
        return bool(env.get("FEISHU_APP_ID") and env.get("FEISHU_APP_SECRET"))

    # ------------------------------------------------------------------
    # Step bodies.
    # ------------------------------------------------------------------
    async def _step1_validate(
        self,
        feishu_app_id: str,
        feishu_app_secret: SecretStr,
    ) -> None:
        """Phase 3 step 1 = format check only.

        Real Feishu API connectivity happens in step 7 (hermes doctor).
        """
        if not feishu_app_id or len(feishu_app_id) < 8:
            raise WizardStepError(
                1,
                "App ID 格式不正确",
                "app_id too short",
                fix_hint="feishu_auth_fail",
            )
        plain = feishu_app_secret.get_secret_value()
        if not plain or len(plain) < 8:
            raise WizardStepError(
                1,
                "App Secret 格式不正确",
                "app_secret too short",
                fix_hint="feishu_auth_fail",
            )

    async def _run_step(
        self,
        name: str,
        step: int,
        feishu_app_id: str,
        feishu_app_secret: SecretStr,
        domain: str,
        connection_mode: str,
        group_strategy: str,
    ) -> None:
        if step == 2:
            try:
                await self.cli.profile_create(name)
            except HermesCliError as e:
                raise WizardStepError(
                    2,
                    f"Profile 创建失败：{e.hint}",  # noqa: RUF001
                    e.hint,
                    fix_hint="unknown",
                ) from e
            return

        if step == 3:
            # Plaintext .env on disk (Hermes reads literal KEY=VALUE).
            env_data: dict[str, str] = {
                "FEISHU_APP_ID": feishu_app_id,
                "FEISHU_APP_SECRET": feishu_app_secret.get_secret_value(),
            }
            if domain == "lark":
                env_data["FEISHU_DOMAIN"] = "lark"
            if connection_mode == "websocket":
                env_data["FEISHU_CONNECTION_MODE"] = "websocket"
            if group_strategy != "mention":
                env_data["FEISHU_GROUP_STRATEGY"] = group_strategy
            await self.fs.write_env(name, env_data)

            # Update DB row with Fernet ciphertext + wizard config snapshot.
            secret_enc = encrypt_str(feishu_app_secret.get_secret_value())
            result = await self.session.scalars(select(Bot).where(Bot.name == name))
            bot = result.first()
            if bot is not None:
                bot.feishu_app_id = feishu_app_id
                bot.feishu_app_secret_enc = secret_enc
                bot.domain = domain
                bot.connection_mode = connection_mode
                bot.group_strategy = group_strategy
                await self.session.commit()
            # Pitfall #1: release the DB session — steps 4-7 don't need it,
            # holding it open across the whole stream blocks SQLite writers.
            await self.session.close()
            return

        if step == 4:
            installed, _ = await self.cli.check_lark_oapi()
            if not installed:
                try:
                    await self.cli.install_lark_oapi()
                except HermesCliError as e:
                    raise WizardStepError(
                        4,
                        "lark-oapi 安装失败",
                        "pip install failed",
                        fix_hint="lark_oapi_missing",
                    ) from e
            return

        if step == 5:
            if await self._feishu_env_configured(name):
                return
            raise WizardStepError(
                5,
                "Gateway 配置失败",
                "FEISHU_APP_ID or FEISHU_APP_SECRET missing from .env",
                fix_hint="gateway_setup_fail",
            )
            return

        if step == 6:
            try:
                await self.cli.gateway_start(name)
            except HermesCliError as e:
                raise WizardStepError(
                    6,
                    "Gateway 启动失败",
                    e.hint,
                    fix_hint="gateway_start_fail",
                ) from e
            return

    async def _step7_doctor(self, name: str) -> None:
        try:
            ok = await self.cli.doctor(profile=name)
        except HermesCliError as e:
            raise WizardStepError(
                7,
                "飞书 API 联通测试失败",
                e.hint,
                fix_hint="feishu_auth_fail",
            ) from e
        if not ok:
            raise WizardStepError(
                7,
                "飞书 API 联通测试失败",
                "hermes doctor returned non-zero",
                fix_hint="feishu_auth_fail",
            )
