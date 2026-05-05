"""lark-cli subprocess wrapper and output parsing.

Wizard step 1 runs ``lark-cli config init --new --lang zh``. The command prints
an ASCII QR block plus an ``https://open.feishu.cn/page/cli?...`` URL, then
waits while the user completes the browser-side Feishu app setup.

Parsing stays in :func:`extract_open_feishu_url`; process I/O stays in
:func:`stream_lark_init_lines` so route tests can override the generator.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
from collections.abc import AsyncIterator, Awaitable, Callable

_URL_RE = re.compile(r"https://open\.feishu\.cn/page/cli\?[^\s\"'`<>]+")


def extract_open_feishu_url(text: str) -> str | None:
    """Extract the first open.feishu.cn setup URL from a lark-cli output chunk."""
    m = _URL_RE.search(text)
    return m.group(0) if m else None


async def stream_lark_init_lines(
    *,
    binary: str = "lark-cli",
    extra_args: tuple[str, ...] = ("config", "init", "--new", "--lang", "zh"),
    stop_check: Callable[[], Awaitable[bool]] | None = None,
    poll_interval_sec: float = 0.5,
    max_runtime_sec: float = 600.0,
) -> AsyncIterator[str]:
    """Run lark-cli config init and stream stdout/stderr line by line.

    On caller cancellation, SIGKILL and reap the child process. Yielded strings
    preserve trailing newlines. If the binary is missing, yield
    ``__lark_cli_missing__\n`` and finish.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            binary,
            *extra_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
    except FileNotFoundError:
        yield "__lark_cli_missing__\n"
        return

    assert proc.stdout is not None
    loop = asyncio.get_event_loop()
    started = loop.time()
    read_task: asyncio.Task[bytes] | None = asyncio.create_task(proc.stdout.readline())
    try:
        while True:
            if stop_check is not None and await stop_check():
                break
            if loop.time() - started > max_runtime_sec:
                yield "__lark_cli_timeout__\n"
                break
            if read_task is None:
                break
            done, _ = await asyncio.wait({read_task}, timeout=poll_interval_sec)
            if not done:
                continue
            line = read_task.result()
            read_task = None
            if not line:
                break
            yield line.decode("utf-8", errors="replace")
            read_task = asyncio.create_task(proc.stdout.readline())
        await proc.wait()
    finally:
        if read_task is not None and not read_task.done():
            read_task.cancel()
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
