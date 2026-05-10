"""Pipeline orchestrator for topic-driven paper scoring.

The pipeline replaces the monolithic score_for_request() with a composable
sequence of Stage objects. Each Stage is independently testable.
"""

from .base import Pipeline, PipelineState, Stage

__all__ = ["Pipeline", "PipelineState", "Stage"]
