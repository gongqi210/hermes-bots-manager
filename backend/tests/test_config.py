from __future__ import annotations

from pathlib import Path


def test_path_settings_expand_user(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_CONSOLE_HERMES_HOME", "~/.hermes")
    monkeypatch.setenv("HERMES_CONSOLE_ARCHIVE_DIR", "~/.hermes-console/archives")
    monkeypatch.setenv("HERMES_CONSOLE_MASTER_KEY_PATH", "~/.hermes-console/master.key")

    import app.config as c

    c._settings = None
    settings = c.get_settings()

    assert settings.hermes_home == Path.home() / ".hermes"
    assert settings.archive_dir == Path.home() / ".hermes-console" / "archives"
    assert settings.master_key_path == Path.home() / ".hermes-console" / "master.key"
