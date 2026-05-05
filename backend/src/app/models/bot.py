"""Bot ORM model — Phase 2 minimal columns for list/create/clone/rename/delete.

Design notes:

* `feishu_app_secret_enc` stores the Fernet ciphertext of the same plaintext
  that lives in ``~/.hermes/<profile>/.env`` (mode 0600). NFR-02 requires the
  DB never holds plaintext; the dual-store pattern is intentional (Hermes
  reads literal KEY=VALUE from .env so the file MUST be plaintext, but the
  console UI / audit / recovery flows read from DB).
* `feishu_app_id` is nullable so a half-configured Bot (filesystem profile
  exists, .env not yet provided) still gets a DB row from list-merge flows.
* `tags` is a JSON array column — SQLite stores as TEXT, SQLAlchemy auto
  serializes; never larger than ~10 short strings in practice.
* `last_active_at` is set by Phase 4 message-processing flows; today it stays
  NULL until a Bot actually receives traffic. Used to sort BOT-02 list.
* `name` uniqueness is enforced via UniqueConstraint (matches BOT-08).
* `feishu_app_id` partial-unique index lives in the alembic migration (SQLite
  partial index requires raw `sqlite_where` text — see 002_add_bots.py).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Bot(Base):
    __tablename__ = "bots"
    __table_args__ = (UniqueConstraint("name", name="uq_bots_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    feishu_app_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # NFR-02: Fernet-encrypted plaintext stored in DB. Plain version goes to
    # ~/.hermes/<name>/.env (mode 600). Dual-store is intentional.
    feishu_app_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Phase 3 wizard config (FEISHU-01). Migration 003 adds these with
    # server defaults so existing Phase 2 rows backfill cleanly.
    domain: Mapped[str] = mapped_column(
        String(16), nullable=False, default="feishu", server_default="feishu"
    )
    connection_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="websocket", server_default="websocket"
    )
    group_strategy: Mapped[str] = mapped_column(
        String(16), nullable=False, default="mention", server_default="mention"
    )
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Phase 4 GATEWAY-01: Supervisor writes the most recent gateway state here so
    # status_decider can answer "is this Bot up?" without re-shelling-out to the
    # Hermes CLI on every list-bots request. Vocabulary mirrors GatewayState
    # in app.schemas.gateway: running | starting | stopped | error | unconfigured.
    gateway_state_cache: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unconfigured", server_default="unconfigured"
    )
    # Wall-clock when gateway_state_cache last transitioned. NULL until the
    # Supervisor writes the first observation.
    gateway_state_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
