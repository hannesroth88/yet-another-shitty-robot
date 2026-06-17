"""Tiny logging setup shared by the CLI and the control server.

Call :func:`setup_logging` once at process start. Level is controlled by the
``ROBOT_LOG_LEVEL`` env var (default ``INFO``). Logs go to stderr, which the
``robot`` CLI tees into ``logs/server.log`` -- so STT/LLM lines show up in
``cli/robot logs``.
"""
from __future__ import annotations

import logging
import os

_CONFIGURED = False


def setup_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = os.environ.get("ROBOT_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    _CONFIGURED = True
