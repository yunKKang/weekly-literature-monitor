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
    ) -> None:
        self.keyword_config_path = keyword_config_path
        self.use_legacy = use_legacy

    def run(self, state: PipelineState) -> PipelineState:
        if not self.use_legacy:
            return state

        # Lazy import to avoid pulling in compat.py at module load time
        from literature_monitor.compat import CONFIG_DIR
        from relevance_filter import load_keyword_config, score_paper

        config_path = self.keyword_config_path or str(CONFIG_DIR / "keywords.json")
        from pathlib import Path
        kw_config = load_keyword_config(Path(config_path))

        result = score_paper(state.title, state.abstract, kw_config)

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
                    hits.append(KeywordHit(
                        keyword=keyword,
                        field=field,
                        hit_type="pipeline_keyword",
                        weight=2,
                        snippet=_snippet(text, keyword),
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


def _snippet(text: str, keyword: str, radius: int = 70) -> str:
    lower = text.lower()
    idx = lower.find(keyword.lower())
    if idx < 0:
        return text[: radius * 2].strip()
    start = max(0, idx - radius)
    end = min(len(text), idx + len(keyword) + radius)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return f"{prefix}{text[start:end].strip()}{suffix}"
