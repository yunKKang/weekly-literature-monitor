"""Rule scoring stage — wraps legacy relevance_filter.score_paper().

This stage is the BRANCH BY ABSTRACTION layer: it delegates to the existing
legacy scoring code so we can run old and new pipelines in parallel without
changing the scoring logic itself.

Stage name: rule_scoring
"""

from __future__ import annotations

from typing import Any

from literature_monitor.pipeline.base import PipelineState


class RuleScoringStage:
    """Apply legacy rule-based scoring via relevance_filter.

    This stage calls the existing score_paper() function and maps its
    output into PipelineState fields. By wrapping the legacy code in a
    Stage, we can replace it incrementally without breaking anything.

    Stage name: rule_scoring
    """

    name = "rule_scoring"

    def __init__(
        self,
        keyword_config_path: str | None = None,
        *,
        use_legacy: bool = True,
        journal_pool_ids: list[str] | None = None,
    ) -> None:
        self.keyword_config_path = keyword_config_path
        self.use_legacy = use_legacy
        self.journal_pool_ids = journal_pool_ids or []
        self._kw_config = None  # cached lazy-loaded config

    def _should_apply_legacy_gfcf(self) -> bool:
        """Check if journal_pool_ids contain any legacy pool markers."""
        if not self.journal_pool_ids:
            return False
        legacy_pool_markers = (
            "main_pool",
            "gfcf",
            "sna",
            "capital",
            "built_environment",
            "digital",
        )
        return any(
            any(marker in pool_id for marker in legacy_pool_markers)
            for pool_id in self.journal_pool_ids
        )

    def _get_kw_config(self):
        """Lazy-load and cache keyword config."""
        if self._kw_config is None:
            from literature_monitor.config import CONFIG_DIR
            from literature_monitor.core.relevance_filter import load_keyword_config

            from pathlib import Path
            config_path = self.keyword_config_path or str(Path(__file__).resolve().parent.parent.parent.parent / "config" / "keywords.json")
            self._kw_config = load_keyword_config(Path(config_path))
        return self._kw_config

    def run(self, state: PipelineState) -> PipelineState:
        if not self.use_legacy:
            return state

        from literature_monitor.core.relevance_filter import score_paper

        kw_config = self._get_kw_config()
        result = score_paper(state.title, state.abstract, kw_config)

        # Zero out legacy result if not using legacy GFCF scoring
        if not self._should_apply_legacy_gfcf():
            result.score = 0
            result.priority = "LOW"
            result.matched_keywords = []
            result.matched_pipelines = []
            result.matched_asset_types = []
            result.matched_themes = []
            result.exclusion_reason = None
            result.negative_matches = []

        # Map legacy RelevanceResult into PipelineState
        matched_kw = list(result.matched_keywords or [])
        matched_pipes = list(result.matched_pipelines or [])

        # Build keyword hits from legacy matched terms
        hits = []
        text_fields = {
            "title": state.title,
            "abstract": state.abstract,
            "journal": state.journal,
            "topic": " ".join(state.topics),
        }
        for keyword in matched_kw:
            for field, text in text_fields.items():
                if keyword and keyword.lower() in text.lower():
                    from literature_monitor.core.models import KeywordHit
                    from literature_monitor.core.scoring import snippet
                    hits.append(KeywordHit(
                        keyword=keyword,
                        field=field,
                        hit_type="pipeline_keyword",
                        weight=2,
                        snippet=snippet(text, keyword),
                    ))
                    break

        breakdown = dict(state.breakdown)
        breakdown.update({
            "legacy_score": result.score,
            "legacy_priority": result.priority,
            "matched_asset_types": result.matched_asset_types,
            "matched_themes": result.matched_themes,
            "exclusion_reason": result.exclusion_reason,
            "negative_matches": result.negative_matches,
        })

        return state.with_(
            rule_score=float(result.score),
            matched_pipelines=tuple(matched_pipes),
            matched_keywords=tuple(set(state.matched_keywords) | set(matched_kw)),
            keyword_hits=tuple(list(state.keyword_hits) + hits),
            excluded=result.is_excluded,
            exclusion_reason=result.exclusion_reason,
            breakdown=breakdown,
        )
