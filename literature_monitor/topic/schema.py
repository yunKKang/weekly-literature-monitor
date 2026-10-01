"""Topic — first-class abstraction for isolated research domains.

Each topic is a self-contained research configuration: its own keywords,
journal pools, scoring rules, and export targets. Topics are defined in YAML
files under topics/ and validated by Pydantic schemas at load time.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DateRange(BaseModel):
    """Date range for a search or monitoring window."""

    date_from: str = Field(..., description="Start date YYYY-MM-DD")
    date_to: str | None = Field(None, description="End date YYYY-MM-DD, null=today")

    @field_validator("date_from", "date_to")
    @classmethod
    def _check_date_format(cls, v: str | None) -> str | None:
        if v is not None and not _ISO_DATE.match(v):
            raise ValueError(f"Date must be YYYY-MM-DD, got: {v!r}")
        return v


class KeywordSet(BaseModel):
    """A named set of keywords for matching against paper titles/abstracts.

    Can be defined inline in the topic YAML or referenced from shared/keyword_libs/.
    """

    name: str
    description: str = ""
    keywords: list[str] = Field(default_factory=list, description="Primary keywords")
    synonyms: list[str] = Field(default_factory=list, description="Synonym expansions")
    negative_keywords: list[str] = Field(
        default_factory=list, description="Exclusion terms"
    )


class HardThreshold(BaseModel):
    """AND/OR condition for paper filtering.

    required_sets is a list of AND groups. Each AND group can be:
    - A string: must match this keyword set
    - A list of strings: must match at least one (OR within group)

    Example: [["investment_terms", "capital_stock_terms"], "environmental_impact"]
    Means: (investment_terms OR capital_stock_terms) AND environmental_impact
    """

    required_sets: list[str | list[str]] = Field(
        ...,
        description="AND groups. Each element is a keyword set name (string) "
        "or a list of names (OR: match at least one).",
    )


class PipelineConfig(BaseModel):
    """A single pipeline within a topic — defines filtering and scoring rules."""

    name: str = Field(..., description="Human-readable pipeline name")
    description: str = ""
    priority: int = Field(1, description="Lower = higher priority")
    hard_threshold: HardThreshold
    keyword_sets: list[str] = Field(
        default_factory=list,
        description="Keyword set names to use for scoring (beyond threshold).",
    )
    bonus_terms: list[str] = Field(
        default_factory=list, description="Extra scoring terms (+3 each, cap 6)"
    )
    negative_keywords: list[str] = Field(
        default_factory=list, description="Per-pipeline exclusions"
    )


class ScoringConfig(BaseModel):
    """Scoring weights and thresholds — overridable per topic."""

    title_weight: int = Field(8, description="Weight for title matches")
    abstract_weight: int = Field(5, description="Weight for abstract matches")
    high_threshold: int = Field(20, description="Score ≥ this → HIGH priority")
    medium_threshold: int = Field(12, description="Score ≥ this → MEDIUM priority")
    negative_penalty: int = Field(
        20, description="Points subtracted per negative keyword hit"
    )
    consistency_check: bool = Field(
        True, description="Require asset/methodology match for HIGH priority"
    )


class ExporterConfig(BaseModel):
    """Configuration for an output exporter."""

    type: str = Field(
        ...,
        description="Exporter type: markdown, csv, bibtex, github_issue, obsidian",
    )
    enabled: bool = True
    options: dict = Field(default_factory=dict, description="Exporter-specific options")


class LLMReviewConfig(BaseModel):
    """Optional LLM second-pass review configuration."""

    enabled: bool = False
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    base_url: str = Field(
        "",
        description="Custom API base URL for OpenAI-compatible providers. "
        "Leave empty for the provider default.",
    )
    two_stage: bool = Field(
        True,
        description="Use two-stage LLM filtering: Stage 1 (title-only fast filter) "
        "then Stage 2 (abstract deep judge). Saves cost by skipping abstract "
        "analysis for clearly irrelevant papers.",
    )
    enabled_for_priorities: list[str] = Field(
        default_factory=lambda: ["HIGH", "MEDIUM"],
        description="Only run LLM review on papers with these priorities",
    )
    max_papers_per_run: int = 50
    max_cost_usd: float = 5.0
    prompt_template: str = ""
    concurrency: int = 3


class Topic(BaseModel):
    """A complete research topic configuration.

    Each topic is an isolated research domain with its own keywords, pipelines,
    journal pools, and scoring rules. Defined in topics/<id>.yaml.
    """

    id: str = Field(
        ...,
        description="Unique topic identifier (matches filename without .yaml)",
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    name: str = Field(..., description="Human-readable topic name")
    description: str = ""
    version: str = "1.0"

    # --- Search scope ---
    date_range: DateRange = Field(
        default_factory=lambda: DateRange(date_from="2020-01-01")
    )
    journal_pool_ids: list[str] = Field(
        default_factory=list,
        description="Pool IDs from shared/journals/ to include",
    )
    journal_issns: list[str] = Field(
        default_factory=list,
        description="Additional explicit ISSNs not in any pool",
    )
    include_conferences: bool = Field(
        False,
        description="Include conference paper fetching (Crossref container-title search)",
    )

    # --- Keyword definitions ---
    keyword_sets: list[KeywordSet] = Field(
        ..., description="Named keyword sets used by pipelines"
    )

    # --- Scoring pipelines ---
    pipelines: list[PipelineConfig] = Field(
        ..., description="Ordered list of scoring pipelines"
    )
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)

    # --- LLM review (optional) ---
    llm_review: LLMReviewConfig = Field(default_factory=LLMReviewConfig)

    # --- Export ---
    exporters: list[ExporterConfig] = Field(default_factory=list)

    # --- Metadata ---
    benchmark_dois: list[str] = Field(default_factory=list, description="DOIs of benchmark papers for recall verification")
    created_at: str = ""
    updated_at: str = ""
    author: str = ""
    tags: list[str] = Field(default_factory=list)
