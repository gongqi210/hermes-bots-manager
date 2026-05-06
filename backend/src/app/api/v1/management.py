"""Phase 5 management router — model config, workspace, skills, health.

Mounted under ``/api/v1/bots/{bot_name}/...`` so it MUST register before the
catch-all ``bots`` router. RBAC: ``GET`` requires Viewer+, ``PUT`` requires
Editor+.

These endpoints are deliberately small. They read ``config.yaml`` via PyYAML,
mutate the slice the operator owns, and write back preserving every other
key (defense against future Hermes config additions).
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path
from typing import Any, Literal

import anyio
import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.hermes_cli import HermesCliAdapter
from app.adapters.hostops import HostOps
from app.adapters.profile_fs import ProfileFsAdapter
from app.auth.rbac import Role, require_role
from app.db.session import get_session
from app.models.bot import Bot
from app.models.workspace_library import WorkspaceLibrary
from app.schemas.management import (
    ChatgptAuthStartOut,
    HealthOut,
    ModelConfigOut,
    ModelConfigUpdateIn,
    SkillItem,
    SkillsOut,
    SkillsUpdateIn,
    WorkspaceLibraryCreateIn,
    WorkspaceLibraryItem,
    WorkspaceOut,
    WorkspaceReuseOption,
    WorkspaceUpdateIn,
)
from app.services.management import (
    check_missing_deps,
    discover_skills,
    extract_disabled,
    extract_model_block,
    extract_workspace_cwd,
    is_chatgpt_auth,
    list_model_provider_options,
    merge_disabled,
    merge_model_block,
    merge_workspace_cwd,
    probe_workspace,
    read_config_yaml,
    reset_active_gateway_sessions,
    selected_provider_transport,
    start_codex_auth_session,
    sync_skills_fs,
    sync_workspace_env,
    validate_workspace_path,
    write_config_yaml,
)
from app.services.provider_auth import (
    provider_authorized,
    reuse_provider_auth,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["management"])


def _global_skills_dir(fs: ProfileFsAdapter) -> Path:
    """Hermes-default global skills directory for the configured home."""
    return fs.hermes_home / "skills"


async def _ensure_known_profile(
    bot_name: str,
    *,
    session: AsyncSession,
    fs: ProfileFsAdapter,
) -> None:
    bot = (await session.execute(select(Bot).where(Bot.name == bot_name))).scalar_one_or_none()
    if bot is not None:
        return
    if bot_name in await fs.list_profiles():
        return
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bot not found")


def _state_deps(request: Request) -> tuple[ProfileFsAdapter, HostOps, HermesCliAdapter]:
    fs: ProfileFsAdapter = request.app.state.fs
    host: HostOps = request.app.state.host
    cli: HermesCliAdapter = request.app.state.cli
    return fs, host, cli


# ---------------------------------------------------------------------------
# Model config
# ---------------------------------------------------------------------------


@router.get(
    "/bots/{bot_name}/model-config",
    response_model=ModelConfigOut,
    dependencies=[Depends(require_role(Role.VIEWER))],
)
async def get_model_config(
    bot_name: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ModelConfigOut:
    fs, _, _ = _state_deps(request)
    await _ensure_known_profile(bot_name, session=session, fs=fs)
    doc = await read_config_yaml(fs, bot_name)
    block = extract_model_block(doc)
    providers = await list_model_provider_options(fs, bot_name, doc)
    return ModelConfigOut(
        bot_name=bot_name,
        provider=block["provider"],
        model=block["model"],
        base_url=block["base_url"],
        api_mode=block["api_mode"],
        is_chatgpt_auth=is_chatgpt_auth(block["provider"], block["api_mode"], block["base_url"]),
        provider_authorized=await provider_authorized(fs, bot_name, block["provider"]),
        providers=providers,
    )


@router.put(
    "/bots/{bot_name}/model-config",
    response_model=ModelConfigOut,
    dependencies=[Depends(require_role(Role.EDITOR))],
)
async def put_model_config(
    bot_name: str,
    body: ModelConfigUpdateIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ModelConfigOut:
    fs, _, _ = _state_deps(request)
    await _ensure_known_profile(bot_name, session=session, fs=fs)
    doc = await read_config_yaml(fs, bot_name)
    providers = await list_model_provider_options(fs, bot_name, doc, preferred_provider=body.provider)
    base_url, api_mode = selected_provider_transport(
        providers,
        body.provider,
        body.base_url,
        body.api_mode,
    )
    new_doc = merge_model_block(
        doc,
        provider=body.provider,
        model=body.model,
        base_url=base_url,
        api_mode=api_mode,
    )
    await write_config_yaml(fs, bot_name, new_doc)

    block = extract_model_block(new_doc)
    if is_chatgpt_auth(block["provider"], block["api_mode"], block["base_url"]):
        await reuse_provider_auth(fs, bot_name)
    providers = await list_model_provider_options(fs, bot_name, new_doc)
    return ModelConfigOut(
        bot_name=bot_name,
        provider=block["provider"],
        model=block["model"],
        base_url=block["base_url"],
        api_mode=block["api_mode"],
        is_chatgpt_auth=is_chatgpt_auth(block["provider"], block["api_mode"], block["base_url"]),
        provider_authorized=await provider_authorized(fs, bot_name, block["provider"]),
        providers=providers,
    )


@router.post(
    "/bots/{bot_name}/model-config/chatgpt-auth/start",
    response_model=ChatgptAuthStartOut,
    dependencies=[Depends(require_role(Role.EDITOR))],
)
async def start_chatgpt_auth(
    bot_name: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ChatgptAuthStartOut:
    fs, _, _ = _state_deps(request)
    await _ensure_known_profile(bot_name, session=session, fs=fs)
    launch = await anyio.to_thread.run_sync(start_codex_auth_session)
    return ChatgptAuthStartOut(
        authorization_url=launch["authorization_url"],
        process_id=launch["process_id"],
        message="已打开 Codex auth 授权页, 请在浏览器中完成 ChatGPT 授权",
    )


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


def _workspace_view(bot_name: str, cwd: str | None) -> WorkspaceOut:
    probe = probe_workspace(cwd)
    return WorkspaceOut(
        bot_name=bot_name,
        cwd=cwd,
        exists=probe["exists"],
        is_directory=probe["is_directory"],
        readable=probe["readable"],
        writable=probe["writable"],
        status=probe["status"],
        message=probe["message"],
    )


@router.get(
    "/bots/{bot_name}/workspace",
    response_model=WorkspaceOut,
    dependencies=[Depends(require_role(Role.VIEWER))],
)
async def get_workspace(
    bot_name: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> WorkspaceOut:
    fs, _, _ = _state_deps(request)
    await _ensure_known_profile(bot_name, session=session, fs=fs)
    doc = await read_config_yaml(fs, bot_name)
    cwd = extract_workspace_cwd(doc)
    return _workspace_view(bot_name, cwd)


@router.put(
    "/bots/{bot_name}/workspace",
    response_model=WorkspaceOut,
    dependencies=[Depends(require_role(Role.EDITOR))],
)
async def put_workspace(
    bot_name: str,
    body: WorkspaceUpdateIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> WorkspaceOut:
    fs, _, _ = _state_deps(request)
    await _ensure_known_profile(bot_name, session=session, fs=fs)
    cwd = body.cwd.strip() if isinstance(body.cwd, str) else None
    if cwd and not cwd.startswith("/"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Workspace 路径必须是绝对路径 (以 / 开头)",
        )
    if cwd:
        validate_workspace_path(cwd, fs.hermes_home)
    try:
        await fs.snapshot_profile(bot_name)
    except Exception:
        logger.warning(
            "snapshot_profile failed for %s (workspace save proceeds)", bot_name, exc_info=True
        )
    doc = await read_config_yaml(fs, bot_name)
    new_doc = merge_workspace_cwd(doc, cwd)
    await write_config_yaml(fs, bot_name, new_doc)
    await sync_workspace_env(fs, bot_name, cwd)
    await reset_active_gateway_sessions(fs, bot_name)
    return _workspace_view(bot_name, cwd)


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


async def _skills_view(
    bot_name: str,
    *,
    fs: ProfileFsAdapter,
    host: HostOps,
) -> SkillsOut:
    doc = await read_config_yaml(fs, bot_name)
    disabled = extract_disabled(doc)
    items_raw = await discover_skills(fs, host, bot_name, global_skills_dir=_global_skills_dir(fs))
    disabled_set = set(disabled)
    items = []
    for item in items_raw:
        # discover_skills already sets enabled based on .disabled/ scan;
        # we also honour the config.yaml disabled list for global skills
        enabled = item.get("enabled", True) and item["name"] not in disabled_set
        skill = SkillItem(
            name=item["name"],
            category=item.get("category"),
            description=item.get("description"),
            source=item["source"],
            enabled=enabled,
            dangerous=bool(item.get("dangerous", False)),
            shadowed_source=item.get("shadowed_source"),
            requires_tools=item.get("requires_tools") or [],
        )
        skill.missing_deps = check_missing_deps(skill)
        items.append(skill)
    return SkillsOut(bot_name=bot_name, skills=items, disabled=disabled)


@router.get(
    "/bots/{bot_name}/skills",
    response_model=SkillsOut,
    dependencies=[Depends(require_role(Role.VIEWER))],
)
async def get_skills(
    bot_name: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> SkillsOut:
    fs, host, _ = _state_deps(request)
    await _ensure_known_profile(bot_name, session=session, fs=fs)
    return await _skills_view(bot_name, fs=fs, host=host)


@router.put(
    "/bots/{bot_name}/skills",
    response_model=SkillsOut,
    dependencies=[Depends(require_role(Role.EDITOR))],
)
async def put_skills(
    bot_name: str,
    body: SkillsUpdateIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> SkillsOut:
    fs, host, _ = _state_deps(request)
    await _ensure_known_profile(bot_name, session=session, fs=fs)
    doc = await read_config_yaml(fs, bot_name)
    previous_disabled = set(extract_disabled(doc))
    new_disabled = {
        item.strip() for item in body.disabled if isinstance(item, str) and item.strip()
    }

    # Compute the set of skills being enabled (previously disabled, now enabled).
    being_enabled = previous_disabled - new_disabled
    if being_enabled:
        items_raw = await discover_skills(
            fs, host, bot_name, global_skills_dir=_global_skills_dir(fs)
        )
        dangerous_names = {item["name"] for item in items_raw if item.get("dangerous")}
        risky_enables = being_enabled & dangerous_names
        if risky_enables and body.confirm_name != bot_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "启用以下高风险 Skill 需要二次确认 (confirm_name 必须等于 Bot 名):"
                    f" {', '.join(sorted(risky_enables))}"
                ),
            )

    new_doc = merge_disabled(doc, sorted(new_disabled))
    try:
        await fs.snapshot_profile(bot_name)
    except Exception:
        logger.warning(
            "snapshot_profile failed for %s (skills save proceeds)", bot_name, exc_info=True
        )
    await write_config_yaml(fs, bot_name, new_doc)

    # Filesystem diff-sync: move disabled skills to .disabled/, re-enable others.
    # We discover all known skill names from the profile dir + .disabled/ and derive
    # enabled_set = all_known - new_disabled.
    profile_skills_dir = fs.profile_dir(bot_name) / "skills"
    try:
        all_skill_names: set[str] = set()
        if profile_skills_dir.exists():
            for entry in profile_skills_dir.iterdir():
                if not entry.name.startswith(".") and entry.is_dir():
                    all_skill_names.add(entry.name)
        disabled_subdir = profile_skills_dir / ".disabled"
        if disabled_subdir.exists():
            for entry in disabled_subdir.iterdir():
                if entry.is_dir():
                    all_skill_names.add(entry.name)
        enabled_set = all_skill_names - new_disabled
        await sync_skills_fs(profile_skills_dir, enabled_set)
    except Exception:
        logger.warning(
            "sync_skills_fs failed for %s (skills config already saved)", bot_name, exc_info=True
        )

    return await _skills_view(bot_name, fs=fs, host=host)


# ---------------------------------------------------------------------------
# Skills upload (SKILLS-07)
# ---------------------------------------------------------------------------

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


@router.post(
    "/bots/{bot_name}/skills/upload",
    response_model=SkillItem,
    dependencies=[Depends(require_role(Role.OWNER))],
)
async def upload_skill(
    bot_name: str,
    file: UploadFile,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> SkillItem:
    """Upload a skill zip package. Owner-only. Zip-slip protected."""
    fs, _, _ = _state_deps(request)
    await _ensure_known_profile(bot_name, session=session, fs=fs)

    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="zip 文件超过 10 MB 大小限制",
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="文件不是有效的 zip 格式",
        ) from exc

    # Derive extraction directory from upload filename stem
    stem = Path(file.filename or "upload").stem
    profile_skills_dir = fs.profile_dir(bot_name) / "skills"
    extract_dir = profile_skills_dir / stem
    extract_dir_resolved = extract_dir.resolve()

    # Zip-slip check: every member must resolve inside extract_dir
    for member in zf.namelist():
        target = (extract_dir / member).resolve()
        try:
            target.relative_to(extract_dir_resolved)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="zip 路径不安全，拒绝解压",  # noqa: RUF001
            ) from exc

    # Require skill.yaml (or SKILL.md) in the zip
    names_lower = [n.lower() for n in zf.namelist()]
    if not any("skill.yaml" in n or "skill.md" in n for n in names_lower):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="zip 中缺少 skill.yaml，不是有效的技能包",  # noqa: RUF001
        )

    # Snapshot before extraction
    try:
        await fs.snapshot_profile(bot_name)
    except Exception:
        logger.warning("snapshot_profile failed for %s (upload proceeds)", bot_name, exc_info=True)

    # Extract (blocking I/O — run in thread)
    def _extract() -> None:
        extract_dir.mkdir(parents=True, exist_ok=True)
        zf.extractall(extract_dir)

    await anyio.to_thread.run_sync(_extract)

    return SkillItem(name=stem, source="uploaded", enabled=True)


# ---------------------------------------------------------------------------
# Health summary
# ---------------------------------------------------------------------------


@router.get(
    "/bots/{bot_name}/health",
    response_model=HealthOut,
    dependencies=[Depends(require_role(Role.VIEWER))],
)
async def get_health(
    bot_name: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HealthOut:
    fs, host, cli = _state_deps(request)
    await _ensure_known_profile(bot_name, session=session, fs=fs)

    # Lazy import to mirror gateway router's pattern (avoids circular import +
    # keeps BotService construction co-located with the only place using it).
    from app.config import get_settings
    from app.services.bot import BotService

    settings = get_settings()
    service = BotService(
        session=session,
        cli=cli,
        fs=fs,
        archive_dir=settings.archive_dir,
    )
    gw = await service.compute_gateway_status(bot_name, host=host, fs=fs, cli=cli)

    doc = await read_config_yaml(fs, bot_name)
    model_block = extract_model_block(doc)
    model_configured = bool(model_block["provider"] and model_block["model"])
    provider_auth_status = (
        await provider_authorized(fs, bot_name, model_block["provider"]) if model_configured else False
    )

    cwd = extract_workspace_cwd(doc)
    ws = probe_workspace(cwd)
    workspace_status: Any = ws["status"]

    skills_view = await _skills_view(bot_name, fs=fs, host=host)
    skills_total = len(skills_view.skills)
    skills_enabled = sum(1 for s in skills_view.skills if s.enabled)
    dangerous_skill_count = sum(1 for s in skills_view.skills if s.dangerous and s.enabled)
    shadowed_skill_count = sum(1 for s in skills_view.skills if s.shadowed_source)

    # Allowlist preset detection (Phase 5 plan 05-05): empty list = open testing,
    # exact match of resolved Owner/Admin Feishu IDs = owner_admin, else custom.
    try:
        current_allow = await fs.read_allowed_users(bot_name)
    except Exception:
        current_allow = []
    if not current_allow:
        allowlist_preset: Literal["open", "owner_admin", "custom"] = "open"
    else:
        from app.models.user import User as UserModel

        owner_admin_ids = [
            row.feishu_user_id
            for row in (
                await session.execute(
                    select(UserModel).where(
                        UserModel.role.in_(["Owner", "Admin"]),
                        UserModel.feishu_user_id.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
            if row.feishu_user_id
        ]
        if owner_admin_ids and set(owner_admin_ids) == set(current_allow):
            allowlist_preset = "owner_admin"
        else:
            allowlist_preset = "custom"

    overall: Literal["ok", "warning", "error"] = "ok"
    if gw.state in ("error",) or workspace_status == "error":
        overall = "error"
    elif (
        gw.state in ("stopped", "unconfigured", "starting")
        or not model_configured
        or (model_configured and not provider_auth_status)
        or workspace_status == "warning"
    ):
        overall = "warning"

    return HealthOut(
        bot_name=bot_name,
        gateway_state=gw.state,
        gateway_why=gw.why,
        model_configured=model_configured,
        provider_authorized=provider_auth_status,
        workspace_status=workspace_status,
        skills_enabled=skills_enabled,
        skills_total=skills_total,
        dangerous_skill_count=dangerous_skill_count,
        shadowed_skill_count=shadowed_skill_count,
        allowlist_preset=allowlist_preset,
        overall=overall,
    )


# ---------------------------------------------------------------------------
# Workspace library (WORKSPACE-02 Mode B)
# ---------------------------------------------------------------------------

workspace_library_router = APIRouter(tags=["workspace-library"])


@workspace_library_router.get(
    "/workspace-library",
    response_model=list[WorkspaceLibraryItem],
    dependencies=[Depends(require_role(Role.VIEWER))],
)
async def list_workspace_library(
    session: AsyncSession = Depends(get_session),
) -> list[WorkspaceLibraryItem]:
    rows = (await session.execute(select(WorkspaceLibrary))).scalars().all()
    return [WorkspaceLibraryItem.model_validate(row) for row in rows]


@workspace_library_router.post(
    "/workspace-library",
    response_model=WorkspaceLibraryItem,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def create_workspace_library(
    body: WorkspaceLibraryCreateIn,
    session: AsyncSession = Depends(get_session),
) -> WorkspaceLibraryItem:
    row = WorkspaceLibrary(path=body.path, label=body.label)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return WorkspaceLibraryItem.model_validate(row)


@workspace_library_router.delete(
    "/workspace-library/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def delete_workspace_library(
    item_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    row = await session.get(WorkspaceLibrary, item_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="workspace library entry not found"
        )
    await session.delete(row)
    await session.commit()


# ---------------------------------------------------------------------------
# Workspace reuse options (WORKSPACE-02 Mode C)
# ---------------------------------------------------------------------------


@router.get(
    "/bots/{bot_name}/workspace-options/reuse",
    response_model=list[WorkspaceReuseOption],
    dependencies=[Depends(require_role(Role.VIEWER))],
)
async def get_workspace_reuse_options(
    bot_name: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> list[WorkspaceReuseOption]:
    """Return list of {bot_name, cwd} for all bots with terminal.cwd set, excluding the caller."""
    fs, _, _ = _state_deps(request)
    await _ensure_known_profile(bot_name, session=session, fs=fs)

    # Get all bot names from the Bot table
    all_bots = (await session.execute(select(Bot))).scalars().all()
    options: list[WorkspaceReuseOption] = []
    for bot in all_bots:
        if bot.name == bot_name:
            continue
        doc = await read_config_yaml(fs, bot.name)
        cwd = extract_workspace_cwd(doc)
        if cwd:
            options.append(WorkspaceReuseOption(bot_name=bot.name, cwd=cwd))
    return options
