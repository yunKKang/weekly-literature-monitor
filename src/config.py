#!/usr/bin/env python3
"""Centralized configuration for weekly literature monitor.

All configurable settings are defined here and can be overridden
via environment variables. Other modules should import from here.
"""

from __future__ import annotations

import os


class Config:
    CROSSREF_MAILTO: str = os.environ.get(
        "CROSSREF_MAILTO", "weekly-literature-monitor@example.com"
    )

    USER_AGENT: str = os.environ.get(
        "USER_AGENT", f"weekly-literature-monitor/2.1 (mailto:{CROSSREF_MAILTO})"
    )

    API_DELAY_SECONDS: float = float(os.environ.get("API_DELAY_SECONDS", "0.3"))

    MAX_PAPERS_PER_JOURNAL: int = int(os.environ.get("MAX_PAPERS_PER_JOURNAL", "200"))

    MAX_PAPERS_PER_BATCH: int = int(os.environ.get("MAX_PAPERS_PER_BATCH", "1000"))

    MAX_CURSOR_PAGES: int = int(os.environ.get("MAX_CURSOR_PAGES", "100"))

    MAX_PAPERS_PER_CONFERENCE: int = int(
        os.environ.get("MAX_PAPERS_PER_CONFERENCE", "50")
    )

    API_TIMEOUT_SECONDS: int = int(os.environ.get("API_TIMEOUT_SECONDS", "30"))

    CROSSREF_RETRY_COUNT: int = int(os.environ.get("CROSSREF_RETRY_COUNT", "3"))

    CROSSREF_RETRY_DELAY: float = float(
        os.environ.get("CROSSREF_RETRY_DELAY", "2.0")
    )

    MAX_SEEN_DOIS: int = int(os.environ.get("MAX_SEEN_DOIS", "10000"))

    OVERLAP_DAYS: int = int(os.environ.get("OVERLAP_DAYS", "30"))

    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")

    ISSN_BATCH_SIZE: int = int(os.environ.get("ISSN_BATCH_SIZE", "10"))

    GITHUB_RETRY_COUNT: int = int(os.environ.get("GITHUB_RETRY_COUNT", "3"))

    GITHUB_RETRY_DELAY: float = float(os.environ.get("GITHUB_RETRY_DELAY", "2.0"))

    # GitHub API credentials (from environment)
    GITHUB_TOKEN: str | None = os.environ.get("GITHUB_TOKEN")
    GITHUB_REPOSITORY: str | None = os.environ.get("GITHUB_REPOSITORY")


config = Config()
