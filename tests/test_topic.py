"""Tests for the Topic abstraction layer."""

from __future__ import annotations

import pytest
from pathlib import Path

from literature_monitor.topic.schema import (
    Topic,
    KeywordSet,
    PipelineConfig,
    HardThreshold,
    ScoringConfig,
)
from literature_monitor.topic.loader import (
    list_topics,
    load_topic,
    load_all_topics,
    validate_topic,
    topic_to_legacy_keywords_dict,
)


TOPICS_DIR = Path(__file__).parent.parent / "topics"


@pytest.fixture(autouse=True)
def _set_topics_dir(monkeypatch, tmp_path):
    """Point loader at the real topics/ directory for tests."""
    monkeypatch.setenv("LITMON_TOPICS_DIR", str(TOPICS_DIR))


class TestTopicSchema:
    def test_minimal_topic(self):
        t = Topic(
            id="test_minimal",
            name="Test",
            keyword_sets=[
                KeywordSet(name="set_a", keywords=["alpha", "beta"]),
                KeywordSet(name="set_b", keywords=["gamma"]),
            ],
            pipelines=[
                PipelineConfig(
                    name="pipe1",
                    hard_threshold=HardThreshold(required_sets=["set_a", "set_b"]),
                )
            ],
        )
        assert t.id == "test_minimal"
        assert len(t.pipelines) == 1
        assert t.scoring.title_weight == 8  # default

    def test_invalid_id_rejected(self):
        with pytest.raises(Exception):
            Topic(
                id="Bad-ID!",
                name="Test",
                keyword_sets=[KeywordSet(name="a", keywords=["x"])],
                pipelines=[
                    PipelineConfig(
                        name="p",
                        hard_threshold=HardThreshold(required_sets=["a"]),
                    )
                ],
            )

    def test_defaults(self):
        t = Topic(
            id="test_defaults",
            name="Test",
            keyword_sets=[],
            pipelines=[],
        )
        assert t.scoring.high_threshold == 20
        assert t.llm_review.enabled is False
        assert t.exporters == []


class TestLoadTopic:
    def test_load_gfcf(self):
        t = load_topic("gfcf_environment")
        assert t.id == "gfcf_environment"
        assert len(t.pipelines) == 4
        assert len(t.keyword_sets) >= 5
        inv = next(ks for ks in t.keyword_sets if ks.name == "investment_terms")
        assert "GFCF" in inv.keywords
        assert "gross fixed capital formation" in inv.keywords

    def test_load_algal_bloom(self):
        t = load_topic("algal_bloom_ml")
        assert t.id == "algal_bloom_ml"
        assert len(t.pipelines) == 3
        assert t.scoring.consistency_check is False

    def test_load_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            load_topic("does_not_exist")


class TestListTopics:
    def test_lists_all(self):
        ids = list_topics()
        assert "gfcf_environment" in ids
        assert "algal_bloom_ml" in ids


class TestValidateTopic:
    def test_gfcf_valid(self):
        ok, msg = validate_topic("gfcf_environment")
        assert ok is True
        assert "4 pipelines" in msg

    def test_algal_bloom_valid(self):
        ok, msg = validate_topic("algal_bloom_ml")
        assert ok is True
        assert "3 pipelines" in msg

    def test_invalid_ref_detected(self):
        """A topic with a pipeline referencing a nonexistent keyword set
        should fail validation."""
        import yaml, os
        bad = {
            "id": "test_bad_ref",
            "name": "Bad Ref",
            "keyword_sets": [{"name": "only_set", "keywords": ["a"]}],
            "pipelines": [
                {
                    "name": "p1",
                    "hard_threshold": {"required_sets": ["only_set", "missing_set"]},
                }
            ],
        }
        p = Path(os.environ["LITMON_TOPICS_DIR"]) / "test_bad_ref.yaml"
        try:
            with p.open("w") as f:
                yaml.dump(bad, f)
            ok, msg = validate_topic("test_bad_ref")
            assert ok is False
            assert "missing_set" in msg
        finally:
            p.unlink(missing_ok=True)


class TestTopicToLegacyKeywords:
    def test_produces_valid_legacy_structure(self):
        t = load_topic("gfcf_environment")
        legacy = topic_to_legacy_keywords_dict(t)
        assert "scoring_rules" in legacy
        assert "gfcf_vocabulary" in legacy
        assert "pipelines" in legacy
        assert legacy["scoring_rules"]["title_weight"] == 8
        inv = legacy["gfcf_vocabulary"]["investment_terms"]["keywords_en"]
        assert "GFCF" in inv

    def test_algal_bloom_converts(self):
        t = load_topic("algal_bloom_ml")
        legacy = topic_to_legacy_keywords_dict(t)
        assert len(legacy["pipelines"]) == 3


class TestLoadAllTopics:
    def test_loads_both(self):
        all_topics = load_all_topics()
        assert "gfcf_environment" in all_topics
        assert "algal_bloom_ml" in all_topics
        assert len(all_topics) >= 2
