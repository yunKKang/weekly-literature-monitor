"""Journal pool helpers for the web search product."""

from __future__ import annotations

from typing import Any

from literature_monitor.config import CONFIG_DIR
from literature_monitor.core.utils import load_json


def load_journal_config() -> dict[str, Any]:
    return load_json(CONFIG_DIR / "journals.json")


def list_journal_pools() -> list[dict[str, Any]]:
    config = load_journal_config()
    mapping = config.get("pool_issn_mapping", {})
    pools = []
    for pool_id, pool in config.get("pools", {}).items():
        if not isinstance(pool, dict):
            continue
        pools.append(
            {
                "id": pool_id,
                "name": pool.get("name", pool_id),
                "description": pool.get("description", ""),
                "tier": pool.get("tier", ""),
                "priority": pool.get("priority"),
                "issns": mapping.get(pool_id, []),
                "journals": journals_for_pool(pool),
            }
        )
    return pools


def journals_for_pool(pool: dict[str, Any]) -> list[dict[str, Any]]:
    journals = []
    for category in pool.get("categories", {}).values():
        if not isinstance(category, dict):
            continue
        for journal in category.get("journals", []):
            if isinstance(journal, dict):
                journals.append(journal)
    return journals


def resolve_issns(pool_ids: list[str], explicit_issns: list[str]) -> list[str]:
    config = load_journal_config()
    mapping = config.get("pool_issn_mapping", {})
    if not pool_ids and not explicit_issns:
        return sorted(
            {issn.strip() for issn in config.get("issn_list", []) if issn.strip()}
        )
    issns = []
    for pool_id in pool_ids:
        issns.extend(mapping.get(pool_id, []))
    issns.extend(explicit_issns)
    return sorted({issn.strip() for issn in issns if issn.strip()})
