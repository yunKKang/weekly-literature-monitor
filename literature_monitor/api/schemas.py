"""API schemas with a small fallback when pydantic is unavailable."""

from __future__ import annotations

try:
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover - fallback for core-only test envs
    class BaseModel:  # type: ignore
        def __init__(self, **data):
            for key, value in data.items():
                setattr(self, key, value)

        def model_dump(self):
            return dict(self.__dict__)

    def Field(default_factory=None, default=None):  # type: ignore
        return default_factory() if default_factory else default


class SearchRunCreate(BaseModel):
    date_from: str
    date_to: str
    keywords: list[str] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)
    journal_pool_ids: list[str] = Field(default_factory=list)
    journal_issns: list[str] = Field(default_factory=list)
    include_conferences: bool = False
    min_score: float = 0
    max_results_per_source: int = 10000
