"""Search topic preset helpers backed by YAML Topic definitions.

The Web UI consumes a flat preset-like DTO, but the runtime source of truth is
now topics/*.yaml. Keep the adapter here so callers do not read legacy
config/search_topics.json.
"""

from __future__ import annotations

from typing import Any

from literature_monitor.topic.loader import load_all_topics
from literature_monitor.topic.schema import Topic


_DEFAULT_MAX_RESULTS_PER_SOURCE = 10000


def _unique_extend(items: list[str], values: list[str]) -> None:
    seen = set(items)
    for value in values:
        if value and value not in seen:
            items.append(value)
            seen.add(value)


def topic_to_search_preset(topic: Topic) -> dict[str, Any]:
    """Project a YAML Topic into the flat DTO expected by the Web UI/CLI."""
    keywords: list[str] = []
    synonyms: list[str] = []
    negative_keywords: list[str] = []

    for keyword_set in topic.keyword_sets:
        _unique_extend(keywords, keyword_set.keywords)
        _unique_extend(synonyms, keyword_set.synonyms)
        _unique_extend(negative_keywords, keyword_set.negative_keywords)

    for pipeline in topic.pipelines:
        _unique_extend(negative_keywords, pipeline.negative_keywords)

    return {
        "id": topic.id,
        "name": topic.name,
        "description": topic.description,
        "date_from": topic.date_range.date_from,
        "date_to": topic.date_range.date_to,
        "keywords": keywords,
        "synonyms": synonyms,
        "negative_keywords": negative_keywords,
        "journal_pool_ids": topic.journal_pool_ids,
        "journal_issns": topic.journal_issns,
        "include_conferences": topic.include_conferences,
        "min_score": 0,
        "max_results_per_source": _DEFAULT_MAX_RESULTS_PER_SOURCE,
        "source": "yaml",
    }


def list_search_topics() -> list[dict[str, Any]]:
    """List all search topics from YAML definitions.

    This is intentionally YAML-only: config/search_topics.json is a retired
    legacy file and must not be part of normal runtime topic selection.
    """
    topics = load_all_topics().values()
    return [topic_to_search_preset(topic) for topic in sorted(topics, key=lambda t: t.id)]
