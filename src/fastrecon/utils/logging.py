"""Logging helpers."""

from __future__ import annotations

import logging
import os

_LEVEL = os.environ.get("FASTRECON_LOG_LEVEL", "INFO").upper()


def get_logger(name: str = "fastrecon") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(_LEVEL)
        logger.propagate = False
    return logger
