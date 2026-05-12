"""Tests for the LLM Review Stage."""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from literature_monitor.pipeline.base import PipelineState
from literature_monitor.pipeline.stages.llm_review import LLMReviewStage


class TestLLMReviewStage:
    """Test LLM review stage behavior."""

    def test_skips_low_scored_papers(self):
        """Should not call LLM for papers below min_level threshold."""
        stage = LLMReviewStage(min_level="MEDIUM", api_key="fake-key")
        state = PipelineState(
            title="Some paper",
            abstract="Some abstract with enough length to pass the check " * 5,
            relevance_level="LOW",
            total_score=10.0,
        )
        result = stage.run(state)
        assert result.total_score == 10.0  # unchanged
        assert stage._calls_made == 0

    def test_skips_papers_without_abstract(self):
        """Should not call LLM for papers with no abstract."""
        stage = LLMReviewStage(min_level="MEDIUM", api_key="fake-key")
        state = PipelineState(
            title="Some paper",
            abstract="",
            relevance_level="MEDIUM",
            total_score=40.0,
        )
        result = stage.run(state)
        assert result.total_score == 40.0  # unchanged

    def test_skips_papers_with_short_abstract(self):
        """Should not call LLM for papers with very short abstract."""
        stage = LLMReviewStage(min_level="MEDIUM", api_key="fake-key")
        state = PipelineState(
            title="Some paper",
            abstract="Short",
            relevance_level="MEDIUM",
            total_score=40.0,
        )
        result = stage.run(state)
        assert result.total_score == 40.0

    def test_skips_when_budget_exceeded(self):
        """Should not call LLM when max_papers reached."""
        stage = LLMReviewStage(max_papers=0, api_key="fake-key")
        state = PipelineState(
            title="Some paper",
            abstract="Some abstract with enough length " * 5,
            relevance_level="MEDIUM",
            total_score=40.0,
        )
        result = stage.run(state)
        assert result.total_score == 40.0

    def test_skips_when_cost_exceeded(self):
        """Should not call LLM when max_cost reached."""
        stage = LLMReviewStage(max_cost_usd=0.0, api_key="fake-key")
        state = PipelineState(
            title="Some paper",
            abstract="Some abstract with enough length " * 5,
            relevance_level="HIGH",
            total_score=80.0,
        )
        result = stage.run(state)
        assert result.total_score == 80.0

    def test_skips_when_no_api_key(self):
        """Should gracefully skip when no API key is available."""
        stage = LLMReviewStage(api_key="")
        state = PipelineState(
            title="Carbon footprint of capital investment",
            abstract="This paper examines embodied carbon emissions in GFCF using MRIO analysis. " * 3,
            relevance_level="MEDIUM",
            total_score=40.0,
        )
        result = stage.run(state)
        assert result.total_score == 40.0  # unchanged

    @patch.object(LLMReviewStage, '_call_llm')
    def test_penalizes_irrelevant_paper(self, mock_call):
        """Should penalize paper judged irrelevant by LLM."""
        mock_call.return_value = {
            "relevant": False,
            "confidence": "high",
            "reason": "This paper is about cement chemistry, not GFCF.",
        }
        stage = LLMReviewStage(api_key="fake-key", penalty_points=100.0)
        state = PipelineState(
            title="Some paper",
            abstract="Some abstract with enough length " * 5,
            relevance_level="MEDIUM",
            total_score=40.0,
            breakdown={},
        )
        result = stage.run(state)
        assert result.total_score == 0.0
        assert result.relevance_level == "LOW"
        assert "llm_review" in result.breakdown
        assert result.breakdown["llm_review"]["relevant"] is False
        assert stage._calls_made == 1

    @patch.object(LLMReviewStage, '_call_llm')
    def test_boosts_relevant_paper(self, mock_call):
        """Should boost paper judged relevant by LLM."""
        mock_call.return_value = {
            "relevant": True,
            "confidence": "high",
            "reason": "Studies embodied carbon in investment via MRIO.",
        }
        stage = LLMReviewStage(api_key="fake-key")
        state = PipelineState(
            title="Carbon emissions embodied in investment",
            abstract="This paper uses MRIO to quantify carbon footprint of GFCF across countries. " * 3,
            relevance_level="MEDIUM",
            total_score=40.0,
            breakdown={},
        )
        result = stage.run(state)
        assert result.total_score == 45.0  # +5 for high confidence
        assert "llm_review" in result.breakdown
        assert result.breakdown["llm_review"]["relevant"] is True

    @patch.object(LLMReviewStage, '_call_llm')
    def test_medium_confidence_boost(self, mock_call):
        """Should apply medium confidence boost."""
        mock_call.return_value = {
            "relevant": True,
            "confidence": "medium",
            "reason": "Likely relevant but unclear methodology.",
        }
        stage = LLMReviewStage(api_key="fake-key")
        state = PipelineState(
            title="Some paper",
            abstract="Some abstract with enough length " * 5,
            relevance_level="MEDIUM",
            total_score=40.0,
            breakdown={},
        )
        result = stage.run(state)
        assert result.total_score == 42.0  # +2 for medium confidence

    @patch.object(LLMReviewStage, '_call_llm')
    def test_low_confidence_no_boost(self, mock_call):
        """Should not boost low confidence results."""
        mock_call.return_value = {
            "relevant": True,
            "confidence": "low",
            "reason": "Possibly relevant.",
        }
        stage = LLMReviewStage(api_key="fake-key")
        state = PipelineState(
            title="Some paper",
            abstract="Some abstract with enough length " * 5,
            relevance_level="MEDIUM",
            total_score=40.0,
            breakdown={},
        )
        result = stage.run(state)
        assert result.total_score == 40.0  # no boost

    def test_prompt_template_formatting(self):
        """Should correctly format prompt template."""
        stage = LLMReviewStage(
            topic_name="Test Topic",
            topic_description="Test description",
            api_key="fake-key",
        )
        # Just verify the template can be formatted
        prompt = stage.prompt_template.format(
            topic_name="Test Topic",
            topic_description="Test description",
            title="Test Title",
            abstract="Test Abstract",
            journal="Test Journal",
            year="2026",
        )
        assert "Test Topic" in prompt
        assert "Test Title" in prompt
        assert "relevant" in prompt.lower()

    @patch.object(LLMReviewStage, '_call_llm')
    def test_tracks_call_count(self, mock_call):
        """Should track number of LLM calls made."""
        mock_call.return_value = {"relevant": True, "confidence": "high", "reason": "ok"}
        stage = LLMReviewStage(api_key="fake-key", max_papers=3)
        state = PipelineState(
            title="Paper",
            abstract="Abstract text " * 10,
            relevance_level="MEDIUM",
            total_score=40.0,
            breakdown={},
        )
        for _ in range(5):
            stage.run(state)
        assert stage._calls_made == 3  # capped at max_papers

    @patch.object(LLMReviewStage, '_call_llm', side_effect=Exception("API down"))
    def test_handles_llm_failure_gracefully(self, mock_call):
        """Should not crash when LLM call fails."""
        stage = LLMReviewStage(api_key="fake-key")
        state = PipelineState(
            title="Paper",
            abstract="Abstract text " * 10,
            relevance_level="MEDIUM",
            total_score=40.0,
            breakdown={},
        )
        result = stage.run(state)
        assert result.total_score == 40.0  # unchanged


