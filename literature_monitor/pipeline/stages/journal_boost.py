"""Journal boost stage — papers in selected journals get +5.

Stage name: journal_boost
"""

from __future__ import annotations

from literature_monitor.pipeline.base import PipelineState


class JournalBoostStage:
    """+5 points if the paper's ISSN is in the selected set.

    Stage name: journal_boost
    """

    name = "journal_boost"

    def __init__(self, selected_issns: set[str], bonus: float = 5.0) -> None:
        self.selected_issns = selected_issns
        self.bonus = bonus

    def run(self, state: PipelineState) -> PipelineState:
        journal_score = self.bonus if state.issn and state.issn in self.selected_issns else 0.0
        breakdown = dict(state.breakdown)
        breakdown["journal_score"] = journal_score
        return state.with_(journal_score=journal_score, breakdown=breakdown)
