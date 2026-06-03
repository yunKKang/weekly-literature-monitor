"""Negative keyword penalty stage.

Each negative keyword hit costs 20 points (configurable).

Stage name: negative_filter
"""

from __future__ import annotations

from literature_monitor.pipeline.base import PipelineState


class NegativeFilterStage:
    """Subtract points for each negative keyword match.

    Stage name: negative_filter
    """

    name = "negative_filter"

    def __init__(
        self,
        negative_keywords: list[str],
        penalty_per_hit: float = 20.0,
    ) -> None:
        self.negative_keywords = negative_keywords
        self.penalty_per_hit = penalty_per_hit

    def run(self, state: PipelineState) -> PipelineState:
        if not self.negative_keywords:
            return state

        combined = " ".join(
            [state.title, state.abstract, state.journal, " ".join(state.topics)]
        ).lower()

        penalty = sum(
            self.penalty_per_hit
            for kw in self.negative_keywords
            if kw and kw.lower() in combined
        )

        if penalty > 0:
            breakdown = dict(state.breakdown)
            breakdown["negative_penalty"] = penalty
            return state.with_(breakdown=breakdown)

        return state
