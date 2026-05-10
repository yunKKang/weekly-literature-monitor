"""Topic loader — YAML to Topic object with validation."""

from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any

from .schema import Topic

# Default locations
_TOPICS_DIR_ENV = "LITMON_TOPICS_DIR"
_DEFAULT_TOPICS_DIR = Path(__file__).parent.parent.parent / "topics"


def _topics_dir() -> Path:
    """Resolve the topics directory."""
    import os
    env = os.environ.get(_TOPICS_DIR_ENV)
    if env:
        return Path(env)
    return _DEFAULT_TOPICS_DIR


def list_topics() -> list[str]:
    """List all topic IDs (filenames without .yaml extension)."""
    d = _topics_dir()
    if not d.exists():
        return []
    return sorted(
        p.stem for p in d.glob("*.yaml")
        if not p.stem.startswith(".")
    )


def load_topic(topic_id: str) -> Topic:
    """Load and validate a topic from its YAML file.

    Args:
        topic_id: Topic identifier (filename without .yaml)

    Returns:
        Validated Topic object

    Raises:
        FileNotFoundError: If topic YAML does not exist
        pydantic.ValidationError: If YAML content fails schema validation
    """
    path = _topics_dir() / f"{topic_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Topic file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        raise ValueError(f"Empty topic file: {path}")

    # Ensure id matches filename
    if "id" not in data:
        data["id"] = topic_id

    return Topic.model_validate(data)


def load_all_topics() -> dict[str, Topic]:
    """Load all topics from the topics directory.

    Returns:
        Dict mapping topic_id -> Topic
    """
    result: dict[str, Topic] = {}
    for topic_id in list_topics():
        result[topic_id] = load_topic(topic_id)
    return result


def validate_topic(topic_id: str) -> tuple[bool, str]:
    """Validate a topic YAML file.

    Returns:
        (is_valid, message)
    """
    try:
        topic = load_topic(topic_id)
        ks_names = {ks.name for ks in topic.keyword_sets}
        errors: list[str] = []
        for pipe in topic.pipelines:
            for req in pipe.hard_threshold.required_sets:
                if req not in ks_names:
                    errors.append(
                        f"Pipeline '{pipe.name}': required_set '{req}' "
                        f"not found in keyword_sets (available: {ks_names})"
                    )
            for ks_ref in pipe.keyword_sets:
                if ks_ref not in ks_names:
                    errors.append(
                        f"Pipeline '{pipe.name}': keyword_set '{ks_ref}' "
                        f"not found in keyword_sets"
                    )
        if errors:
            return False, "Validation errors:\n" + "\n".join(f"  - {e}" for e in errors)
        n_pipes = len(topic.pipelines)
        n_sets = len(topic.keyword_sets)
        n_pools = len(topic.journal_pool_ids)
        return True, (
            f"OK: {n_pipes} pipelines, {n_sets} keyword_sets, "
            f"{n_pools} journal_pools, scoring: "
            f"title_w={topic.scoring.title_weight} "
            f"high≥{topic.scoring.high_threshold}"
        )
    except Exception as e:
        return False, f"Error: {e}"


def topic_to_legacy_keywords_dict(topic: Topic) -> dict[str, Any]:
    """Convert a Topic into a dict resembling the legacy keywords.json structure.

    This enables incremental migration: existing scoring code can consume
    topic-derived config without being rewritten yet.
    """
    # Build keyword set lookup
    ks_map = {ks.name: ks for ks in topic.keyword_sets}

    # Build pipelines dict in legacy format
    pipelines: dict[str, Any] = {}
    for pipe in topic.pipelines:
        domain_key = None
        domain_ks = None
        for ks_ref in pipe.keyword_sets:
            if ks_ref not in ("investment_terms", "asset_types", "bonus_terms"):
                domain_key = ks_ref
                domain_ks = ks_map.get(ks_ref)
                break

        pipelines[pipe.name] = {
            "name": pipe.name,
            "priority": pipe.priority,
            "hard_threshold": {
                "required": [
                    {"field": "both", "keyword_set": s}
                    for s in pipe.hard_threshold.required_sets
                ],
            },
        }
        if domain_ks:
            pipelines[pipe.name][domain_key] = {
                "keywords_en": domain_ks.keywords,
            }
        if pipe.bonus_terms:
            pipelines[pipe.name]["bonus_terms"] = {
                "keywords_en": pipe.bonus_terms,
                "max_bonus": 6,
            }

    # Build investment terms dict
    inv_ks = ks_map.get("investment_terms")
    investment_terms = {
        "keywords_en": inv_ks.keywords if inv_ks else [],
        "keywords_cn": [],
        "concept_groups": {},
    }

    # Build asset types dict
    asset_ks = ks_map.get("asset_types")
    asset_types = {
        "description": "SNA asset type categories",
        "structures": {"keywords_en": []},
        "machinery_equipment": {"keywords_en": []},
    }
    if asset_ks:
        asset_types["all"] = {"keywords_en": asset_ks.keywords}

    return {
        "version": topic.version,
        "description": topic.description,
        "scoring_rules": {
            "title_weight": topic.scoring.title_weight,
            "abstract_weight": topic.scoring.abstract_weight,
            "high_threshold": topic.scoring.high_threshold,
            "medium_threshold": topic.scoring.medium_threshold,
        },
        "gfcf_vocabulary": {
            "investment_terms": investment_terms,
            "asset_types": asset_types,
        },
        "pipelines": pipelines,
        "negative_keywords": {
            pipe.name: {
                "keywords_en": pipe.negative_keywords,
                "apply_to": [pipe.name],
            }
            for pipe in topic.pipelines
            if pipe.negative_keywords
        },
    }
