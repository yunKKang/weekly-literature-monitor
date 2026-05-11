"""LLM Review Stage — second-pass relevance judgment using LLM.

This stage sends paper abstracts to an LLM and asks: "Is this paper really
about the environmental impacts of gross fixed capital formation (GFCF)?
Does it use MRIO/EEIO methods, analyze capital stock lifecycle impacts,
or quantify embodied emissions in investment?"

The LLM acts as a final filter to remove false positives that passed
the rule-based scoring but are semantically off-topic.

Stage name: llm_review
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from literature_monitor.pipeline.base import PipelineState

logger = logging.getLogger(__name__)

# Default prompt template for GFCF research relevance judgment
DEFAULT_PROMPT = """You are a research assistant specializing in industrial ecology,
environmental economics, and input-output analysis. Your task is to evaluate whether
a paper is genuinely relevant to the research topic: "{topic_name}".

{topic_description}

## Relevance Criteria

A paper is RELEVANT if it:
- Studies the environmental impacts (carbon footprint, material footprint, water footprint,
  land use, biodiversity, energy) of capital formation, investment, or fixed assets
- Uses multi-regional input-output (MRIO) or environmentally extended input-output (EEIO)
  methods to analyze investment-related emissions or resource use
- Examines the lifecycle environmental impacts of infrastructure, buildings, machinery,
  or other capital stock
- Analyzes embodied emissions or resource use in capital goods trade
- Studies decarbonization pathways related to capital investment decisions
- Quantifies the environmental footprint of GDP components related to investment
- Compares capital formation environmental impacts across countries or development stages

A paper is NOT RELEVANT if it:
- Uses "investment" only in financial/portfolio sense (stocks, bonds, ESG investing)
- Uses "capital" only in human capital, social capital, or natural capital (not manufactured capital)
- Uses "carbon" only in chemistry, biology, or atmospheric science contexts
- Uses "construction" only in materials science (cement chemistry, not building lifecycle)
- Is about biological "investment in offspring" or ecological "capital" in ecosystems
- Is purely about operational emissions (not embodied/capital-related)
- Uses MRIO/EEIO but not for capital/investment analysis

## Paper to Evaluate

Title: {title}
Abstract: {abstract}
Journal: {journal}
Year: {year}

## Output Format

Respond in JSON:
{{
  "relevant": true/false,
  "confidence": "high"/"medium"/"low",
  "reason": "<one sentence explaining the judgment>",
  "topics": ["<list of matching research themes if relevant>"]
}}
"""


class LLMReviewStage:
    """Second-pass relevance judgment using LLM.

    Only runs on papers above min_level threshold (default: MEDIUM).
    Sends title + abstract to LLM with a domain-specific prompt.
    Papers judged irrelevant get a large score penalty.

    Stage name: llm_review
    """

    name = "llm_review"

    def __init__(
        self,
        topic_name: str = "GFCF Environmental Impact",
        topic_description: str = "",
        provider: str = "openai",
        model: str = "gpt-4o-mini",
        min_level: str = "MEDIUM",
        max_papers: int = 50,
        max_cost_usd: float = 5.0,
        penalty_points: float = 100.0,
        prompt_template: str | None = None,
        api_key: str | None = None,
        concurrency: int = 3,
    ) -> None:
        self.topic_name = topic_name
        self.topic_description = topic_description or (
            f"The research topic is: {topic_name}. "
            "Evaluate whether this paper genuinely contributes to understanding "
            "the environmental impacts of capital formation and investment."
        )
        self.provider = provider
        self.model = model
        self.min_level = min_level
        self.max_papers = max_papers
        self.max_cost_usd = max_cost_usd
        self.penalty_points = penalty_points
        self.prompt_template = prompt_template or DEFAULT_PROMPT
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.concurrency = concurrency
        self._calls_made = 0
        self._total_cost = 0.0

    def run(self, state: PipelineState) -> PipelineState:
        """Run LLM review if paper meets threshold and budget allows."""
        # Check budget
        if self._calls_made >= self.max_papers:
            return state
        if self._total_cost >= self.max_cost_usd:
            return state

        # Check relevance level threshold
        level_order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        if level_order.get(state.relevance_level, 0) < level_order.get(self.min_level, 0):
            return state

        # Check if abstract is available
        if not state.abstract or len(state.abstract) < 50:
            return state

        # Call LLM (catch any exception — never crash the pipeline)
        try:
            result = self._call_llm(state)
        except Exception as e:
            logger.warning("LLM review failed: %s", e)
            return state
        if not result:
            return state
        self._calls_made += 1

        # Apply LLM judgment
        relevant = result.get("relevant", True)
        confidence = result.get("confidence", "low")
        reason = result.get("reason", "")

        breakdown = dict(state.breakdown)
        breakdown["llm_review"] = {
            "relevant": relevant,
            "confidence": confidence,
            "reason": reason,
            "model": self.model,
        }

        if not relevant:
            # Penalize: paper judged irrelevant by LLM
            return state.with_(
                total_score=max(0.0, state.total_score - self.penalty_points),
                relevance_level="LOW",
                breakdown=breakdown,
            )

        # Relevant: add LLM reasoning to breakdown, boost score slightly
        boost = 5.0 if confidence == "high" else 2.0 if confidence == "medium" else 0.0
        return state.with_(
            total_score=round(state.total_score + boost, 3),
            breakdown=breakdown,
        )

    def _call_llm(self, state: PipelineState) -> dict[str, Any] | None:
        """Call LLM API to judge paper relevance."""
        prompt = self.prompt_template.format(
            topic_name=self.topic_name,
            topic_description=self.topic_description,
            title=state.title,
            abstract=state.abstract[:2000],
            journal=state.journal,
            year=state.year or "unknown",
        )

        try:
            if self.provider == "openai":
                return self._call_openai(prompt)
            elif self.provider == "anthropic":
                return self._call_anthropic(prompt)
            else:
                logger.warning("Unknown LLM provider: %s", self.provider)
                return None
        except Exception as e:
            logger.warning("LLM review failed: %s", e)
            return None

    def _call_openai(self, prompt: str) -> dict[str, Any] | None:
        """Call OpenAI API."""
        import urllib.request
        import urllib.error

        api_key = self.api_key
        if not api_key:
            logger.warning("No OpenAI API key — skipping LLM review")
            return None

        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": 300,
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            logger.warning("OpenAI API error: %s", e.code)
            return None

        # Estimate cost (rough: ~$0.15/1M input tokens for gpt-4o-mini)
        usage = data.get("usage", {})
        total_tokens = usage.get("total_tokens", 500)
        self._total_cost += total_tokens * 0.15 / 1_000_000

        content = data["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.warning("LLM returned non-JSON: %s", content[:100])
            return None

    def _call_anthropic(self, prompt: str) -> dict[str, Any] | None:
        """Call Anthropic API."""
        import urllib.request
        import urllib.error

        api_key = self.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.warning("No Anthropic API key — skipping LLM review")
            return None

        body = json.dumps({
            "model": self.model,
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            logger.warning("Anthropic API error: %s", e.code)
            return None

        content = data["content"][0]["text"]
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.warning("LLM returned non-JSON: %s", content[:100])
            return None
