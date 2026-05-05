from __future__ import annotations

import logging
import sys

from app.secret_filter import SecretFilter


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logger with SecretFilter and a console handler."""
    root = logging.getLogger()
    root.setLevel(level)

    # Remove default handlers to avoid double-logging on reload.
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s [%(name)s] %(message)s"))
    handler.addFilter(SecretFilter())  # scrub formatted msg on handler
    root.addHandler(handler)
    root.addFilter(SecretFilter())  # scrub raw record on logger

    # Tame uvicorn.access noise; default INFO.
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(level)
