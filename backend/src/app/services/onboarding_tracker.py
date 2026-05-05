"""OnboardingTracker — Phase 4 D-19 / 3-min KPI 埋点.

A static-method facade around :class:`OnboardingRun` writes. Each REST handler
that crosses a funnel boundary calls one of the five hooks; the tracker keeps
the run-status state machine consistent so the (Phase 6) dashboard can
compute the 3-minute @-able KPI by reading the table directly.

Funnel
------

::

    started_at <= login_at <= wizard_done_at <= gateway_running_at
                            <= first_pairing_approved_at <= first_message_at

``total_duration_ms`` is set ONLY by :meth:`hook_first_message` — it is the
authoritative end-to-end duration and never auto-derived (per CONTEXT D-19,
SUMMARY 04-02).

Status transitions
------------------

* :meth:`hook_login` creates a new ``in_progress`` run iff the user has none.
* :meth:`hook_first_message` flips the run to ``success`` and stamps
  ``total_duration_ms``.
* ``failed`` / ``expired`` transitions are owned by external callers (REST
  handlers / TTL job) — not by this module.

Idempotency
-----------

Each hook short-circuits to a noop when re-invoked on a run already past the
relevant funnel step (D-19: "first" really means first). This protects against
double-fire from retry / re-render loops in the wizard.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.onboarding_run import OnboardingRun, OnboardingStatus


class OnboardingTracker:
    """Static facade for OnboardingRun writes — see module docstring."""

    @staticmethod
    async def hook_login(session: AsyncSession, user_id: int) -> int:
        """Stamp ``login_at``. Creates a new in-progress run if none exists.

        Returns the run id so the caller can pass it to later hooks (or stash
        in a session cookie for cross-request continuity).
        """
        existing = await OnboardingTracker._get_in_progress(session, user_id)
        now = datetime.now(UTC)
        if existing is None:
            row = OnboardingRun(
                user_id=user_id,
                started_at=now,
                login_at=now,
                last_step="login",
            )
            session.add(row)
            await session.flush()
            return row.id
        existing.login_at = now
        existing.last_step = "login"
        return existing.id

    @staticmethod
    async def hook_wizard_done(session: AsyncSession, user_id: int, bot_id: int) -> None:
        """Stamp ``wizard_done_at`` + bind the run to the freshly-created Bot."""
        run = await OnboardingTracker._get_in_progress(session, user_id)
        if run is None:
            return
        run.wizard_done_at = datetime.now(UTC)
        run.bot_id = bot_id
        run.last_step = "wizard_done"

    @staticmethod
    async def hook_gateway_running(session: AsyncSession, user_id: int) -> None:
        """Stamp ``gateway_running_at`` — Supervisor / REST start handler hook."""
        run = await OnboardingTracker._get_in_progress(session, user_id)
        if run is None:
            return
        run.gateway_running_at = datetime.now(UTC)
        run.last_step = "gateway_running"

    @staticmethod
    async def hook_first_pairing_approved(session: AsyncSession, user_id: int) -> None:
        """Stamp ``first_pairing_approved_at`` (idempotent — first-fire wins)."""
        run = await OnboardingTracker._get_in_progress(session, user_id)
        if run is None or run.first_pairing_approved_at is not None:
            return
        run.first_pairing_approved_at = datetime.now(UTC)
        run.last_step = "first_pairing_approved"

    @staticmethod
    async def hook_first_message(
        session: AsyncSession, user_id: int, run_id: int | None = None
    ) -> None:
        """Stamp ``first_message_at`` + ``total_duration_ms`` + flip to success.

        D-19 fallback: in MVP the user clicks "我收到了第一条 @ 回复" because we
        cannot reliably detect the inbound first message from the gateway log
        side-channel. ``run_id`` lets the caller target a specific run rather
        than the current in-progress one — useful when the run terminated and
        we want to amend it.
        """
        if run_id is None:
            run = await OnboardingTracker._get_in_progress(session, user_id)
        else:
            run = (
                await session.execute(select(OnboardingRun).where(OnboardingRun.id == run_id))
            ).scalar_one_or_none()
        if run is None:
            return
        now = datetime.now(UTC)
        run.first_message_at = now
        # started_at may be naive (SQLite returns naive UTC); coerce both
        # sides to naive for the subtraction so we never hit the mixed-tz
        # TypeError.
        started = run.started_at.replace(tzinfo=None) if run.started_at.tzinfo else run.started_at
        now_naive = now.replace(tzinfo=None)
        run.total_duration_ms = int((now_naive - started).total_seconds() * 1000)
        run.status = OnboardingStatus.SUCCESS.value
        run.last_step = "first_message"

    @staticmethod
    async def _get_in_progress(session: AsyncSession, user_id: int) -> OnboardingRun | None:
        """Return the user's most-recent in_progress run, or None."""
        return (
            await session.execute(
                select(OnboardingRun)
                .where(
                    OnboardingRun.user_id == user_id,
                    OnboardingRun.status == OnboardingStatus.IN_PROGRESS.value,
                )
                .order_by(OnboardingRun.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
