"""Hard threshold stage — reject papers that don't pass any pipeline's AND condition."""

from __future__ import annotations

import re

from literature_monitor.pipeline.base import PipelineState


class HardThresholdStage:
    """Reject papers that fail ALL pipelines' hard thresholds.

    Each pipeline has required_sets (e.g. [investment_terms, environmental_impact]).
    A paper passes the hard threshold if ANY pipeline's required_sets are ALL matched.
    This is OR between pipelines, AND within each pipeline's required sets.

    Stage name: hard_threshold
    """

    name = "hard_threshold"

    def __init__(
        self,
        keyword_sets: dict[str, list[str]] | None = None,
        pipeline_requirements: list[list[str]] | None = None,
    ) -> None:
        """
        Args:
            keyword_sets: Mapping of set_name -> keyword strings.
            pipeline_requirements: List of pipelines, each is a list of required
                keyword set names. Paper passes if ANY pipeline's sets all match.
                If None, passes all papers through.
        """
        self.keyword_sets = keyword_sets or {}
        self.pipeline_requirements = pipeline_requirements
        # Compile patterns once
        self._compiled: dict[str, list[re.Pattern]] = {
            name: [re.compile(re.escape(kw), re.IGNORECASE) for kw in kws if kw]
            for name, kws in self.keyword_sets.items()
        }

    def run(self, state: PipelineState) -> PipelineState:
        if not self.pipeline_requirements or not self._compiled:
            # No requirements configured — pass through (backward compat)
            return state.with_(passed_hard_threshold=True)

        text = f"{state.title} {state.abstract}"

        for required_sets in self.pipeline_requirements:
            all_matched = True
            for set_name in required_sets:
                patterns = self._compiled.get(set_name, [])
                if not patterns or not any(p.search(text) for p in patterns):
                    all_matched = False
                    break
            if all_matched:
                return state.with_(passed_hard_threshold=True)

        # No pipeline's requirements were fully met
        return state.with_(
            excluded=True,
            exclusion_reason="hard_threshold: no pipeline requirements met",
        )
