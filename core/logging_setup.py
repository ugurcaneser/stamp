"""Local operation log (§4.5): every destructive operation, device, timestamp,
and command run gets written to a log file for post-hoc debugging.

All core/ modules log through logging.getLogger("stamp"); this module just
wires that logger to a file. GUI and CLI both call configure_logging() once
at startup.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

LOGGER_NAME = "stamp"


def default_log_dir() -> str:
    xdg_state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(xdg_state, "stamp")


def default_log_path() -> str:
    return os.path.join(default_log_dir(), "stamp.log")


def configure_logging(log_path: str | None = None, level: int = logging.INFO) -> logging.Logger:
    """Configure (idempotently) the "stamp" logger to append to log_path.

    Safe to call more than once (e.g. GUI + CLI code paths in tests) — it
    won't stack duplicate handlers.
    """
    path = log_path or default_log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)

    already_configured = any(
        isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", None) == os.path.abspath(path)
        for h in logger.handlers
    )
    if not already_configured:
        handler = logging.FileHandler(path)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)

    logger.info("=== Stamp session started at %s ===", datetime.now(timezone.utc).isoformat())
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
