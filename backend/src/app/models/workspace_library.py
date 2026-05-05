"""WorkspaceLibrary ORM model — Phase 5 workspace library (WORKSPACE-02 Mode B)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkspaceLibrary(Base):
    __tablename__ = "workspace_library"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    registered_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
