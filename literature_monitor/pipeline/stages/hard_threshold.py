"""Hard threshold stage — reject papers that don't pass any pipeline's AND/OR condition."""

from __future__ import annotations

import re

from literature_monitor.pipeline.base import PipelineState


class HardThresholdStage:
    """Reject papers that fail ALL pipelines' hard thresholds.

    Each pipeline has required_sets (AND groups). Each AND group can be:
    - A string: must match this keyword set
    - A list of strings: must match at least one (OR within group)

    A paper passes if ANY pipeline's AND groups are all satisfied.

    Stage name: hard_threshold
    """

    name = "hard_threshold"

    def __init__(
        self,
        keyword_sets: dict[str, list[str]] | None = None,
        pipeline_requirements: list[list[str | list[str]]] | None = None,
    ) -> None:
        self.keyword_sets = keyword_sets or {}
        self.pipeline_requirements = pipeline_requirements
        self._compiled: dict[str, list[re.Pattern]] = {
            name: [re.compile(re.escape(kw), re.IGNORECASE) for kw in kws if kw]
            for name, kws in self.keyword_sets.items()
        }

    def _check_and_group(self, text: str, group: str | list[str]) -> bool:
        """Check if an AND group is satisfied.

        - str: must match this keyword set
        - list[str]: must match at least one (OR)
        """
        if isinstance(group, str):
            patterns = self._compiled.get(group, [])
            return bool(patterns) and any(p.search(text) for p in patterns)
        elif isinstance(group, list):
            for set_name in group:
                patterns = self._compiled.get(set_name, [])
                if patterns and any(p.search(text) for p in patterns):
                    return True
            return False
        return False

    def run(self, state: PipelineState) -> PipelineState:
        if not self.pipeline_requirements or not self._compiled:
            return state.with_(passed_hard_threshold=True)

        text = f"{state.title} {state.abstract}"

        for required_sets in self.pipeline_requirements:
            all_matched = True
            for group in required_sets:
                if not self._check_and_group(text, group):
                    all_matched = False
                    break
            if all_matched:
                return state.with_(passed_hard_threshold=True)

        return state.with_(
            excluded=True,
            exclusion_reason="hard_threshold: no pipeline requirements met",
        )
