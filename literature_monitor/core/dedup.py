"""Deduplication helpers for multi-source literature records."""

from __future__ import annotations

import re
from collections import defaultdict

from literature_monitor.compat import SRC  # noqa: F401
from paper_utils import normalize_doi

from .models import ProviderPaper


def normalized_doi(value: str | None) -> str | None:
    if not value:
        return None
    parsed = normalize_doi(value)
    return parsed.full if parsed else value.strip().lower() or None


def normalize_title(value: str | None) -> str:
    if not value:
        return ""
    text = value.lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def first_author_key(authors: list[str]) -> str:
    if not authors:
        return ""
    return normalize_title(authors[0])[:48]


def deduplicate_records(records: list[ProviderPaper]) -> list[list[ProviderPaper]]:
    """Group provider records by DOI first, then conservative title/year/author."""
    groups: dict[str, list[ProviderPaper]] = defaultdict(list)
    no_doi: list[ProviderPaper] = []

    for record in records:
        doi = normalized_doi(record.paper.doi)
        if doi:
            groups[f"doi:{doi}"].append(record)
        else:
            no_doi.append(record)

    for record in no_doi:
        title = normalize_title(record.paper.title)
        year = record.paper.year or ""
        author = first_author_key(record.paper.authors)
        if not title:
            key = f"raw:{id(record)}"
        else:
            key = f"title:{title}|{year}|{author}"
        groups[key].append(record)

    return list(groups.values())

