"""Base types for the scoring pipeline.

Design:
- PipelineState: immutable data bag passed through each stage.
  Each stage reads from it, enriches it, and returns a new instance.
- Stage: Protocol — any object with run(state) -> PipelineState.
- Pipeline: orchestrator that chains stages in order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class PipelineState:
    """Immutable state passed through each scoring stage.

    Every stage receives this and returns a new PipelineState with its
    contributions added. Frozen=True ensures no accidental mutation.
    """

    # --- Input (immutable across stages) ---
    title: str = ""
    abstract: str = ""
    journal: str = ""
    topics: tuple[str, ...] = ()
    doi: str = ""
    year: str | None = None
    publication_date: str | None = None
    issn: str | None = None
    paper_id: int = 0

    # --- Accumulated scores (stages add to these) ---
    rule_score: float = 0.0
    text_score: float = 0.0
    recency_score: float = 0.0
    journal_score: float = 0.0
    total_score: float = 0.0

    # --- Accumulated metadata (stages add to these) ---
    relevance_level: str = "LOW"
    matched_pipelines: tuple[str, ...] = ()
    matched_keywords: tuple[str, ...] = ()
    keyword_hits: tuple[Any, ...] = ()
    breakdown: dict[str, Any] = field(default_factory=dict)

    # --- Stage gate flags ---
    passed_hard_threshold: bool = False
    excluded: bool = False
    exclusion_reason: str | None = None

    # --- Stage control ---
    skipped_stages: tuple[str, ...] = ()

    def with_(self, **kwargs: Any) -> PipelineState:
        """Return a new state with the given fields updated."""
        from dataclasses import replace
        return replace(self, **kwargs)


@runtime_checkable
class Stage(Protocol):
    """Protocol for a scoring pipeline stage.

    Each stage:
    1. Receives a PipelineState (read-only)
    2. Computes its contribution
    3. Returns a new PipelineState with fields updated

    Stages are identified by their 'name' attribute for logging/debugging.
    """

    name: str

    def run(self, state: PipelineState) -> PipelineState:
        """Execute this stage and return the enriched state."""
        ...


class Pipeline:
    """Orchestrator that chains stages in order.

    Usage:
        pipeline = Pipeline([stage1, stage2, stage3])
        result = pipeline.run(initial_state)

    For multi-paper processing with shared budget state (e.g., LLM review),
    use run_batch() which reuses the same stage instances across all papers.
    """

    def __init__(self, stages: list[Stage]) -> None:
        self.stages = stages

    def run(self, state: PipelineState) -> PipelineState:
        """Execute all stages in order, skipping any in state.skipped_stages."""
        for stage in self.stages:
            if state.excluded:
                break
            if stage.name in state.skipped_stages:
                continue
            state = stage.run(state)
        return state

    def run_batch(self, states: list[PipelineState]) -> list[PipelineState]:
        """Run all states through the pipeline, reusing stage instances.

        This preserves budget state (e.g., LLM call counts) across all papers.
        Use this instead of calling run() in a loop when stages have
        per-session state like LLM budget counters.
        """
        results: list[PipelineState] = []
        for state in states:
            results.append(self.run(state))
        return results

    def describe(self) -> list[str]:
        """Return the ordered list of stage names."""
        return [s.name for s in self.stages]
