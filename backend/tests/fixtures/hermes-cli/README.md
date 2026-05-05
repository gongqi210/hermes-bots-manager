# Hermes CLI Golden Fixtures

Captured: 2026-04-29
Source: macOS Darwin 25.4.0, hermes v0.8.0 (2026.4.8)
Source machine: /Users/example/.local/bin/hermes
Source profile: ~/.hermes/ (default) + test-research-probe

These fixtures are the **frozen contract** for `app/adapters/parsers.py`.
Do NOT regenerate casually — only when Hermes upgrades to v0.9+ or output format changes.

## Re-capture

Run `make capture-fixtures` on a host with hermes v0.8+ on PATH.

## Files

- `profile_list_2_profiles.txt` — `hermes profile list` with default+1 profile (real capture)
- `profile_list_only_default.txt` — derived: same header but only the `◆default` row (used for "fresh install" parser test)
- `profile_show_default.txt` — `hermes profile show <name>` for the unconfigured `test-research-probe` profile (real capture; filename retained for downstream parser test naming compatibility)
- `profile_show_unconfigured.txt` — duplicate of `profile_show_default.txt`; explicit alias for parser fixtures testing the `.env: not configured` branch
- `profile_create_success.txt` — **synthesized template** for the success case Hermes prints (real machine output was not captured during the success path; parser must tolerate extra noise lines and rely on exit code)
- `profile_create_dup_error.txt` — duplicate-name error (note: `Error:` is emitted to stdout, not stderr)
- `profile_create_invalid_name.txt` — invalid regex error (real capture)
- `profile_create_default_rejected.txt` — reserved-name rejection (real capture)
- `gateway_status_running.txt` — `hermes -p default gateway status` (running) — real capture
- `gateway_status_stopped.txt` — **synthesized stopped variant**; real probe machine had gateway running so the stopped output was not captured live
- `gateway_pid_default.json` — exact bytes from `~/.hermes/gateway.pid`

## Pitfalls (covered in 02-RESEARCH.md)

- Errors go to stdout, not stderr — capture both streams in subprocess wrappers
- `gateway status` cross-profile leak in v0.8: any `-p <name>` falls back to the default's PID (Pitfall #1)
- Skill sync prints noise on `profile create` — parser should be exit-code-driven, not text-driven
- `default` profile lives at `~/.hermes/`, NOT `~/.hermes/profiles/default/` (Pitfall #2)

## Maintenance

Files marked **synthesized** above MUST be replaced with real captures the first time we observe the corresponding state in the wild. When that happens:
1. Pipe the live output into the fixture (preserve trailing newline policy: every file ends with one `\n`)
2. Update this README to remove the `synthesized` tag
3. Bump fixtures across affected parser tests
