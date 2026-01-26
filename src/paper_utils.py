#!/usr/bin/env python3
"""Utility functions for the weekly literature monitor.

This module provides common utilities including timestamp generation,
DOI handling, and HTTP fetching.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from config import config

logger = logging.getLogger(__name__)


def now_iso() -> str:
    """Return current timestamp in ISO 8601 format with UTC timezone."""
    return datetime.now(timezone.utc).isoformat()


def today_str() -> str:
    """Return today's date as YYYY-MM-DD string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def days_ago(n: int) -> str:
    """Return date N days ago as YYYY-MM-DD string."""
    dt = datetime.now(timezone.utc) - timedelta(days=n)
    return dt.strftime("%Y-%m-%d")


@dataclass(frozen=True)
class DoiInfo:
    """Parsed DOI information."""

    prefix: str  # DOI prefix (e.g., "10.1234")
    suffix: str  # DOI suffix (e.g., "nature.s12345")
    full: str  # Full DOI


def normalize_doi(doi: str) -> DoiInfo | None:
    """Normalize and parse a DOI string.

    Args:
        doi: Raw DOI string (may include URL prefix, spaces, etc.)

    Returns:
        DoiInfo object with parsed components, or None if invalid.
    """
    if not doi:
        return None

    # Remove any URL prefix
    cleaned = doi.strip()
    cleaned = re.sub(r"^https?://(dx\.)?doi\.org/", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip().lower()

    # Remove common trailing characters
    cleaned = re.sub(r"[\s#?;.]+$", "", cleaned)

    # DOI format: 10.<prefix>/<suffix>
    doi_pattern = r"^(10\.\d{4,9})/(.+)$"
    match = re.match(doi_pattern, cleaned)

    if not match:
        return None

    prefix, suffix = match.groups()
    suffix = re.sub(r"[#?].*$", "", suffix).strip()

    return DoiInfo(prefix=prefix, suffix=suffix, full=f"{prefix}/{suffix}")


def doi_to_url(doi: str | DoiInfo) -> str:
    """Convert DOI to official DOI URL."""
    if isinstance(doi, DoiInfo):
        doi_str = doi.full
    else:
        normalized = normalize_doi(doi)
        doi_str = normalized.full if normalized else doi
    return f"https://doi.org/{doi_str}"


def clean_title(title: str | None) -> str:
    """Clean and normalize a paper title."""
    if not title:
        return ""

    # Remove HTML entities
    title = re.sub(r"&amp;", "&", title)
    title = re.sub(r"&lt;", "<", title)
    title = re.sub(r"&gt;", ">", title)
    title = re.sub(r"&quot;", '"', title)
    title = re.sub(r"&#39;", "'", title)
    title = re.sub(r"&nbsp;", " ", title)

    # Normalize whitespace
    title = " ".join(title.split())

    return title


def clean_abstract(abstract: str | None) -> str:
    """Clean and normalize an abstract."""
    if not abstract:
        return ""

    # Remove HTML tags
    abstract = re.sub(r"<[^>]+>", " ", abstract)

    # Normalize whitespace
    abstract = " ".join(abstract.split())

    return abstract


@dataclass
class SearchParams:
    """Parameters for a literature search."""

    query: str = ""
    year_from: str | int | None = None
    year_to: str | int | None = None
    issns: list[str] | None = None
    venue: str | None = None
    max_results: int = 100
    sort_by: str = "published"  # relevance, published, citationCount
    sort_order: str = "desc"  # desc, asc


def fetch_url(
    url: str,
    *,
    timeout_s: int = 30,
    headers: dict[str, str] | None = None,
) -> tuple[int | None, bytes]:
    """Fetch a URL and return status code and body.

    Args:
        url: URL to fetch
        timeout_s: Timeout in seconds
        headers: Optional HTTP headers

    Returns:
        Tuple of (status_code, body_bytes). Returns (None, b"") on failure.
    """
    default_headers = {
        "User-Agent": config.USER_AGENT,
    }
    if headers:
        default_headers.update(headers)

    req = urllib.request.Request(url, headers=default_headers)

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status = getattr(resp, "status", None)
            return status, resp.read()
    except urllib.error.HTTPError as e:
        body = e.read() if hasattr(e, "read") else b""
        return getattr(e, "code", None), body
    except urllib.error.URLError:
        return None, b""


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file."""
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict[str, Any]) -> None:
    """Save data to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_issn_list(config_path: Path | None = None) -> list[str]:
    """Get the list of ISSN from journals configuration."""
    if config_path is None:
        script_dir = Path(__file__).parent.parent
        config_path = script_dir / "config" / "journals.json"

    config = load_json(config_path)
    return config.get("issn_list", [])


def get_issn_by_tier(
    tiers: list[str] | None = None,
    config_path: Path | None = None,
) -> tuple[list[str], dict[str, int]]:
    """Get ISSNs filtered by monitoring tier.

    Args:
        tiers: List of tier codes to include (e.g., ["S", "A"]). None = all tiers.
        config_path: Path to journals.json config file.

    Returns:
        Tuple of (issn_list, tier_info) where tier_info maps tier -> count.
    """
    if config_path is None:
        script_dir = Path(__file__).parent.parent
        config_path = script_dir / "config" / "journals.json"

    config = load_json(config_path)
    pools = config.get("pools", {})
    pool_issn_mapping = config.get("pool_issn_mapping", {})

    issns: list[str] = []
    tier_counts: dict[str, int] = {}

    for pool_name, pool_data in pools.items():
        if not isinstance(pool_data, dict):
            continue
        pool_tier = pool_data.get("tier", "A")

        if tiers is not None and pool_tier not in tiers:
            continue

        pool_issns = pool_issn_mapping.get(pool_name, [])
        issns.extend(pool_issns)
        tier_counts[pool_tier] = tier_counts.get(pool_tier, 0) + len(pool_issns)

    return list(set(issns)), tier_counts


def get_tier_frequency(tier: str, config_path: Path | None = None) -> int:
    """Get monitoring frequency in days for a tier."""
    if config_path is None:
        script_dir = Path(__file__).parent.parent
        config_path = script_dir / "config" / "journals.json"

    config = load_json(config_path)
    tier_config = config.get("monitoring_tiers", {}).get(tier, {})
    return tier_config.get("frequency_days", 7)


def get_journal_title_by_issn(issn: str, config_path: Path | None = None) -> str | None:
    """Get journal title by ISSN."""
    if config_path is None:
        script_dir = Path(__file__).parent.parent
        config_path = script_dir / "config" / "journals.json"

    config = load_json(config_path)

    for pool_data in config.get("pools", {}).values():
        if not isinstance(pool_data, dict):
            continue
        for cat_data in pool_data.get("categories", {}).values():
            if not isinstance(cat_data, dict):
                continue
            for journal in cat_data.get("journals", []):
                if journal.get("issn") == issn:
                    return journal.get("title")

    return None


def get_conference_titles(
    tiers: list[str] | None = None,
    config_path: Path | None = None,
) -> list[dict[str, str]]:
    """Get list of conference container titles from Pool C.

    Args:
        tiers: List of tier codes to filter by (e.g., ["A"]). None = all tiers.
        config_path: Path to journals.json config file.

    Returns:
        List of dicts with keys: container_title, full_name, publisher
    """
    if config_path is None:
        script_dir = Path(__file__).parent.parent
        config_path = script_dir / "config" / "journals.json"

    config = load_json(config_path)
    pools = config.get("pools", {})
    conferences: list[dict[str, str]] = []

    for pool_name, pool_data in pools.items():
        if not isinstance(pool_data, dict):
            continue

        pool_tier = pool_data.get("tier", "A")

        if tiers is not None and pool_tier not in tiers:
            continue

        categories = pool_data.get("categories", {})
        for cat_name, cat_data in categories.items():
            if not isinstance(cat_data, dict):
                continue

            conf_list = cat_data.get("conferences", [])
            for conf in conf_list:
                if isinstance(conf, dict) and conf.get("container_title"):
                    conferences.append(
                        {
                            "container_title": conf.get("container_title", ""),
                            "full_name": conf.get("full_name", ""),
                            "publisher": conf.get("publisher", ""),
                        }
                    )

    return conferences
