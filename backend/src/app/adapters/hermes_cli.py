"""HermesCliAdapter — single entry point for ``hermes`` invocations (NFR-05).

Every public method:
  1. Builds an args list (NEVER constructs flags from user input directly).
  2. Delegates to :meth:`HostOps.run_hermes` (the only seam allowed to spawn
     subprocesses; the arg whitelist in ``LocalHostOps`` is the second line of
     defense against bot-name flag injection — Pitfall #14).
  3. Classifies failures via :func:`parsers.classify_create_error` or a small
     in-line string match against well-known Hermes v0.8 error phrases.
  4. Returns a typed dataclass on success; raises :class:`HermesCliError` with
     a typed ``hint`` on failure so the API layer can translate without
     re-parsing stdout.

Hint vocabulary (extend deliberately):

* ``"duplicate"``         — profile name already exists
* ``"invalid_name"``      — name failed Hermes' regex validation
* ``"reserved_name"``     — attempted to create the built-in ``default`` profile
* ``"not_found"``         — operation targets a non-existent profile
* ``"timeout"``           — ``HostOps`` returned the ``-9`` timeout sentinel
* ``"gateway_setup_fail"``— ``hermes -p <p> gateway setup`` exited non-zero (Phase 3)
* ``"gateway_start_fail"``— ``hermes -p <p> gateway start`` exited non-zero (Phase 3)
* ``"gateway_stop_fail"`` — ``hermes -p <p> gateway stop`` exited non-zero (Phase 4)
* ``"gateway_restart_fail"`` — ``hermes -p <p> gateway restart`` exited non-zero (Phase 4)
* ``"pairing_expired"``   — ``pairing approve`` reported "not found or expired" (Phase 4 / FINDING-05)
* ``"pairing_approve_fail"`` — generic ``pairing approve`` failure (Phase 4)
* ``"lark_oapi_missing"`` — ``pip install lark-oapi`` failed (Phase 3)
* ``"unknown"``           — anything else (still surfaced; just unclassified)
"""

from __future__ import annotations

from pathlib import Path

from app.adapters.hostops import HostOps
from app.adapters.parsers import (
    PairingListOutput,
    ProfileShow,
    ProfileSummary,
    classify_create_error,
    parse_pairing_list,
    parse_profile_list,
    parse_profile_show,
)

__all__ = ["HermesCliAdapter", "HermesCliError"]

# Cap stderr/stdout snippet in the exception message — log files keep the full
# output, but the human-facing message stays bounded.
_MSG_SNIPPET_LIMIT = 200
_HERMES_VENV_PYTHON = Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "python"
_LARK_OAPI_IMPORT_CHECK = (
    "import importlib.metadata as m; import lark_oapi; print(m.version('lark-oapi'))"
)


