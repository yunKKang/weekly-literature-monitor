"""Recency boost stage — newer papers get higher scores.

Formula: max(0.0, 8.0 - log1p(age_days)) — same as legacy.

Stage name: recency_boost
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from literature_monitor.pipeline.base import PipelineState


class RecencyBoostStage:
    """Score newer papers higher.

    Stage name: recency_boost
    """

    name = "recency_boost"

    def run(self, state: PipelineState) -> PipelineState:
        value = state.publication_date or state.year
        if not value:
            return state

        try:
            if len(value) == 4:
                published = datetime(int(value), 1, 1, tzinfo=timezone.utc)
            else:
                published = datetime.strptime(value[:10], "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
        except (ValueError, TypeError):
            return state

        age_days = max((datetime.now(timezone.utc) - published).days, 0)
        recency_score = max(0.0, 8.0 - math.log1p(age_days))

        breakdown = dict(state.breakdown)
        breakdown["recency_score"] = recency_score
        return state.with_(recency_score=recency_score, breakdown=breakdown)
