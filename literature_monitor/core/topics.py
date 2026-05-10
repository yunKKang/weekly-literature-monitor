"""Search topic preset helpers."""

from __future__ import annotations

from typing import Any

from literature_monitor.compat import CONFIG_DIR, SRC  # noqa: F401
from paper_utils import load_json


def load_topic_config() -> dict[str, Any]:
    return load_json(CONFIG_DIR / "search_topics.json")


def list_search_topics() -> list[dict[str, Any]]:
    config = load_topic_config()
    topics = config.get("topics", [])
    return [topic for topic in topics if isinstance(topic, dict) and topic.get("id")]