class HermesCliError(Exception):
    """Hermes CLI failure carrying a typed ``hint`` for upstream translation.

    See module docstring for the hint vocabulary. Original ``stdout`` /
    ``stderr`` are preserved on the exception so callers (audit log,
    diagnostic page) can present the raw output if needed.
    """

    def __init__(
        self,
        returncode: int,
        stdout: str,
        stderr: str,
        hint: str = "unknown",
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.hint = hint
        snippet = (stderr or stdout).strip()[:_MSG_SNIPPET_LIMIT] or "(empty)"
        super().__init__(f"hermes exited {returncode} ({hint}): {snippet}")


def _check_timeout(returncode: int, stdout: str, stderr: str) -> None:
    """Raise ``HermesCliError(hint='timeout')`` if HostOps returned the -9 sentinel.

    LocalHostOps.run_hermes returns ``CliResult(returncode=-9, stderr="timeout")``
    when ``asyncio.wait_for`` fires; treat that as a hard error with a
    user-friendly hint rather than letting it look like a normal subprocess
    failure.
    """
    if returncode == -9 and "timeout" in stderr.lower():
        raise HermesCliError(returncode=-9, stdout=stdout, stderr=stderr, hint="timeout")


def _classify_not_found(stdout: str) -> str:
    """Return ``'not_found'`` if Hermes' "doesn't exist" wording is present."""
    if "not found" in stdout or "does not exist" in stdout:
        return "not_found"
    return "unknown"


class HermesCliAdapter:
    """The single entry point for any ``hermes`` invocation. NFR-05.

    Construct with a ``HostOps`` impl (``LocalHostOps`` in production,
    ``InMemoryHostOps`` in tests). Never spawns a subprocess directly.
    """

    def __init__(self, host: HostOps) -> None:
        self.host = host

    # ---- profile read ops ----------------------------------------------------

    async def profile_list(self) -> list[ProfileSummary]:
        r = await self.host.run_hermes(["profile", "list"], timeout_sec=10)
        _check_timeout(r.returncode, r.stdout, r.stderr)
        if r.returncode != 0:
            raise HermesCliError(r.returncode, r.stdout, r.stderr)
        return parse_profile_list(r.stdout)

    async def profile_show(self, name: str) -> ProfileShow:
        r = await self.host.run_hermes(["profile", "show", name], timeout_sec=10)
        _check_timeout(r.returncode, r.stdout, r.stderr)
        if r.returncode != 0:
            raise HermesCliError(
                r.returncode, r.stdout, r.stderr, hint=_classify_not_found(r.stdout)
            )
        return parse_profile_show(r.stdout)

    # ---- profile mutation ops ------------------------------------------------

    async def profile_create(self, name: str, *, clone_from: str | None = None) -> None:
        """Create a profile. ALWAYS passes ``--no-alias`` (Pitfall #7).

        Hermes' default behavior (no flag) creates a launcher in
        ``~/.local/bin/<name>`` which silently shadows our binary. The console
        is the source of truth for aliases — disable Hermes' behavior at every
        call site.
        """
        args = ["profile", "create", "--no-alias", name]
        if clone_from is not None:
            args += ["--clone-from", clone_from]
        # Skill sync is the slowest step (~3s for 77 skills); 60s gives slack.
        r = await self.host.run_hermes(args, timeout_sec=60)
        _check_timeout(r.returncode, r.stdout, r.stderr)
        if r.returncode != 0:
            raise HermesCliError(
                r.returncode,
                r.stdout,
                r.stderr,
                hint=classify_create_error(r.stdout),
            )

    async def profile_delete(self, name: str) -> None:
        r = await self.host.run_hermes(["profile", "delete", "-y", name], timeout_sec=30)
        _check_timeout(r.returncode, r.stdout, r.stderr)
        if r.returncode != 0:
            raise HermesCliError(
                r.returncode, r.stdout, r.stderr, hint=_classify_not_found(r.stdout)
            )

    async def profile_rename(self, old: str, new: str) -> None:
        r = await self.host.run_hermes(["profile", "rename", old, new], timeout_sec=30)
        _check_timeout(r.returncode, r.stdout, r.stderr)
        if r.returncode != 0:
            # Rename collides with the same error vocab as create (duplicate /
            # invalid_name / reserved_name / not_found).
            hint = classify_create_error(r.stdout)
            if hint == "unknown":
                hint = _classify_not_found(r.stdout)
            raise HermesCliError(r.returncode, r.stdout, r.stderr, hint=hint)

    async def profile_export(self, name: str, *, output_path: Path) -> Path:
        r = await self.host.run_hermes(
            ["profile", "export", name, "-o", str(output_path)], timeout_sec=120
        )
        _check_timeout(r.returncode, r.stdout, r.stderr)
        if r.returncode != 0:
            raise HermesCliError(r.returncode, r.stdout, r.stderr)
        return output_path

    # ---- diagnostic ----------------------------------------------------------

    async def doctor(self, profile: str | None = None) -> bool:
        """Return True iff ``hermes doctor`` exits zero (the authoritative signal).

        Hermes formats its doctor output for humans and varies it across
        versions — exit-code is the only stable contract.
        """
        args = ["doctor"] if profile is None else ["-p", profile, "doctor"]
        r = await self.host.run_hermes(args, timeout_sec=30)
        _check_timeout(r.returncode, r.stdout, r.stderr)
        return r.returncode == 0

    # ---- gateway ops (Phase 3) -----------------------------------------------

    async def gateway_setup(self, profile: str) -> None:
        """Configure gateway for the named profile.

        Raises :class:`HermesCliError` with hint ``'gateway_setup_fail'`` on
        non-zero exit, or ``'timeout'`` on the -9 sentinel.
        """
        r = await self.host.run_hermes(["-p", profile, "gateway", "setup"], timeout_sec=60)
        _check_timeout(r.returncode, r.stdout, r.stderr)
        if r.returncode != 0:
            raise HermesCliError(r.returncode, r.stdout, r.stderr, hint="gateway_setup_fail")

    async def gateway_start(self, profile: str) -> None:
        """Start gateway for the named profile.

        Raises :class:`HermesCliError` with hint ``'gateway_start_fail'`` on
        non-zero exit, or ``'timeout'`` on the -9 sentinel.
        """
        r = await self.host.run_hermes(["-p", profile, "gateway", "start"], timeout_sec=30)
        _check_timeout(r.returncode, r.stdout, r.stderr)
        if r.returncode != 0:
            raise HermesCliError(r.returncode, r.stdout, r.stderr, hint="gateway_start_fail")

    async def gateway_status(self, profile: str) -> str:
        """Return ``'running'`` | ``'stopped'`` based on stdout heuristic.

        Note: Hermes v0.8 may not have per-profile gateway status; this is a
        best-effort string match. Phase 4 replaces with PID-file probe.
        """
        r = await self.host.run_hermes(["-p", profile, "gateway", "status"], timeout_sec=10)
        _check_timeout(r.returncode, r.stdout, r.stderr)
        if "running" in (r.stdout + r.stderr).lower():
            return "running"
        return "stopped"

    async def gateway_stop(self, profile: str) -> None:
        """Stop the gateway scoped to ``profile`` (NEVER passes ``--all``).

        Per FINDING-05: ``hermes -p <p> gateway stop`` (without ``--all``)
        keeps the kill scoped to this profile's gateway PID; using ``--all``
        would tear down every Bot's gateway and break the singleton-not-yours
        case (Pitfall #1).
        """
        r = await self.host.run_hermes(["-p", profile, "gateway", "stop"], timeout_sec=30)
        _check_timeout(r.returncode, r.stdout, r.stderr)
        if r.returncode != 0:
            raise HermesCliError(r.returncode, r.stdout, r.stderr, hint="gateway_stop_fail")

    async def gateway_restart(self, profile: str) -> None:
        """Restart the gateway scoped to ``profile``.

        Hermes ``restart`` is the recommended way to clear a stale PID file
        (status_decider's "PID 文件残留" hint points the user here).
        """
        r = await self.host.run_hermes(["-p", profile, "gateway", "restart"], timeout_sec=60)
        _check_timeout(r.returncode, r.stdout, r.stderr)
        if r.returncode != 0:
            raise HermesCliError(r.returncode, r.stdout, r.stderr, hint="gateway_restart_fail")

    async def pairing_approve(self, profile: str, code: str, *, platform: str = "feishu") -> None:
        """Approve a pending pairing code in ``profile`` for ``platform`` (default feishu).

        Validates ``code`` and ``platform`` BEFORE any subprocess call:
          * ``code`` must be 1..64 alphanumeric chars (matches FINDING-01 regex
            and rules out flag/space injection at adapter layer per Pitfall #14).
          * ``platform`` must be alphanumeric — same rationale.

        Failure classification (FINDING-05): the v0.8 ``pairing approve``
        prints ``Code '...' not found or expired`` to stdout AND returns
        exit-code 0. We classify by stdout text first; non-zero falls through
        to the generic ``pairing_approve_fail`` hint.
        """
        if not code or len(code) > 64 or not code.isalnum():
            raise ValueError("invalid pairing code")
        if not platform or not platform.isalnum():
            raise ValueError("invalid pairing platform")

        r = await self.host.run_hermes(
            ["-p", profile, "pairing", "approve", platform, code], timeout_sec=30
        )
        _check_timeout(r.returncode, r.stdout, r.stderr)

        # FINDING-05: expired-code path returns exit 0 but prints
        # "not found or expired". Classify by text first, then return code.
        merged = (r.stdout + "\n" + r.stderr).lower()
        if "not found or expired" in merged:
            raise HermesCliError(r.returncode, r.stdout, r.stderr, hint="pairing_expired")
        if r.returncode != 0:
            raise HermesCliError(r.returncode, r.stdout, r.stderr, hint="pairing_approve_fail")

    async def pairing_revoke(self, profile: str, user_id: str, *, platform: str = "feishu") -> None:
        """Revoke an approved user from ``profile``'s allowlist for ``platform``.

        Note: ``hermes pairing`` v0.8 has subcommands ``list``, ``approve``,
        ``revoke``, ``clear-pending`` (no ``reject``) per FINDING-05.
        """
        # user_id may contain '_' (Feishu open-ids look like ou_xxxxx) so we
        # only forbid whitespace, '-' (flag-like), '=' (env-like), and
        # control chars. Length cap mirrors pairing_approve.
        if not user_id or len(user_id) > 128:
            raise ValueError("invalid pairing user_id")
        if any(c.isspace() for c in user_id) or user_id.startswith("-") or "=" in user_id:
            raise ValueError("invalid pairing user_id")
        if not platform or not platform.isalnum():
            raise ValueError("invalid pairing platform")

        r = await self.host.run_hermes(
            ["-p", profile, "pairing", "revoke", platform, user_id], timeout_sec=15
        )
        _check_timeout(r.returncode, r.stdout, r.stderr)
        if r.returncode != 0:
            raise HermesCliError(
                r.returncode, r.stdout, r.stderr, hint=_classify_not_found(r.stdout)
            )

    async def pairing_list(self, profile: str) -> PairingListOutput:
        """Return parsed ``hermes -p <p> pairing list`` output.

        See :func:`parse_pairing_list` for the section/row contract.
        """
        r = await self.host.run_hermes(["-p", profile, "pairing", "list"], timeout_sec=15)
        _check_timeout(r.returncode, r.stdout, r.stderr)
        if r.returncode != 0:
            raise HermesCliError(
                r.returncode, r.stdout, r.stderr, hint=_classify_not_found(r.stdout)
            )
        return parse_pairing_list(r.stdout)

    def gateway_log_path(self, profile: str | None = None) -> Path:
        """Return the gateway log path for ``profile`` when Hermes exposes one.

        Hermes v0.8 originally wrote one shared file at
        ``~/.hermes/logs/gateway.log``. Current installs can run profile-scoped
        gateways that write ``~/.hermes/profiles/<profile>/logs/gateway.log``.
        Prefer that file when it exists; otherwise keep the shared-log fallback.
        """
        shared = Path.home() / ".hermes" / "logs" / "gateway.log"
        if profile and profile != "default":
            profile_log = Path.home() / ".hermes" / "profiles" / profile / "logs" / "gateway.log"
            if profile_log.exists() or profile_log.parent.exists():
                return profile_log
        return shared

    # ---- lark-oapi detection / install (Phase 3) -----------------------------

    async def check_lark_oapi(self) -> tuple[bool, str | None]:
        """Check if ``lark-oapi`` is installed. Returns ``(is_installed, version)``.

        Uses the historical ``python3 -m pip show`` check first, then falls
        back to the Hermes-managed venv. Hermes v0.8 may have ``lark_oapi``
        available in ``~/.hermes/hermes-agent/venv`` even when the system
        Python has no package or no pip.
        """
        r = await self.host.run_command(
            ["python3", "-m", "pip", "show", "lark-oapi"], timeout_sec=15
        )
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                if line.lower().startswith("version:"):
                    version = line.split(":", 1)[1].strip()
                    return (True, version)
            return (True, None)

        try:
            hermes_runtime = await self.host.run_command(
                [str(_HERMES_VENV_PYTHON), "-c", _LARK_OAPI_IMPORT_CHECK],
                timeout_sec=15,
            )
        except OSError:
            return (False, None)
        if hermes_runtime.returncode != 0:
            return (False, None)
        lines = hermes_runtime.stdout.strip().splitlines()
        version = lines[-1].strip() if lines else ""
        return (True, version or None)

    async def install_lark_oapi(self) -> None:
        """Install ``lark-oapi==1.5.5``. Version is HARDCODED — never user input.

        Raises :class:`HermesCliError` with hint ``'lark_oapi_missing'`` if pip
        install fails.
        """
        r = await self.host.run_command(
            ["python3", "-m", "pip", "install", "lark-oapi==1.5.5"],
            timeout_sec=120,
        )
        if r.returncode != 0:
            raise HermesCliError(r.returncode, r.stdout, r.stderr, hint="lark_oapi_missing")
