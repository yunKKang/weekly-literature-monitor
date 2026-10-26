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
    unknown_pools = sorted(set(pool_ids) - set(mapping))
    if unknown_pools:
        raise ValueError(f"Unknown journal pool(s): {', '.join(unknown_pools)}")
    if not pool_ids and not explicit_issns:
        default_pool = config.get("monitoring", {}).get("default_pool")
        if default_pool:
            return sorted(set(mapping[default_pool]))
        return sorted(
            {issn.strip() for issn in config.get("issn_list", []) if issn.strip()}
        )
    issns = []
    for pool_id in pool_ids:
        issns.extend(mapping.get(pool_id, []))
    issns.extend(explicit_issns)
    return sorted({issn.strip() for issn in issns if issn.strip()})


def monitoring_issns(
    run_date: str, *, include_supplementary: bool = False
) -> list[str]:
    """Use the core pool weekly and the supplementary pool on the first Monday."""
    from datetime import date

    config = load_journal_config()
    options = config.get("monitoring", {})
    if not options:
        return resolve_issns([], [])
    pools = [options["default_pool"]]
    day = date.fromisoformat(run_date)
    if include_supplementary or (day.weekday() == 0 and day.day <= 7):
        pools.append(options["supplementary_pool"])
    return resolve_issns(pools, [])


def validate_catalog() -> None:
    """Reject malformed identifiers or duplicate journal identities before retrieval."""
    import re

    config = load_journal_config()
    used = set()
    for pool_id, pool in config["pools"].items():
        declared = []
        for journal in journals_for_pool(pool):
            issn = journal["issn"]
            if not re.fullmatch(r"\d{4}-\d{3}[\dX]", issn):
                raise ValueError(f"Invalid ISSN: {issn}")
            digits = issn.replace("-", "")
            check = (
                11 - sum(int(c) * w for c, w in zip(digits[:7], range(8, 1, -1))) % 11
            ) % 11
            if digits[-1] != ("X" if check == 10 else str(check)):
                raise ValueError(f"Invalid ISSN checksum: {issn}")
            if issn in used:
                raise ValueError(f"Duplicate journal ISSN: {issn}")
            used.add(issn)
            declared.append(issn)
        if set(declared) != set(config["pool_issn_mapping"][pool_id]):
            raise ValueError(f"Journal mapping drift: {pool_id}")
    if used != set(config["issn_list"]):
        raise ValueError("Global journal list drift")
