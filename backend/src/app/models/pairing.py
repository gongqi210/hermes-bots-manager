"""Pairing ORM model — Phase 4 GATEWAY-10/11/12.

Storage discipline (CONTEXT D-04, D-12):

* While ``status='pending'`` we MAY temporarily store ``code_plaintext`` so the
  REST approve endpoint can shell out to ``hermes pairing approve feishu <code>``
  without forcing the operator to retype the code from the UI.
* On any transition out of pending (approved | rejected | expired) the writer
  MUST clear ``code_plaintext`` to NULL — only ``code_hash`` (sha256 hex) and
  ``code_last4`` survive for audit / list display.
* ``code_plaintext`` MUST NEVER be exposed on any wire format (NFR-02). The
  Pydantic ``PairingOut`` schema in ``app.schemas.pairing`` does not include
  the field; a grep test in 04-02 enforces this invariant.

Status state machine (D-17):

    pending --(operator approves)--> approved
    pending --(operator rejects)--> rejected
    pending --(TTL expires)--> expired

The ``CheckConstraint`` on ``status`` is the database-level invariant; the
``PairingStatus`` ``StrEnum`` mirrors the same vocabulary in Python so callers
get type-checking + value safety.

Relationships: ``bot_id`` cascades on bot deletion (an orphan pairing is
useless); ``processed_by_user_id`` is SET NULL via ORM-level ``ondelete``
behaviour (ForeignKey only — no cascade) because audit trail should outlive
the operator account.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PairingStatus(StrEnum):
    """State machine vocabulary for ``Pairing.status``."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


_PAIRING_STATUS_VALUES = tuple(s.value for s in PairingStatus)


class Pairing(Base):
    """A pairing-code request intercepted from the gateway log.

    GATEWAY-10: a row is created automatically by the log-tail worker when a
    pairing line is detected. GATEWAY-11/12 govern the TTL + hash storage.
    """

    __tablename__ = "pairings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired')",
            name="ck_pairings_status",
        ),
        Index("ix_pairings_bot_id", "bot_id"),
        Index("ix_pairings_status_expires", "status", "expires_at"),
        # MAJOR 8: partial unique on (bot_id, code_hash) WHERE status='pending'.
        # SELECT-before-INSERT in pairing_writer is the friendly-skip path; this
        # index is the correctness backstop on theoretical race (Supervisor is
        # single-task per bot, so collisions should not occur in practice).
        Index(
            "ix_pairings_dedupe_pending",
            "bot_id",
            "code_hash",
            unique=True,
            sqlite_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("bots.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(
        String(16), nullable=False, default="feishu", server_default="feishu"
    )
    # NULL once status leaves 'pending'; never on the wire.
    code_plaintext: Mapped[str | None] = mapped_column(String(64), nullable=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    feishu_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=PairingStatus.PENDING.value,
        server_default=PairingStatus.PENDING.value,
    )
    intercepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
