"""Keyword match stage — dynamic keyword hits from user query terms.

Replicates collect_keyword_hits() from scoring.py: matches each query
keyword against title/abstract/journal/topic with weighted scoring.

Stage name: keyword_match
"""

from __future__ import annotations

import re

from literature_monitor.pipeline.base import PipelineState


_FIELD_WEIGHTS = {
    "title": 12,
    "abstract": 7,
    "journal": 4,
    "topic": 5,
}


class KeywordMatchStage:
    """Match user query keywords against paper fields with weighted scoring.

    Replicates the collect_keyword_hits() function from core/scoring.py.
    Each keyword hit adds weight points: title=12, abstract=7, journal=4, topic=5.

    Stage name: keyword_match
    """

    name = "keyword_match"

    def __init__(self, query_terms: list[str] | None = None) -> None:
        self.query_terms = query_terms or []

    def run(self, state: PipelineState) -> PipelineState:
        if not self.query_terms:
            return state

        from literature_monitor.core.models import KeywordHit
        from literature_monitor.core.scoring import snippet as _snippet

        fields = {
            "title": state.title,
            "abstract": state.abstract,
            "journal": state.journal,
            "topic": " ".join(state.topics),
        }

        hits: list[KeywordHit] = []
        for keyword in self.query_terms:
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            for field, text in fields.items():
                if not text or not pattern.search(text):
                    continue
                weight = _FIELD_WEIGHTS[field]
                hits.append(KeywordHit(
                    keyword=keyword,
                    field=field,
                    hit_type="user_keyword",
                    weight=weight,
                    snippet=_snippet(text, keyword),
                ))

        dynamic_score = sum(hit.weight for hit in hits)

        breakdown = dict(state.breakdown)
        breakdown["dynamic_keyword_score"] = dynamic_score

        all_hits = list(state.keyword_hits) + hits
        all_kw = set(state.matched_keywords) | {h.keyword for h in hits}

        return state.with_(
            rule_score=state.rule_score + dynamic_score,
            keyword_hits=tuple(all_hits),
            matched_keywords=tuple(all_kw),
            breakdown=breakdown,
        )
