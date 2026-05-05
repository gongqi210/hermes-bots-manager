"""Pydantic schemas for the Phase 4 Pairing REST API.

Wire-format invariant (NFR-02 + CONTEXT D-12): plaintext pairing codes never
leave the backend. ``PairingOut`` exposes only ``code_last4`` and the
opaque ``id``; the on-disk column for the temporary plaintext (used by the
Hermes CLI approve call) is internal-only and intentionally not surfaced
here. A grep on this directory for that field name MUST return zero hits.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class PairingOut(BaseModel):
    """Wire format for a single pairing record.

    Populated from a :class:`app.models.pairing.Pairing` row plus a join on
    ``bots.name``. ``seconds_to_expiry`` is computed by the router so the
    client can display a live countdown without re-deriving the value.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    bot_id: int
    bot_name: str | None = None  # populated via join in the router
    platform: str
    code_last4: str
    feishu_user_id: str | None
    status: Literal["pending", "approved", "rejected", "expired"]
    intercepted_at: datetime
    expires_at: datetime
    processed_at: datetime | None
    # Computed at the router boundary; client uses for the countdown chip in
    # the pending-pairings drawer.
    seconds_to_expiry: int | None = None


class PairingListItem(PairingOut):
    """Alias for list endpoints — identical shape today, kept distinct so a
    future hand-off may add per-row aggregates without breaking detail clients.
    """


class PairingActionResponse(BaseModel):
    """Response body for ``POST /pairings/{id}/approve|reject``."""

    id: int
    status: Literal["approved", "rejected"]
    message: str  # 中文提示, 见 D-17 / D-18 vocabulary
