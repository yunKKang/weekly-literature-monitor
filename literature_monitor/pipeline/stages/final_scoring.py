"""Final scoring stage — combine all stage outputs into total score + level.

This stage must run LAST in the pipeline. It reads all accumulated scores
and computes the final total and relevance level.

Stage name: final_scoring
"""

from __future__ import annotations

from literature_monitor.pipeline.base import PipelineState


LEVEL_HIGH = 70
LEVEL_MEDIUM = 35


class FinalScoringStage:
    """Compute total_score and relevance_level from all accumulated scores.

    total = rule_score * 1.0 + text_score * text_weight + recency + journal - negative
    level = HIGH if score >= 70 or legacy_priority == HIGH, else MEDIUM if >= 35, else LOW

    Stage name: final_scoring
    """

    name = "final_scoring"

    def __init__(
        self,
        rule_weight: float = 1.0,
        text_weight: float = 0.6,
        legacy_high_threshold: int = 20,
        legacy_medium_threshold: int = 12,
    ) -> None:
        self.rule_weight = rule_weight
        self.text_weight = text_weight
        self.legacy_high_threshold = legacy_high_threshold
        self.legacy_medium_threshold = legacy_medium_threshold

    def run(self, state: PipelineState) -> PipelineState:
        breakdown = dict(state.breakdown)
        negative_penalty = breakdown.get("negative_penalty", 0.0)

        total = (
            state.rule_score * self.rule_weight
            + state.text_score * self.text_weight
            + state.recency_score
            + state.journal_score
            - negative_penalty
        )
        total = max(0.0, total)

        # Determine level
        legacy_priority = breakdown.get("legacy_priority", "LOW")
        legacy_score = breakdown.get("legacy_score", 0)

        if legacy_priority == "HIGH" or legacy_score >= self.legacy_high_threshold:
            level = "HIGH"
        elif legacy_priority == "MEDIUM" or legacy_score >= self.legacy_medium_threshold:
            level = "MEDIUM"
        elif total >= LEVEL_HIGH:
            level = "HIGH"
        elif total >= LEVEL_MEDIUM:
            level = "MEDIUM"
        else:
            level = "LOW"

        breakdown["total_score_raw"] = total
        return state.with_(
            total_score=round(total, 3),
            relevance_level=level,
            breakdown=breakdown,
        )