class TestLLMReviewWithPipeline:
    """Test LLM review integrated into the full pipeline."""

    def test_llm_stage_name(self):
        stage = LLMReviewStage()
        assert stage.name == "llm_review"

    def test_llm_in_pipeline_stages(self):
        """Verify LLM stage appears in pipeline when topic has llm_review enabled."""
        from literature_monitor.pipeline.engine import build_pipeline
        from literature_monitor.topic.schema import Topic, KeywordSet, PipelineConfig, HardThreshold, LLMReviewConfig, ScoringConfig, LLMReviewConfig

        topic = Topic(
            id="test",
            name="Test",
            keyword_sets=[KeywordSet(name="inv", keywords=["investment"])],
            pipelines=[PipelineConfig(
                name="pipe1",
                hard_threshold=HardThreshold(required_sets=["inv"]),
            )],
            llm_review=LLMReviewConfig(enabled=True, model="gpt-4o-mini"),
        )
        pipeline = build_pipeline(topic=topic, use_legacy=False)
        stage_names = pipeline.describe()
        assert "llm_review" in stage_names

    def test_llm_not_in_pipeline_when_disabled(self):
        """Verify LLM stage does not appear when disabled."""
        from literature_monitor.pipeline.engine import build_pipeline
        from literature_monitor.topic.schema import Topic, KeywordSet, PipelineConfig, HardThreshold, LLMReviewConfig

        topic = Topic(
            id="test",
            name="Test",
            keyword_sets=[KeywordSet(name="inv", keywords=["investment"])],
            pipelines=[PipelineConfig(
                name="pipe1",
                hard_threshold=HardThreshold(required_sets=["inv"]),
            )],
            llm_review=LLMReviewConfig(enabled=False),
        )
        pipeline = build_pipeline(topic=topic, use_legacy=False)
        stage_names = pipeline.describe()
        assert "llm_review" not in stage_names


