"""Background task: clean ``.tar.gz`` archives older than ``max_age_days``.

The archive directory holds tarballs created by ``BotService.delete_bot`` —
the format is ``<bot-name>-<YYYYMMDD-HHMMSS>.tar.gz``. By default we keep 30
days of history (PRD soft commitment); operators can override via
``HERMES_CONSOLE_ARCHIVE_DIR`` + a Phase 6 settings page.

This is a one-shot scan run on app startup (NFR-04 single-worker constraint
makes it safe to do without locking). A real cron-style scheduler (APScheduler)
is M2 work. Until then, restarting the app once a day is enough.

Only files matching ``*.tar.gz`` are touched — if an operator has dropped a
``readme.txt`` into the archive dir, it stays.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


async def cleanup_old_archives(archive_dir: Path, *, max_age_days: int = 30) -> int:
    """Remove ``*.tar.gz`` files in ``archive_dir`` whose mtime is > ``max_age_days``.

    Returns the count of files removed (0 when dir is missing or empty).

    Failures on individual files are logged but do not abort the scan — one
    permission-denied file shouldn't block cleanup of the others.
    """
    if not archive_dir.exists():
        return 0
    cutoff = time.time() - (max_age_days * 86400)
    removed = 0
    for entry in archive_dir.iterdir():
        if not entry.is_file():
            continue
        # Match foo.tar.gz — both suffixes must be present.
        if entry.suffix != ".gz" or not entry.name.endswith(".tar.gz"):
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
                logger.info("archive cleanup removed %s", entry)
        except OSError as exc:
            logger.warning("archive cleanup skipped %s: %s", entry, exc)
    return removed
