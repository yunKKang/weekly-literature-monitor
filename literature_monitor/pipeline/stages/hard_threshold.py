"""Hard threshold stage — reject papers that don't pass investment + domain AND condition."""

from __future__ import annotations

import re
from typing import Any

from literature_monitor.pipeline.base import PipelineState


class HardThresholdStage:
    """Reject papers that fail the hard threshold (investment AND domain).

    This is the first stage in the pipeline. If a paper doesn't match
    at least one investment term AND one domain term, it's excluded.

    In the new Pipeline system, this stage uses Topic keyword_sets
    directly instead of the legacy KeywordConfig approach.

    For now, this is a STUB that always passes (letting the legacy
    scoring stage handle filtering). Once we fully migrate scoring
    logic from relevance_filter.py, this will do the actual work.

    Stage name: hard_threshold
    """

    name = "hard_threshold"

    def __init__(self, keyword_sets: dict[str, list[str]] | None = None) -> None:
        """
        Args:
            keyword_sets: Mapping of set_name -> keyword patterns.
                          If None, passes all papers through (legacy compat).
        """
        self.keyword_sets = keyword_sets
        self._compiled: dict[str, list[re.Pattern]] | None = None
        if keyword_sets:
            self._compiled = {
                name: [re.compile(re.escape(kw), re.IGNORECASE) for kw in kws]
                for name, kws in keyword_sets.items()
            }

    def run(self, state: PipelineState) -> PipelineState:
        if self._compiled is None:
            # Legacy compat — no keyword sets configured, pass through
            return state.with_(passed_hard_threshold=True)

        text = f"{state.title} {state.abstract}"
        matched_sets: list[str] = []
        for set_name, patterns in self._compiled.items():
            if any(p.search(text) for p in patterns):
                matched_sets.append(set_name)

        # All configured sets must have at least one match
        required = set(self._compiled.keys())
        matched = set(matched_sets)
        if not required.issubset(matched):
            missing = required - matched
            return state.with_(
                excluded=True,
                exclusion_reason=f"hard_threshold: missing matches in {missing}",
            )

        return state.with_(passed_hard_threshold=True)