class TestC1C2Fixes:
    """Tests for Critical bug fixes C1 and C2."""

    def test_c1_llm_runs_after_final_scoring(self):
        """C1: LLM review stage must come AFTER final_scoring in the pipeline."""
        from literature_monitor.pipeline.engine import build_pipeline
        from literature_monitor.topic.schema import (
            Topic, KeywordSet, PipelineConfig, HardThreshold, LLMReviewConfig
        )

        topic = Topic(
            id="test",
            name="Test",
            keyword_sets=[KeywordSet(name="inv", keywords=["investment"])],
            pipelines=[PipelineConfig(
                name="pipe1",
                hard_threshold=HardThreshold(required_sets=["inv"]),
            )],
            llm_review=LLMReviewConfig(enabled=True, model="gpt-4o-mini"),
        )
        pipeline = build_pipeline(topic=topic, use_legacy=True)
        names = pipeline.describe()

        # LLM review must be AFTER final_scoring
        llm_idx = names.index("llm_review")
        final_idx = names.index("final_scoring")
        assert llm_idx > final_idx, (
            f"llm_review at position {llm_idx} should be after "
            f"final_scoring at position {final_idx}"
        )

    def test_c2_budget_persists_across_pipeline_instances(self):
        """C2: LLM stage's budget counter persists across multiple pipeline.run() calls."""
        from unittest.mock import patch
        from literature_monitor.pipeline.base import PipelineState
        from literature_monitor.pipeline.stages.llm_review import LLMReviewStage

        # Create a minimal pipeline: just FinalScoring + LLMReview
        from literature_monitor.pipeline.stages.final_scoring import FinalScoringStage
        from literature_monitor.pipeline.base import Pipeline

        llm_stage = LLMReviewStage(
            api_key="fake-key",
            max_papers=2,
            min_level="MEDIUM",
        )
        final_stage = FinalScoringStage()
        pipeline = Pipeline([final_stage, llm_stage])

        with patch.object(llm_stage, '_call_llm', return_value={
            "relevant": True, "confidence": "high", "reason": "ok"
        }):
            # Run 5 papers through the SAME pipeline instance
            for i in range(5):
                state = PipelineState(
                    title="Investment in carbon footprint of capital formation",
                    abstract="This paper examines embodied carbon emissions in gross fixed capital formation. " * 5,
                    year="2026",
                    rule_score=80.0,  # HIGH score
                    text_score=0.0,
                    recency_score=6.0,
                    journal_score=0.0,
                    breakdown={"legacy_priority": "HIGH", "legacy_score": 80},
                )
                result = pipeline.run(state)

            # Budget should be shared: only 2 out of 5 should have been reviewed
            assert llm_stage._calls_made == 2, (
                f"Expected 2 LLM calls (max_papers=2), got {llm_stage._calls_made}"
            )

    def test_c2_run_single_paper_still_works(self):
        """C2: Single paper via pipeline.run() works with LLM budget tracking."""
        from unittest.mock import patch
        from literature_monitor.pipeline.base import PipelineState, Pipeline
        from literature_monitor.pipeline.stages.llm_review import LLMReviewStage
        from literature_monitor.pipeline.stages.final_scoring import FinalScoringStage

        llm_stage = LLMReviewStage(
            api_key="fake-key",
            max_papers=1,
            min_level="MEDIUM",
        )
        final_stage = FinalScoringStage()
        pipeline = Pipeline([final_stage, llm_stage])

        with patch.object(llm_stage, '_call_llm', return_value={
            "relevant": False, "confidence": "high", "reason": "not relevant"
        }):
            state = PipelineState(
                title="Investment in carbon footprint of capital formation",
                abstract="This paper examines embodied carbon emissions in gross fixed capital formation. " * 5,
                year="2026",
                rule_score=80.0,
                text_score=0.0,
                recency_score=6.0,
                journal_score=0.0,
                breakdown={"legacy_priority": "HIGH", "legacy_score": 80},
            )
            result = pipeline.run(state)

            assert llm_stage._calls_made == 1
            # Paper judged irrelevant → penalized
            assert result.relevance_level == "LOW"
