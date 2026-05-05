"""Tests for cleanup_old_archives — 30-day .tar.gz cleanup background task."""

from __future__ import annotations

import os
import time
from pathlib import Path

from app.services.archive import cleanup_old_archives


async def test_cleanup_old_archives_removes_files_older_than_30_days(tmp_path: Path) -> None:
    """3 files: 10 / 20 / 40 days ago. Only the 40-day file should be removed."""
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    fresh = archive_dir / "fresh-20260420-100000.tar.gz"
    mid = archive_dir / "mid-20260410-100000.tar.gz"
    stale = archive_dir / "stale-20260320-100000.tar.gz"
    for p in (fresh, mid, stale):
        p.write_bytes(b"")

    now = time.time()
    os.utime(fresh, (now - 10 * 86400, now - 10 * 86400))
    os.utime(mid, (now - 20 * 86400, now - 20 * 86400))
    os.utime(stale, (now - 40 * 86400, now - 40 * 86400))

    removed = await cleanup_old_archives(archive_dir, max_age_days=30)
    assert removed == 1
    assert fresh.exists()
    assert mid.exists()
    assert not stale.exists()


async def test_cleanup_handles_missing_dir_gracefully(tmp_path: Path) -> None:
    """Non-existent dir → no error, returns 0."""
    missing = tmp_path / "does-not-exist"
    removed = await cleanup_old_archives(missing, max_age_days=30)
    assert removed == 0


async def test_cleanup_only_removes_tar_gz_files(tmp_path: Path) -> None:
    """A .txt file 40 days old must be kept — only *.tar.gz are eligible."""
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    txt = archive_dir / "readme.txt"
    txt.write_text("notes")
    targz = archive_dir / "old-20260320-100000.tar.gz"
    targz.write_bytes(b"")
    now = time.time()
    os.utime(txt, (now - 40 * 86400, now - 40 * 86400))
    os.utime(targz, (now - 40 * 86400, now - 40 * 86400))

    removed = await cleanup_old_archives(archive_dir, max_age_days=30)
    assert removed == 1
    assert txt.exists()
    assert not targz.exists()
