# Hermes v0.8 Empirical Findings -- Phase 4 Wave 0

Captured: 2026-05-04
Hermes version: Hermes Agent v0.8.0 (2026.4.8)

## FINDING-01 -- Pairing log line shape

Source fixture: `pairing_log_sample.txt`

Literal example line containing the pairing code:
```text
UNAVAILABLE -- No live Feishu pairing event was present in this workspace on 2026-05-04.
```

Derived regex (Python, used by `pairing_extractor.extract_pairing`):
```python
UNAVAILABLE -- downstream must set _PAIRING_FALLBACK = True and use r"pairing.*?code[\s:=]+([A-Za-z0-9]{4,12})" until a real pairing log line is captured.
```

Captured groups: code (alphanumeric, len 4-12). Platform and feishu user_id were not available from a real pairing log line.

Conclusion: The real pairing-code log shape remains unavailable; Phase 4 may proceed only with the documented fallback regex and a warning flag.

## FINDING-02 -- Cross-profile approve behaviour

Machine-readable: `FINDING-02.cross_profile_approve = "fails"`

Command tested: `hermes -p <other-profile> pairing approve feishu <CODE-FROM-DEFAULT>`
Exit code: UNTESTABLE
Stdout snippet:
```text
UNAVAILABLE -- No pending pairing code was available to probe cross-profile approve behaviour.
```

Conclusion: UNTESTABLE -- treated conservatively as "Cross-profile approve FAILS -- must switch active profile first. Approve API must include profile-switch step."

Action for 04-05: must call or enforce the active-profile switch before `pairing_approve`; if the active profile is different, return HTTP 409 with the message containing "请先切换到该 Bot 的 Profile".

Action for 04-06: no WebSocket-specific behaviour; approve semantics are owned by the REST pairing endpoint.

## FINDING-03 -- Profile context in shared gateway.log

Source fixture: `gateway_log_active_profile_sample.txt`

Hypothesis tested: do log lines carry `[profile=<name>]` prefix or any other per-line profile marker?

Conclusion: NO usable per-line marker was available from a safe fixture. Local sampled lines had no `[profile=<name>]` marker, but raw lines contained credential-bearing Feishu websocket URLs and user message content, so they were not committed.

Action for 04-05: Supervisor filter degrades to "this Supervisor receives lines iff its bot_name == active_profile"; use `HostOps.read_active_profile()` / profile-list parsing and do not rely on a per-line profile marker.

## FINDING-04 -- FEISHU_ALLOWED_USERS value format

Source fixture: `env_with_allowed_users.txt`

Literal line:
```dotenv
FEISHU_ALLOWED_USERS=ou_xxx,ou_yyy
```

Conclusion: comma-separated single line

Machine-readable: `FINDING-04.allowed_users_separator = ","`

Action for 04-03: ProfileFsAdapter.read_allowed_users / write_allowed_users uses comma as delimiter, trims whitespace around entries, and preserves an empty value as an empty allowlist.

## FINDING-05 -- Other notes (gateway.pid `start_time` field, etc.)

- `start_time` field in `~/.hermes/gateway.pid`: always null in v0.8, fall back to psutil.create_time().
- `hermes pairing` subcommands present: `list`, `approve`, `revoke`, `clear-pending` (NO `reject`).
- `hermes -p <p> gateway stop` (without --all): works per Phase 4 research; keep stop scoped to the current profile and do not use `--all`.
- `hermes pairing approve feishu TESTNOTREAL` returned the "not found or expired" message with exit code 0 on 2026-05-04; adapter code must classify by stdout text, not only return code.
- Current local `hermes pairing list` had no pending requests and two approved Feishu users, so pending-row parsing still needs a real pending fixture.

## Re-capture instructions

Run `make capture-phase4-fixtures` (defined in Makefile) -- see that target for the recipe.
