#!/usr/bin/env python3
"""Configuration module for weekly literature monitor.

All configurable settings are centralized here and can be overridden
via environment variables.
"""

from __future__ import annotations

import os


class Config:
    CROSSREF_MAILTO: str = os.environ.get(
        "CROSSREF_MAILTO", "weekly-literature-monitor@example.com"
    )

    USER_AGENT: str = os.environ.get(
        "USER_AGENT", f"weekly-literature-monitor/1.0 (mailto:{CROSSREF_MAILTO})"
    )

    API_DELAY_SECONDS: float = float(os.environ.get("API_DELAY_SECONDS", "0.3"))

    MAX_PAPERS_PER_JOURNAL: int = int(os.environ.get("MAX_PAPERS_PER_JOURNAL", "50"))

    API_TIMEOUT_SECONDS: int = int(os.environ.get("API_TIMEOUT_SECONDS", "30"))

    MAX_SEEN_DOIS: int = int(os.environ.get("MAX_SEEN_DOIS", "10000"))

    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")


config = Config()
